from contextlib import contextmanager
from io import BytesIO
import ipaddress
from pathlib import Path
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import remote_image


PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


def address_answer(address, port=80):
    parsed = ipaddress.ip_address(address)
    if parsed.version == 4:
        return (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, port),
        )
    return (
        socket.AF_INET6,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        "",
        (address, port, 0, 0),
    )


class FakeHeaders:
    def __init__(self, values=None):
        self._values = {}
        for name, value in (values or {}).items():
            self._values[name.lower()] = value if isinstance(value, list) else [value]

    def get_all(self, name, failobj=None):
        return self._values.get(name.lower(), failobj)


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        self.headers = FakeHeaders(headers)
        self._chunks = list(chunks or [])
        self.read_calls = 0
        self.closed = False

    def read1(self, _amount):
        self.read_calls += 1
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def close(self):
        self.closed = True


class FakeSocket:
    def __init__(self, peer=(PUBLIC_V4, 80)):
        self.peer = peer
        self.timeouts = []
        self.connected_to = None
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def connect(self, socket_address):
        self.connected_to = socket_address

    def getpeername(self):
        return self.peer

    def close(self):
        self.closed = True


class SlowHeaderSocket:
    def __init__(self, payload, clock):
        self._payload = bytearray(payload)
        self._clock = clock
        self.timeouts = []
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def gettimeout(self):
        return self.timeouts[-1] if self.timeouts else None

    def sendall(self, _data, *_args, **_kwargs):
        return None

    def recv_into(self, buffer):
        if not self._payload:
            return 0
        self._clock["value"] += 0.04
        buffer[0] = self._payload.pop(0)
        return 1

    def fileno(self):
        return -1

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, host, port, timeout, response):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.response = response
        self.sock = None
        self.requests = []
        self.closed = False

    def request(self, method, target, headers=None):
        self.requests.append((method, target, dict(headers or {})))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True
        if self.sock is not None:
            self.sock.close()


def queued_resolver(*answer_sets):
    queued = list(answer_sets)

    def resolve(_hostname, port, *_args, **_kwargs):
        if not queued:
            raise AssertionError("Unexpected DNS lookup")
        return [address_answer(address, port) for address in queued.pop(0)]

    return Mock(side_effect=resolve)


@contextmanager
def scripted_transport(responses, resolver):
    response_queue = list(responses)
    connections = []
    sockets = []

    def open_socket(_target, _endpoints, _deadline):
        fake_socket = FakeSocket()
        sockets.append(fake_socket)
        return fake_socket

    def make_connection(host, port, timeout):
        if not response_queue:
            raise AssertionError("Unexpected HTTP connection")
        connection = FakeConnection(host, port, timeout, response_queue.pop(0))
        connections.append(connection)
        return connection

    with (
        patch("remote_image.socket.getaddrinfo", resolver),
        patch(
            "remote_image._open_connected_socket",
            side_effect=open_socket,
        ) as open_mock,
        patch(
            "remote_image.http.client.HTTPConnection",
            side_effect=make_connection,
        ) as connection_mock,
    ):
        yield {
            "connections": connections,
            "connection_mock": connection_mock,
            "open_mock": open_mock,
            "sockets": sockets,
        }


