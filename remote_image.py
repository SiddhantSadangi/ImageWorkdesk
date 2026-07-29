"""Safely download and decode remote images.

Each URL hop is resolved and validated once. The connection is then made to
the exact validated socket address while the original hostname remains in the
HTTP Host header and, for HTTPS, in SNI and certificate verification.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from io import BytesIO
import http.client
import io
import ipaddress
import socket
import ssl
import time
from typing import Any, List, NamedTuple, Optional, Tuple
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
import warnings

from PIL import Image, UnidentifiedImageError


CONNECT_TIMEOUT_SECONDS = 3.0
DNS_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 5.0
TOTAL_TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 3
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_DIMENSION = 10_000
MAX_URL_LENGTH = 2_048
READ_CHUNK_BYTES = 64 * 1024

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MIME_TO_FORMAT = {
    "image/bmp": "BMP",
    "image/gif": "GIF",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/pjpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/x-ms-bmp": "BMP",
}
_ALLOWED_FORMATS = frozenset(_MIME_TO_FORMAT.values())
_SNI_CONTEXT = ssl.create_default_context()

_BLOCKED_PLATFORM_ADDRESSES = frozenset(
    {ipaddress.ip_address("168.63.129.16")}
)
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="image-dns")

class ImageFetchError(Exception):
    """Base class for controlled remote-image failures."""


class InvalidImageURL(ImageFetchError):
    """The supplied URL is malformed or uses a disallowed URL form."""


class UnsafeImageURL(ImageFetchError):
    """The supplied URL targets a network location that is not permitted."""


class ImageDownloadError(ImageFetchError):
    """The remote server could not provide a usable response."""


class ImageTooLarge(ImageFetchError):
    """The response body or decoded image exceeds an application limit."""


class InvalidImageData(ImageFetchError):
    """The response is not a supported, valid raster image."""


class _Target(NamedTuple):
    scheme: str
    hostname: str
    port: int
    host_header: str
    request_target: str
    normalized_url: str


class _Endpoint(NamedTuple):
    family: int
    socket_type: int
    protocol: int
    socket_address: Tuple[Any, ...]
    address_text: str


def _parse_target(url: str) -> _Target:
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise InvalidImageURL("Invalid URL.")
    if url != url.strip():
        raise InvalidImageURL("Invalid URL.")
    if "\\" in url or any(
        ord(character) < 32 or ord(character) == 127 for character in url
    ):
        raise InvalidImageURL("Invalid URL.")

    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise InvalidImageURL("Invalid URL.") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise InvalidImageURL("Only absolute HTTP(S) URLs are allowed.")
    if parsed.fragment:
        raise InvalidImageURL("URL fragments are not allowed.")
    if "@" in parsed.netloc:
        raise InvalidImageURL("Credentials are not allowed in image URLs.")

    try:
        raw_hostname = parsed.hostname
        explicit_port = parsed.port
    except ValueError as exc:
        raise InvalidImageURL("Invalid host or port.") from exc
    if not raw_hostname or "%" in raw_hostname:
        raise InvalidImageURL("Invalid host.")

    try:
        numeric_host = ipaddress.ip_address(raw_hostname)
    except ValueError:
        if ":" in raw_hostname:
            raise InvalidImageURL("Invalid host.")
        dns_hostname = (
            raw_hostname[:-1] if raw_hostname.endswith(".") else raw_hostname
        )
        if dns_hostname.endswith("."):
            raise InvalidImageURL("Invalid host.")
        try:
            hostname = dns_hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise InvalidImageURL("Invalid host.") from exc
        labels = hostname.split(".")
        if (
            not hostname
            or len(hostname) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or any(
                    character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                    for character in label
                )
                for label in labels
            )
        ):
            raise InvalidImageURL("Invalid host.")
    else:
        hostname = str(numeric_host)

    default_port = 443 if scheme == "https" else 80
    if explicit_port is not None and explicit_port != default_port:
        raise UnsafeImageURL("Only the default HTTP(S) port is allowed.")
    if parsed.netloc.endswith(":"):
        raise InvalidImageURL("Invalid port.")

    host_header = "[{}]".format(hostname) if ":" in hostname else hostname
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&?/:;+,%@!$'()*-._~")
    request_target = path + ("?{}".format(query) if query else "")
    normalized_url = urlunsplit((scheme, host_header, path, query, ""))
    if len(normalized_url) > MAX_URL_LENGTH:
        raise InvalidImageURL("Invalid URL.")

    return _Target(
        scheme=scheme,
        hostname=hostname,
        port=default_port,
        host_header=host_header,
        request_target=request_target,
        normalized_url=normalized_url,
    )


def _is_public_address(address: Any) -> bool:
    if address in _BLOCKED_PLATFORM_ADDRESSES:
        return False
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or getattr(address, "is_site_local", False)
    ):
        return False

    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None and not _is_public_address(
            address.ipv4_mapped
        ):
            return False
        if address.sixtofour is not None and not _is_public_address(address.sixtofour):
            return False
        if address.teredo is not None:
            server, client = address.teredo
            if not _is_public_address(server) or not _is_public_address(client):
                return False
    return True


def _resolve_public_endpoints(
    target: _Target, deadline: Optional[float] = None
) -> Tuple[_Endpoint, ...]:
    if deadline is None:
        deadline = time.monotonic() + DNS_TIMEOUT_SECONDS
    future = None
    try:
        future = _DNS_EXECUTOR.submit(
            socket.getaddrinfo,
            target.hostname,
            target.port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
        answers = future.result(
            timeout=min(DNS_TIMEOUT_SECONDS, _remaining_time(deadline))
        )
    except FutureTimeout as exc:
        if future is not None:
            future.cancel()
        raise ImageDownloadError("The image host lookup timed out.") from exc
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise ImageDownloadError("The image host could not be resolved.") from exc
    _remaining_time(deadline)

    endpoints: List[_Endpoint] = []
    seen = set()
    for family, socket_type, protocol, _canonical_name, socket_address in answers:
        if (
            family not in {socket.AF_INET, socket.AF_INET6}
            or socket_type != socket.SOCK_STREAM
            or protocol not in {0, socket.IPPROTO_TCP}
            or not isinstance(socket_address, tuple)
            or len(socket_address) < 2
            or socket_address[1] != target.port
        ):
            raise ImageDownloadError("The image host returned an invalid address.")
        address_text = socket_address[0]
        if not isinstance(address_text, str) or "%" in address_text:
            raise UnsafeImageURL("Scoped network addresses are not allowed.")
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise ImageDownloadError(
                "The image host returned an invalid address."
            ) from exc
        expected_family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        if family != expected_family:
            raise ImageDownloadError("The image host returned an invalid address.")
        if family == socket.AF_INET6:
            if len(socket_address) < 4:
                raise ImageDownloadError("The image host returned an invalid address.")
            if socket_address[3] != 0:
                raise UnsafeImageURL("Scoped network addresses are not allowed.")
        if not _is_public_address(address):
            raise UnsafeImageURL("Private or non-routable addresses are not allowed.")

        normalized_address = str(address)
        key = (family, socket_type, protocol, socket_address)
        if key not in seen:
            seen.add(key)
            endpoints.append(
                _Endpoint(
                    family=family,
                    socket_type=socket_type,
                    protocol=protocol,
                    socket_address=socket_address,
                    address_text=normalized_address,
                )
            )

    if not endpoints:
        raise ImageDownloadError("The image host did not return an address.")
    return tuple(endpoints)


def _remaining_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ImageDownloadError("The image request timed out.")
    return remaining


def _set_socket_timeout(
    connected_socket: socket.socket, deadline: float, cap: float
) -> None:
    connected_socket.settimeout(min(cap, _remaining_time(deadline)))


class _DeadlineReader(io.RawIOBase):
    def __init__(self, owner: "_DeadlineSocket") -> None:
        super().__init__()
        self._owner = owner

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed response stream.")
        return self._owner._recv_into(buffer)

    def fileno(self) -> int:
        return self._owner.fileno()

    def close(self) -> None:
        if not self.closed:
            try:
                super().close()
            finally:
                self._owner._release_reader()


class _DeadlineSocket:
    """Socket facade that reapplies the total deadline to every raw read."""

    def __init__(self, connected_socket: socket.socket, deadline: float) -> None:
        self._socket = connected_socket
        self._deadline = deadline
        self._reader_count = 0
        self._close_requested = False
        self._closed = False

    def _recv_into(self, buffer: Any) -> int:
        _set_socket_timeout(self._socket, self._deadline, READ_TIMEOUT_SECONDS)
        received = self._socket.recv_into(buffer)
        _remaining_time(self._deadline)
        if not isinstance(received, int) or received < 0:
            raise OSError("Invalid socket read result.")
        return received

    def _release_reader(self) -> None:
        if self._reader_count > 0:
            self._reader_count -= 1
        if self._close_requested and self._reader_count == 0:
            self._close_underlying()

    def _close_underlying(self) -> None:
        if not self._closed:
            self._closed = True
            self._socket.close()

    def sendall(self, data: Any, *args: Any, **kwargs: Any) -> None:
        _set_socket_timeout(self._socket, self._deadline, READ_TIMEOUT_SECONDS)
        self._socket.sendall(data, *args, **kwargs)
        _remaining_time(self._deadline)

    def makefile(
        self,
        mode: str = "r",
        buffering: int = -1,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if mode not in {"r", "rb"} or args or kwargs:
            raise ValueError("Unsupported response stream mode.")
        if self._close_requested:
            raise OSError("The response socket is closed.")

        self._reader_count += 1
        raw_reader = _DeadlineReader(self)
        if buffering == 0:
            return raw_reader
        if buffering in {-1, None}:
            buffering = io.DEFAULT_BUFFER_SIZE
        if not isinstance(buffering, int) or buffering <= 0:
            raw_reader.close()
            raise ValueError("Invalid response buffer size.")
        try:
            return io.BufferedReader(raw_reader, buffer_size=buffering)
        except Exception:
            raw_reader.close()
            raise

    def settimeout(self, timeout: float) -> None:
        self._socket.settimeout(timeout)

    def gettimeout(self) -> Optional[float]:
        return self._socket.gettimeout()

    def fileno(self) -> int:
        return self._socket.fileno()

    def close(self) -> None:
        self._close_requested = True
        if self._reader_count == 0:
            self._close_underlying()


def _connect_endpoint(
    target: _Target, endpoint: _Endpoint, deadline: float
) -> socket.socket:
    connected_socket = socket.socket(
        endpoint.family,
        endpoint.socket_type,
        endpoint.protocol,
    )
    try:
        _set_socket_timeout(connected_socket, deadline, CONNECT_TIMEOUT_SECONDS)
        connected_socket.connect(endpoint.socket_address)

        peer = connected_socket.getpeername()
        if not isinstance(peer, tuple) or len(peer) < 2 or peer[1] != target.port:
            raise UnsafeImageURL("The connected peer address is invalid.")
        peer_text = peer[0]
        if not isinstance(peer_text, str) or "%" in peer_text:
            raise UnsafeImageURL("The connected peer address is invalid.")
        try:
            peer_address = ipaddress.ip_address(peer_text)
        except ValueError as exc:
            raise UnsafeImageURL("The connected peer address is invalid.") from exc
        expected_family = (
            socket.AF_INET if peer_address.version == 4 else socket.AF_INET6
        )
        if (
            expected_family != endpoint.family
            or str(peer_address) != endpoint.address_text
        ):
            raise UnsafeImageURL(
                "The connected peer did not match the validated address."
            )

        if target.scheme == "https":
            _set_socket_timeout(connected_socket, deadline, CONNECT_TIMEOUT_SECONDS)
            connected_socket = _SNI_CONTEXT.wrap_socket(
                connected_socket,
                server_hostname=target.hostname,
            )
        return connected_socket
    except Exception:
        connected_socket.close()
        raise


def _open_connected_socket(
    target: _Target, endpoints: Tuple[_Endpoint, ...], deadline: float
) -> socket.socket:
    last_error: Optional[BaseException] = None
    for endpoint in endpoints:
        try:
            return _connect_endpoint(target, endpoint, deadline)
        except UnsafeImageURL:
            raise
        except (OSError, ssl.SSLError) as exc:
            last_error = exc
    raise ImageDownloadError("The image host could not be reached.") from last_error


def _single_header(headers: Any, name: str) -> Optional[str]:
    values = headers.get_all(name, [])
    if not values:
        return None
    if len(values) != 1 or not isinstance(values[0], str):
        raise ImageDownloadError("The image response contains invalid headers.")
    return values[0]


def _read_response_body(
    response: http.client.HTTPResponse,
    connected_socket: socket.socket,
    deadline: float,
) -> Tuple[bytes, str]:
    content_type = _single_header(response.headers, "Content-Type") or ""
    mime_type = content_type.partition(";")[0].strip().lower()
    allowed_mime_types = set(_MIME_TO_FORMAT).union({"", "application/octet-stream"})
    if mime_type not in allowed_mime_types:
        raise InvalidImageData("The response is not a supported image type.")

    content_encoding = (
        _single_header(response.headers, "Content-Encoding") or ""
    ).strip().lower()
    if content_encoding not in {"", "identity"}:
        raise InvalidImageData("Encoded response bodies are not allowed.")

    transfer_encoding = (
        _single_header(response.headers, "Transfer-Encoding") or ""
    ).strip().lower()
    if transfer_encoding not in {"", "chunked"}:
        raise ImageDownloadError("The response uses an unsupported transfer encoding.")

    declared_length = None
    content_length = _single_header(response.headers, "Content-Length")
    if transfer_encoding and content_length is not None:
        raise ImageDownloadError("The response has conflicting length headers.")
    if content_length is not None:
        normalized_length = content_length.strip()
        if not normalized_length.isascii() or not normalized_length.isdigit():
            raise ImageDownloadError("The response has an invalid length.")
        declared_length = int(normalized_length)
        if declared_length > MAX_DOWNLOAD_BYTES:
            raise ImageTooLarge("The image download is too large.")

    body = bytearray()
    while True:
        _set_socket_timeout(connected_socket, deadline, READ_TIMEOUT_SECONDS)
        chunk = response.read1(READ_CHUNK_BYTES)
        _remaining_time(deadline)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise ImageDownloadError("The response body is invalid.")
        if len(body) + len(chunk) > MAX_DOWNLOAD_BYTES:
            raise ImageTooLarge("The image download is too large.")
        try:
            body.extend(chunk)
        except MemoryError as exc:
            raise ImageTooLarge("The image download is too large.") from exc

    if declared_length is not None and len(body) != declared_length:
        raise ImageDownloadError("The response body is incomplete.")
    try:
        return bytes(body), mime_type
    except MemoryError as exc:
        raise ImageTooLarge("The image download is too large.") from exc


def _download_once(
    target: _Target, deadline: float
) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    endpoints = _resolve_public_endpoints(target, deadline)
    _remaining_time(deadline)
    connected_socket = _open_connected_socket(target, endpoints, deadline)
    deadline_socket = _DeadlineSocket(connected_socket, deadline)
    connection = None
    response = None
    try:
        connection = http.client.HTTPConnection(
            target.hostname,
            target.port,
            timeout=min(READ_TIMEOUT_SECONDS, _remaining_time(deadline)),
        )
        connection.sock = deadline_socket
        _set_socket_timeout(deadline_socket, deadline, READ_TIMEOUT_SECONDS)
        connection.request(
            "GET",
            target.request_target,
            headers={
                "Accept": "image/png, image/jpeg, image/webp, image/gif, image/bmp",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Host": target.host_header,
                "User-Agent": "ImageWorkdesk",
            },
        )
        _set_socket_timeout(deadline_socket, deadline, READ_TIMEOUT_SECONDS)
        response = connection.getresponse()
        _remaining_time(deadline)

        if response.status in _REDIRECT_STATUSES:
            location = _single_header(response.headers, "Location")
            if not location:
                raise ImageDownloadError("The redirect response has no destination.")
            if (
                len(location) > MAX_URL_LENGTH
                or location != location.strip()
                or "\\" in location
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in location
                )
            ):
                raise InvalidImageURL("Invalid redirect URL.")
            return None, location, None
        if response.status != 200:
            raise ImageDownloadError(
                "The image server returned an unsuccessful response."
            )

        body, mime_type = _read_response_body(response, deadline_socket, deadline)
        return body, None, mime_type
    except ImageFetchError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
        raise ImageDownloadError("The image request failed.") from exc
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            if connection is not None:
                connection.close()
            else:
                deadline_socket.close()


def _validate_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if (
        width <= 0
        or height <= 0
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ImageTooLarge("The decoded image is too large.")


def _decode_image(body: bytes, mime_type: str) -> Image.Image:
    expected_format = _MIME_TO_FORMAT.get(mime_type)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(body)) as candidate:
                _validate_dimensions(candidate)
                if candidate.format not in _ALLOWED_FORMATS:
                    raise InvalidImageData("The image format is not supported.")
                if expected_format is not None and candidate.format != expected_format:
                    raise InvalidImageData(
                        "The image type does not match its contents."
                    )
                candidate.verify()

            with Image.open(BytesIO(body)) as candidate:
                _validate_dimensions(candidate)
                if candidate.format not in _ALLOWED_FORMATS:
                    raise InvalidImageData("The image format is not supported.")
                if expected_format is not None and candidate.format != expected_format:
                    raise InvalidImageData(
                        "The image type does not match its contents."
                    )
                candidate.load()
                converted = candidate.convert("RGB")
                converted.load()
                result = converted.copy()
                result.info.clear()
                return result
    except ImageFetchError:
        raise
    except MemoryError as exc:
        raise ImageTooLarge("The decoded image is too large.") from exc
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ImageTooLarge("The decoded image is too large.") from exc
    except (
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        raise InvalidImageData("The response is not a valid image.") from exc


def fetch_image_from_url(url: str) -> Image.Image:
    """Fetch an HTTP(S) raster image after enforcing SSRF and resource limits."""

    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
    current_url = url

    for redirect_count in range(MAX_REDIRECTS + 1):
        target = _parse_target(current_url)
        body, redirect_location, mime_type = _download_once(target, deadline)
        if redirect_location is None:
            if body is None or mime_type is None:
                raise ImageDownloadError("The image response is incomplete.")
            return _decode_image(body, mime_type)

        if redirect_count >= MAX_REDIRECTS:
            raise ImageDownloadError("The image URL redirected too many times.")
        try:
            next_url = urljoin(target.normalized_url, redirect_location)
        except (UnicodeError, ValueError) as exc:
            raise InvalidImageURL("Invalid redirect URL.") from exc
        next_target = _parse_target(next_url)
        if target.scheme == "https" and next_target.scheme != "https":
            raise UnsafeImageURL("HTTPS redirects may not downgrade to HTTP.")
        current_url = next_target.normalized_url

    raise ImageDownloadError("The image URL redirected too many times.")