class URLPolicyTests(unittest.TestCase):
    def test_accepts_http_https_default_ports_and_normalizes_host(self):
        http_target = remote_image._parse_target("HTTP://Example.COM:80/a?q=1")
        https_target = remote_image._parse_target("https://example.com:443/image.png")
        ipv6_target = remote_image._parse_target(
            "https://[2606:4700:4700::1111]:443/image.png"
        )

        self.assertEqual(http_target.hostname, "example.com")
        self.assertEqual(http_target.port, 80)
        self.assertEqual(http_target.request_target, "/a?q=1")
        self.assertEqual(https_target.port, 443)
        self.assertEqual(ipv6_target.host_header, "[2606:4700:4700::1111]")

    def test_rejects_disallowed_url_forms(self):
        invalid_urls = [
            "",
            "/relative.png",
            "//example.com/image.png",
            "file:///etc/passwd",
            "ftp://example.com/image.png",
            "gopher://example.com/",
            "data:image/png;base64,AAAA",
            "http://user@example.com/image.png",
            "http://user:pass@example.com/image.png",
            "http://@example.com/image.png",
            "http://example.com:/image.png",
            "http://example.com/image.png#fragment",
            " http://example.com/image.png",
            "http://example.com/image.png\nInjected: yes",
            "http://example.com\\@127.0.0.1/image.png",
        ]

        for url in invalid_urls:
            with (
                self.subTest(url=url),
                self.assertRaises(remote_image.InvalidImageURL),
            ):
                remote_image._parse_target(url)

    def test_rejects_nondefault_and_scheme_mismatched_ports(self):
        for url in (
            "http://example.com:443/image.png",
            "http://example.com:8080/image.png",
            "https://example.com:80/image.png",
            "https://example.com:8443/image.png",
        ):
            with self.subTest(url=url), self.assertRaises(remote_image.UnsafeImageURL):
                remote_image._parse_target(url)


class AddressPolicyTests(unittest.TestCase):
    def target(self):
        return remote_image._parse_target("http://public.test/image.png")

    def test_accepts_global_ipv4_and_ipv6(self):
        answers = [address_answer(PUBLIC_V4), address_answer(PUBLIC_V6)]
        with patch("remote_image.socket.getaddrinfo", return_value=answers):
            endpoints = remote_image._resolve_public_endpoints(self.target())

        self.assertEqual(
            [endpoint.address_text for endpoint in endpoints],
            [PUBLIC_V4, PUBLIC_V6],
        )

    def test_rejects_non_global_addresses(self):
        blocked = [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.0.1",
            "169.254.169.254",
            "168.63.129.16",
            "0.0.0.0",
            "100.64.0.1",
            "224.0.0.1",
            "240.0.0.1",
            "::1",
            "::",
            "fe80::1",
            "fd00::1",
            "ff02::1",
            "::ffff:127.0.0.1",
            "::ffff:168.63.129.16",
        ]

        for address in blocked:
            with self.subTest(address=address), patch(
                "remote_image.socket.getaddrinfo",
                return_value=[address_answer(address)],
            ), self.assertRaises(remote_image.UnsafeImageURL):
                remote_image._resolve_public_endpoints(self.target())

    def test_rejects_mixed_public_and_private_dns_answers(self):
        answers = [address_answer(PUBLIC_V4), address_answer("127.0.0.1")]
        with (
            patch("remote_image.socket.getaddrinfo", return_value=answers),
            self.assertRaises(remote_image.UnsafeImageURL),
        ):
            remote_image._resolve_public_endpoints(self.target())

    def test_normalizes_dns_failure_and_empty_answers(self):
        with patch(
            "remote_image.socket.getaddrinfo",
            side_effect=socket.gaierror("not found"),
        ), self.assertRaises(remote_image.ImageDownloadError):
            remote_image._resolve_public_endpoints(self.target())

        with (
            patch("remote_image.socket.getaddrinfo", return_value=[]),
            self.assertRaises(remote_image.ImageDownloadError),
        ):
            remote_image._resolve_public_endpoints(self.target())

    def test_dns_lookup_timeout_is_controlled_and_cancelled(self):
        future = Mock()
        future.result.side_effect = remote_image.FutureTimeout()
        executor = Mock()
        executor.submit.return_value = future

        with patch("remote_image._DNS_EXECUTOR", executor), patch(
            "remote_image.time.monotonic", return_value=0
        ), self.assertRaises(remote_image.ImageDownloadError):
            remote_image._resolve_public_endpoints(self.target())

        future.result.assert_called_once_with(timeout=remote_image.DNS_TIMEOUT_SECONDS)
        future.cancel.assert_called_once_with()

    def test_rejects_resolver_sockaddr_with_the_wrong_port(self):
        answer = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (PUBLIC_V4, 81),
        )
        with patch(
            "remote_image.socket.getaddrinfo", return_value=[answer]
        ), self.assertRaises(remote_image.ImageDownloadError):
            remote_image._resolve_public_endpoints(self.target())


class ConnectionPinningTests(unittest.TestCase):
    def endpoint(self, address=PUBLIC_V4, port=80):
        return remote_image._Endpoint(
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            (address, port),
            address,
        )

    def test_connects_to_exact_validated_sockaddr(self):
        target = remote_image._parse_target("http://public.test/image.png")
        fake_socket = FakeSocket(peer=(PUBLIC_V4, 80))
        with patch("remote_image.socket.socket", return_value=fake_socket), patch(
            "remote_image.time.monotonic", return_value=0
        ):
            result = remote_image._connect_endpoint(target, self.endpoint(), 10)

        self.assertIs(result, fake_socket)
        self.assertEqual(fake_socket.connected_to, (PUBLIC_V4, 80))
        self.assertLessEqual(
            max(fake_socket.timeouts), remote_image.CONNECT_TIMEOUT_SECONDS
        )

    def test_https_preserves_original_hostname_for_sni(self):
        target = remote_image._parse_target("https://images.example/image.png")
        endpoint = self.endpoint(port=443)
        fake_socket = FakeSocket(peer=(PUBLIC_V4, 443))
        tls_context = Mock()
        tls_context.wrap_socket.return_value = fake_socket

        with patch("remote_image.socket.socket", return_value=fake_socket), patch(
            "remote_image._SNI_CONTEXT", tls_context
        ), patch("remote_image.time.monotonic", return_value=0):
            remote_image._connect_endpoint(target, endpoint, 10)

        tls_context.wrap_socket.assert_called_once_with(
            fake_socket,
            server_hostname="images.example",
        )

    def test_default_tls_context_verifies_certificates_and_hostnames(self):
        self.assertEqual(remote_image._SNI_CONTEXT.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(remote_image._SNI_CONTEXT.check_hostname)

    def test_certificate_failure_closes_the_connected_socket(self):
        target = remote_image._parse_target("https://images.example/image.png")
        endpoint = self.endpoint(port=443)
        fake_socket = FakeSocket(peer=(PUBLIC_V4, 443))
        tls_context = Mock()
        tls_context.wrap_socket.side_effect = ssl.SSLCertVerificationError(
            1,
            "certificate verify failed",
        )

        with patch("remote_image.socket.socket", return_value=fake_socket), patch(
            "remote_image._SNI_CONTEXT", tls_context
        ), patch("remote_image.time.monotonic", return_value=0), self.assertRaises(
            ssl.SSLCertVerificationError
        ):
            remote_image._connect_endpoint(target, endpoint, 10)

        self.assertTrue(fake_socket.closed)

    def test_rejects_peer_port_mismatch_and_closes_socket(self):
        target = remote_image._parse_target("http://public.test/image.png")
        fake_socket = FakeSocket(peer=(PUBLIC_V4, 81))
        with patch("remote_image.socket.socket", return_value=fake_socket), patch(
            "remote_image.time.monotonic", return_value=0
        ), self.assertRaises(remote_image.UnsafeImageURL):
            remote_image._connect_endpoint(target, self.endpoint(), 10)

        self.assertTrue(fake_socket.closed)

    def test_rejects_peer_mismatch_and_closes_socket(self):
        target = remote_image._parse_target("http://public.test/image.png")
        fake_socket = FakeSocket(peer=("8.8.8.8", 80))
        with patch("remote_image.socket.socket", return_value=fake_socket), patch(
            "remote_image.time.monotonic", return_value=0
        ), self.assertRaises(remote_image.UnsafeImageURL):
            remote_image._connect_endpoint(target, self.endpoint(), 10)

        self.assertTrue(fake_socket.closed)


class DeadlineTransportTests(unittest.TestCase):
    def test_real_http_parser_cannot_slow_drip_headers_past_total_deadline(self):
        clock = {"value": 0.0}
        raw_socket = SlowHeaderSocket(
            b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
            clock,
        )
        deadline_socket = remote_image._DeadlineSocket(raw_socket, 0.2)
        connection = remote_image.http.client.HTTPConnection("public.test", 80)
        connection.sock = deadline_socket

        try:
            with patch(
                "remote_image.time.monotonic",
                side_effect=lambda: clock["value"],
            ):
                connection.request("GET", "/image.png")
                with self.assertRaises(remote_image.ImageDownloadError):
                    connection.getresponse()
        finally:
            connection.close()

        self.assertGreaterEqual(clock["value"], 0.2)
        self.assertTrue(raw_socket.timeouts)

    def test_connection_constructor_failure_closes_open_socket(self):
        target = remote_image._parse_target("http://public.test/image.png")
        endpoint = remote_image._Endpoint(
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            (PUBLIC_V4, 80),
            PUBLIC_V4,
        )
        fake_socket = FakeSocket()

        with patch(
            "remote_image._resolve_public_endpoints", return_value=(endpoint,)
        ), patch(
            "remote_image._open_connected_socket", return_value=fake_socket
        ), patch(
            "remote_image.http.client.HTTPConnection", side_effect=ValueError("bad")
        ), patch(
            "remote_image._remaining_time", return_value=1
        ), self.assertRaises(remote_image.ImageDownloadError):
            remote_image._download_once(target, 10)

        self.assertTrue(fake_socket.closed)


class FetchWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        output = BytesIO()
        Image.new("RGB", (2, 3), (10, 20, 30)).save(output, format="PNG")
        cls.png = output.getvalue()

    def image_response(self, content_type="image/png"):
        return FakeResponse(
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(self.png)),
            },
            chunks=[self.png],
        )

    def test_complete_offline_fetch_returns_detached_rgb_image(self):
        response = self.image_response()
        resolver = queued_resolver([PUBLIC_V4])
        with scripted_transport([response], resolver) as transport:
            image = remote_image.fetch_image_from_url("http://public.test/image.png")

        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (2, 3))
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(transport["open_mock"].call_count, 1)
        connection = transport["connections"][0]
        method, request_target, headers = connection.requests[0]
        self.assertEqual((method, request_target), ("GET", "/image.png"))
        self.assertEqual(headers["Host"], "public.test")
        self.assertEqual(headers["Accept-Encoding"], "identity")
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("Cookie", headers)
        self.assertTrue(response.closed)
        self.assertTrue(connection.closed)
        self.assertTrue(transport["sockets"][0].closed)

    def test_relative_redirect_is_revalidated_and_resolved_again(self):
        redirect = FakeResponse(status=302, headers={"Location": "/final.png"})
        success = self.image_response()
        resolver = queued_resolver([PUBLIC_V4], [PUBLIC_V4])

        with scripted_transport([redirect, success], resolver) as transport:
            image = remote_image.fetch_image_from_url("http://public.test/start.png")

        self.assertEqual(image.size, (2, 3))
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(transport["open_mock"].call_count, 2)
        self.assertEqual(redirect.read_calls, 0)
        self.assertEqual(
            transport["connections"][1].requests[0][1],
            "/final.png",
        )

    def test_redirect_to_private_address_is_blocked_before_second_request(self):
        redirect = FakeResponse(
            status=302,
            headers={"Location": "http://private.test/secret.png"},
        )
        resolver = queued_resolver([PUBLIC_V4], ["127.0.0.1"])

        with scripted_transport([redirect], resolver) as transport, self.assertRaises(
            remote_image.UnsafeImageURL
        ):
            remote_image.fetch_image_from_url("http://public.test/start.png")

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(transport["open_mock"].call_count, 1)
        self.assertEqual(transport["connection_mock"].call_count, 1)

    def test_same_host_dns_rebinding_is_blocked(self):
        redirect = FakeResponse(status=302, headers={"Location": "/again.png"})
        resolver = queued_resolver([PUBLIC_V4], ["169.254.169.254"])

        with scripted_transport([redirect], resolver) as transport, self.assertRaises(
            remote_image.UnsafeImageURL
        ):
            remote_image.fetch_image_from_url("http://public.test/start.png")

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(transport["open_mock"].call_count, 1)

    def test_mixed_dns_answer_prevents_any_connection(self):
        success = self.image_response()
        resolver = queued_resolver([PUBLIC_V4, "10.0.0.1"])

        with scripted_transport([success], resolver) as transport, self.assertRaises(
            remote_image.UnsafeImageURL
        ):
            remote_image.fetch_image_from_url("http://public.test/image.png")

        transport["open_mock"].assert_not_called()
        transport["connection_mock"].assert_not_called()

    def test_https_to_http_redirect_is_blocked(self):
        redirect = FakeResponse(
            status=302,
            headers={"Location": "http://public.test/final.png"},
        )
        resolver = queued_resolver([PUBLIC_V4])

        with scripted_transport([redirect], resolver) as transport, self.assertRaises(
            remote_image.UnsafeImageURL
        ):
            remote_image.fetch_image_from_url("https://public.test/start.png")

        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(transport["open_mock"].call_count, 1)

    def test_malformed_redirect_is_normalized_and_connection_is_closed(self):
        redirect = FakeResponse(
            status=302,
            headers={"Location": "http://[::1"},
        )
        resolver = queued_resolver([PUBLIC_V4])

        with scripted_transport([redirect], resolver), self.assertRaises(
            remote_image.InvalidImageURL
        ):
            remote_image.fetch_image_from_url("http://public.test/start.png")

        self.assertTrue(redirect.closed)

    def test_duplicate_redirect_location_is_rejected(self):
        redirect = FakeResponse(
            status=302,
            headers={"Location": ["/one.png", "/two.png"]},
        )
        resolver = queued_resolver([PUBLIC_V4])

        with scripted_transport([redirect], resolver), self.assertRaises(
            remote_image.ImageDownloadError
        ):
            remote_image.fetch_image_from_url("http://public.test/start.png")

        self.assertTrue(redirect.closed)

    def test_three_redirects_are_allowed_but_a_fourth_is_not_followed(self):
        redirects = [
            FakeResponse(status=302, headers={"Location": "/{}.png".format(index)})
            for index in range(4)
        ]
        resolver = queued_resolver(*([[PUBLIC_V4]] * 4))

        with scripted_transport(redirects, resolver) as transport, self.assertRaises(
            remote_image.ImageDownloadError
        ):
            remote_image.fetch_image_from_url("http://public.test/start.png")

        self.assertEqual(resolver.call_count, 4)
        self.assertEqual(transport["open_mock"].call_count, 4)

        allowed_responses = [
            FakeResponse(status=302, headers={"Location": "/{}.png".format(index)})
            for index in range(3)
        ] + [self.image_response()]
        allowed_resolver = queued_resolver(*([[PUBLIC_V4]] * 4))
        with scripted_transport(allowed_responses, allowed_resolver):
            image = remote_image.fetch_image_from_url("http://public.test/start.png")
        self.assertEqual(image.size, (2, 3))


class BodyAndImageLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        png_output = BytesIO()
        Image.new("RGB", (2, 2), "red").save(png_output, format="PNG")
        cls.png = png_output.getvalue()

        tiff_output = BytesIO()
        Image.new("RGB", (2, 2), "red").save(tiff_output, format="TIFF")
        cls.tiff = tiff_output.getvalue()

    def read_body(self, headers, chunks, deadline=10):
        response = FakeResponse(headers=headers, chunks=chunks)
        fake_socket = FakeSocket()
        with patch("remote_image.time.monotonic", return_value=0):
            result = remote_image._read_response_body(response, fake_socket, deadline)
        return result, response, fake_socket

    def test_mime_parameters_missing_mime_and_octet_stream_are_supported(self):
        for content_type in (
            "image/png; charset=binary", None, "application/octet-stream"
        ):
            headers = {"Content-Length": "3"}
            if content_type is not None:
                headers["Content-Type"] = content_type
            with self.subTest(content_type=content_type):
                (body, mime_type), _response, _socket = self.read_body(
                    headers,
                    [b"abc"],
                )
                self.assertEqual(body, b"abc")
                self.assertEqual(mime_type, (content_type or "").partition(";")[0])

    def test_disallowed_mime_and_encoding_are_rejected_before_body_read(self):
        cases = [
            ({"Content-Type": "text/html"}, remote_image.InvalidImageData),
            ({"Content-Type": "image/svg+xml"}, remote_image.InvalidImageData),
            (
                {"Content-Type": "image/png", "Content-Encoding": "gzip"},
                remote_image.InvalidImageData,
            ),
        ]
        for headers, exception in cases:
            response = FakeResponse(headers=headers, chunks=[b"not read"])
            with self.subTest(headers=headers), patch(
                "remote_image.time.monotonic", return_value=0
            ), self.assertRaises(exception):
                remote_image._read_response_body(response, FakeSocket(), 10)
            self.assertEqual(response.read_calls, 0)

    def test_content_length_and_streamed_byte_caps(self):
        with patch("remote_image.MAX_DOWNLOAD_BYTES", 5), patch(
            "remote_image.time.monotonic", return_value=0
        ):
            oversized_header = FakeResponse(
                headers={"Content-Type": "image/png", "Content-Length": "6"},
                chunks=[b"unused"],
            )
            with self.assertRaises(remote_image.ImageTooLarge):
                remote_image._read_response_body(oversized_header, FakeSocket(), 10)
            self.assertEqual(oversized_header.read_calls, 0)

            streamed = FakeResponse(
                headers={"Content-Type": "image/png"},
                chunks=[b"123", b"456"],
            )
            with self.assertRaises(remote_image.ImageTooLarge):
                remote_image._read_response_body(streamed, FakeSocket(), 10)

            exact = FakeResponse(
                headers={"Content-Type": "image/png", "Content-Length": "5"},
                chunks=[b"12345"],
            )
            body, _mime = remote_image._read_response_body(exact, FakeSocket(), 10)
            self.assertEqual(body, b"12345")

    def test_many_tiny_chunks_use_one_bounded_output_buffer(self):
        response = FakeResponse(
            headers={
                "Content-Type": "image/png",
                "Content-Length": "10000",
            },
            chunks=[b"x"] * 10_000,
        )
        with patch("remote_image.MAX_DOWNLOAD_BYTES", 10_000), patch(
            "remote_image.time.monotonic", return_value=0
        ):
            body, _mime = remote_image._read_response_body(response, FakeSocket(), 10)

        self.assertEqual(len(body), 10_000)

    def test_incomplete_or_conflicting_length_is_rejected(self):
        incomplete = FakeResponse(
            headers={"Content-Type": "image/png", "Content-Length": "5"},
            chunks=[b"123"],
        )
        conflicting = FakeResponse(
            headers={
                "Content-Type": "image/png",
                "Content-Length": "3",
                "Transfer-Encoding": "chunked",
            },
            chunks=[b"123"],
        )
        with patch("remote_image.time.monotonic", return_value=0):
            for response in (incomplete, conflicting):
                with self.subTest(response=response), self.assertRaises(
                    remote_image.ImageDownloadError
                ):
                    remote_image._read_response_body(response, FakeSocket(), 10)

    def test_duplicate_lengths_and_malformed_transfer_encoding_are_rejected(self):
        duplicate_length = FakeResponse(
            headers={
                "Content-Type": "image/png",
                "Content-Length": ["3", "3"],
            },
            chunks=[b"123"],
        )
        malformed_transfer = FakeResponse(
            headers={
                "Content-Type": "image/png",
                "Transfer-Encoding": "gzip",
            },
            chunks=[b"123"],
        )
        with patch("remote_image.time.monotonic", return_value=0):
            for response in (duplicate_length, malformed_transfer):
                with self.subTest(response=response), self.assertRaises(
                    remote_image.ImageDownloadError
                ):
                    remote_image._read_response_body(response, FakeSocket(), 10)

    def test_hard_deadline_stops_a_slow_drip_body(self):
        response = FakeResponse(
            headers={"Content-Type": "image/png"},
            chunks=[b"a", b"b", b""],
        )
        fake_socket = FakeSocket()
        with patch(
            "remote_image.time.monotonic",
            side_effect=[0, 4, 4, 9, 9, 11],
        ), self.assertRaises(remote_image.ImageDownloadError):
            remote_image._read_response_body(response, fake_socket, 10)

        self.assertTrue(fake_socket.timeouts)
        self.assertTrue(
            all(
                timeout <= remote_image.READ_TIMEOUT_SECONDS
                for timeout in fake_socket.timeouts
            )
        )

    def test_decodes_valid_png_and_rejects_mismatch_corruption_and_tiff(self):
        image = remote_image._decode_image(self.png, "image/png")
        self.assertEqual((image.mode, image.size), ("RGB", (2, 2)))
        self.assertEqual(image.info, {})
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))

        with self.assertRaises(remote_image.InvalidImageData):
            remote_image._decode_image(self.png, "image/jpeg")
        with self.assertRaises(remote_image.InvalidImageData):
            remote_image._decode_image(b"not an image", "")
        with self.assertRaises(remote_image.InvalidImageData):
            remote_image._decode_image(self.tiff, "application/octet-stream")

    def test_pillow_decompression_bombs_are_normalized_as_too_large(self):
        bomb = Image.DecompressionBombError("decompression bomb")
        with patch("remote_image.Image.open", side_effect=bomb), self.assertRaises(
            remote_image.ImageTooLarge
        ):
            remote_image._decode_image(self.png, "image/png")

    def test_pixel_and_dimension_limits_are_checked_before_full_decode(self):
        dimension_output = BytesIO()
        Image.new("RGB", (11, 1), "red").save(dimension_output, format="PNG")
        pixels_output = BytesIO()
        Image.new("RGB", (10, 10), "red").save(pixels_output, format="PNG")

        with patch("remote_image.MAX_IMAGE_DIMENSION", 10), self.assertRaises(
            remote_image.ImageTooLarge
        ):
            remote_image._decode_image(dimension_output.getvalue(), "image/png")
        with patch("remote_image.MAX_IMAGE_PIXELS", 99), self.assertRaises(
            remote_image.ImageTooLarge
        ):
            remote_image._decode_image(pixels_output.getvalue(), "image/png")


class AppWiringTests(unittest.TestCase):
    def test_streamlit_app_uses_only_the_controlled_fetch_boundary(self):
        repository = Path(__file__).resolve().parents[1]
        source = (repository / "app.py").read_text(encoding="utf-8")
        requirements = (repository / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn('VERSION = "1.0.3"', source)
        self.assertIn("fetch_image_from_url(url)", source)
        self.assertEqual(source.count("fetch_image_from_url(url)"), 1)
        self.assertIn("except ImageFetchError:", source)
        self.assertIn('key="load_remote_image"', source)
        self.assertIn('st.session_state["remote_image_value"]', source)
        self.assertNotIn("requests.get", source)
        self.assertNotIn("import requests", source)
        self.assertNotIn("from io import BytesIO", source)
        self.assertIn("streamlit>=1.53.0", requirements)


if __name__ == "__main__":
    unittest.main()
