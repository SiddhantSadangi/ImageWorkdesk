import contextlib
from io import BytesIO

import numpy as np
import requests
import streamlit as st
from PIL import Image, ImageEnhance, ImageOps
from rembg import remove
from st_social_media_links import SocialMediaIcons
from streamlit_cropper import st_cropper
from streamlit_image_comparison import image_comparison

VERSION = "1.0.0"


# ---------- UTILS ----------
def _reset(key: str) -> None:
    if key == "all":
        st.session_state["rotate_slider"] = 0
        st.session_state["brightness_slider"] = st.session_state[
            "saturation_slider"
        ] = st.session_state["contrast_slider"] = st.session_state[
            "sharpness_slider"
        ] = 100
        st.session_state["bg"] = st.session_state["crop"] = st.session_state[
            "mirror"
        ] = st.session_state["gray_bw"] = 0
    elif key == "rotate_slider":
        st.session_state["rotate_slider"] = 0
    elif key == "checkboxes":
        st.session_state["crop"] = st.session_state["mirror"] = st.session_state[
            "gray_bw"
        ] = 0
    else:
        st.session_state[key] = 100


def _randomize() -> None:
    st.session_state["mirror"] = np.random.choice([0, 1])
    st.session_state["rotate_slider"] = np.random.randint(0, 360)
    st.session_state["brightness_slider"] = np.random.randint(0, 1000)
    st.session_state["saturation_slider"] = np.random.randint(0, 1000)
    st.session_state["contrast_slider"] = np.random.randint(0, 1000)
    st.session_state["sharpness_slider"] = np.random.randint(0, 1000)


st.set_page_config(
    page_title="Image WorkDesk",
    page_icon="🖼️",
    menu_items={
        "About": f"Image WorkDesk v{VERSION}  "
        f"\nApp contact: [Siddhant Sadangi](mailto:siddhant.sadangi@gmail.com)",
        "Report a Bug": "https://github.com/SiddhantSadangi/ImageWorkdesk/issues/new",
        "Get help": None,
    },
    layout="wide",
)

# ---------- HEADER ----------
st.title("🖼️ Welcome to Image WorkDesk!")

# ---------- SIDEBAR ----------
with st.sidebar:
    with st.expander("✅ Supported operations"):
        st.info(
            "* Upload image, take one with your camera, or load from a URL\n"
            "* Crop\n"
            "* Remove background\n"
            "* Mirror\n"
            "* Convert to grayscale or black and white\n"
            "* Rotate\n"
            "* Change brightness, saturation, contrast, sharpness\n"
            "* Generate random image from supplied image\n"
            "* Compare original and final images\n"
            "* Download results"
        )

    with open("sidebar.html", "r", encoding="UTF-8") as sidebar_file:
        sidebar_html = sidebar_file.read().replace("{VERSION}", VERSION)

    st.components.v1.html(sidebar_html, height=225)

    st.html(
        """
        <div style="text-align:center; font-size:14px; color:lightgrey">
            <hr>
            Share the ❤️ on social media
        </div>"""
    )

    social_media_links = [
        "https://www.facebook.com/sharer/sharer.php?kid_directed_site=0&sdk=joey&u=https%3A%2F%2Fimageworkdesk.streamlit.app%2F&display=popup&ref=plugin&src=share_button",
        "https://www.linkedin.com/sharing/share-offsite/?url=https%3A%2F%2Fimageworkdesk.streamlit.app%2F",
        "https://x.com/intent/tweet?original_referer=http%3A%2F%2Flimageworkdesk.streamlit.app&ref_src=twsrc%5Etfw%7Ctwcamp%5Ebuttonembed%7Ctwterm%5Eshare%7Ctwgr%5E&text=Check%20out%20this%20cool%20Streamlit%20app%20%F0%9F%8E%88&url=https%3A%2F%2Fimageworkdesk.streamlit.app%2F",
    ]

    social_media_icons = SocialMediaIcons(
        social_media_links, colors=["lightgray"] * len(social_media_links)
    )

    social_media_icons.render(sidebar=True)

    st.html(
        """
        <div style="text-align:center; font-size:12px; color:lightgrey">
            <hr>
            <a rel="license" href="https://creativecommons.org/licenses/by-nc-sa/4.0/">
                <img alt="Creative Commons License" style="border-width:0"
                    src="https://i.creativecommons.org/l/by-nc-sa/4.0/88x31.png" />
            </a><br><br>
            This work is licensed under a <b>Creative Commons
                Attribution-NonCommercial-ShareAlike 4.0 International License</b>.<br>
            You can modify and build upon this work non-commercially. All derivatives should be
            credited to Siddhant Sadangi and
            be licenced under the same terms.
        </div>
    """
    )

# ---------- OPERATIONS ----------

option = st.radio(
    label="Upload an image, take one with your camera, or load image from a URL",
    options=(
        "Upload an image ⬆️",
        "Take a photo with my camera 📷",
        "Load image from a URL 🌐",
    ),
    help="Uploaded images are deleted from the server when you\n* upload another image, or\n* clear the file uploader, or\n* close the browser tab",
)

if option == "Take a photo with my camera 📷":
    upload_img = st.camera_input(
        label="Take a picture",
    )
    mode = "camera"

elif option == "Upload an image ⬆️":
    upload_img = st.file_uploader(
        label="Upload an image",
        type=["bmp", "jpg", "jpeg", "png", "svg"],
    )
    mode = "upload"

elif option == "Load image from a URL 🌐":
    url = st.text_input(
        "Image URL",
        key="url",
    )
    mode = "url"

    if url != "":
        try:
            response = requests.get(url)
            upload_img = Image.open(BytesIO(response.content))
        except:
            st.error("The URL does not seem to be valid.")

with contextlib.suppress(NameError):
    if upload_img is not None:
        pil_img = (
            upload_img.convert("RGB")
            if mode == "url"
            else Image.open(upload_img).convert("RGB")
        )
        img_arr = np.asarray(pil_img)

        # ---------- PROPERTIES ----------
        st.image(img_arr, use_column_width="auto", caption="Uploaded Image")
        st.text(
            f"Original width = {pil_img.size[0]}px and height = {pil_img.size[1]}px"
        )

        st.caption("All changes are applied on top of the previous change.")

        # ---------- CROP ----------
        st.text("Crop image ✂️")
        cropped_img = st_cropper(Image.fromarray(img_arr), should_resize_image=True)
        st.text(
            f"Cropped width = {cropped_img.size[0]}px and height = {cropped_img.size[1]}px"
        )

        with st.container():
            lcol, rcol = st.columns(2)
            if lcol.checkbox(
                label="Use cropped Image?",
                help="Select to use the cropped image in further operations",
                key="crop",
            ):
                image = cropped_img
            else:
                image = Image.fromarray(img_arr)

            # ---------- REMOVE BACKGROUND ----------
            if lcol.checkbox(
                label="Remove background?",
                help="Select to remove background from the image",
                key="bg",
            ):
                image = remove(image)

            # ---------- MIRROR ----------
            if lcol.checkbox(
                label="Mirror image? 🪞",
                help="Select to mirror the image",
                key="mirror",
            ):
                image = ImageOps.mirror(image)

            # ---------- GRAYSCALE / B&W ----------
            flag = True

            if lcol.checkbox(
                "Convert to grayscale / black & white? 🔲",
                key="gray_bw",
                help="Select to convert image to grayscale or black and white",
            ):
                mode = "L"
                if (
                    lcol.radio(
                        label="Grayscale or B&W",
                        options=("Grayscale", "Black & White"),
                    )
                    == "Grayscale"
                ):
                    image = image.convert(mode)
                else:
                    flag = False
                    lcol.warning(
                        "Some operations not available for black and white images."
                    )
                    thresh = np.array(image).mean()
                    image = image.convert(mode).point(
                        lambda x: 255 if x > thresh else 0, mode="1"
                    )
            else:
                mode = "RGB"
            rcol.image(
                image,
                use_column_width="auto",
            )

            if lcol.button(
                "↩️ Reset",
                on_click=_reset,
                use_container_width=True,
                kwargs={"key": "checkboxes"},
            ):
                lcol.success("Image reset to original!")

        # ---------- OTHER OPERATIONS ----------
        # ---------- 1ST ROW ----------
        with st.container():
            lcol, mcol, rcol = st.columns(3)

            with lcol.expander("🔁 Rotate", expanded=True):
                if "rotate_slider" not in st.session_state:
                    st.session_state["rotate_slider"] = 0
                degrees = st.slider(
                    "Drag slider to rotate image clockwise 🔁",
                    min_value=0,
                    max_value=360,
                    value=st.session_state["rotate_slider"],
                    key="rotate_slider",
                )
                rotated_img = image.rotate(360 - degrees)
                st.image(
                    rotated_img,
                    use_column_width="auto",
                    caption=f"Rotated by {degrees} degrees clockwise",
                )
                if st.button(
                    "↩️ Reset Rotation",
                    on_click=_reset,
                    use_container_width=True,
                    kwargs={"key": "rotate_slider"},
                ):
                    st.success("Rotation reset to original!")

            if flag:
                with mcol.expander("💡 Brightness", expanded=True):
                    if "brightness_slider" not in st.session_state:
                        st.session_state["brightness_slider"] = 100
                    brightness_factor = st.slider(
                        "Drag slider to change brightness 💡",
                        min_value=0,
                        max_value=1000,
                        value=st.session_state["brightness_slider"],
                        key="brightness_slider",
                    )
                    brightness_img = np.asarray(
                        ImageEnhance.Brightness(rotated_img).enhance(
                            brightness_factor / 100
                        )
                    )
                    st.image(
                        brightness_img,
                        use_column_width="auto",
                        caption=f"Brightness: {brightness_factor}%",
                    )
                    if st.button(
                        "↩️ Reset Brightness",
                        on_click=_reset,
                        use_container_width=True,
                        kwargs={"key": "brightness_slider"},
                    ):
                        st.success("Brightness reset to original!")

                with rcol.expander("Saturation", expanded=True):
                    if "saturation_slider" not in st.session_state:
                        st.session_state["saturation_slider"] = 100
                    saturation_factor = st.slider(
                        "Drag slider to change saturation",
                        min_value=0,
                        max_value=1000,
                        value=st.session_state["saturation_slider"],
                        key="saturation_slider",
                    )
                    saturation_img = np.asarray(
                        ImageEnhance.Color(Image.fromarray(brightness_img)).enhance(
                            saturation_factor / 100
                        )
                    )
                    st.image(
                        saturation_img,
                        use_column_width="auto",
                        caption=f"Saturation: {saturation_factor}%",
                    )
                    if st.button(
                        "↩️ Reset Saturation",
                        on_click=_reset,
                        use_container_width=True,
                        kwargs={"key": "saturation_slider"},
                    ):
                        rcol.success("Saturation reset to original!")

                # ---------- 2ND ROW ----------
                with st.container():
                    lcol, mcol, rcol = st.columns(3)

                    with lcol.expander("Contrast", expanded=True):
                        if "contrast_slider" not in st.session_state:
                            st.session_state["contrast_slider"] = 100
                        contrast_factor = st.slider(
                            "Drag slider to change contrast",
                            min_value=0,
                            max_value=1000,
                            value=st.session_state["contrast_slider"],
                            key="contrast_slider",
                        )
                        contrast_img = np.asarray(
                            ImageEnhance.Contrast(
                                Image.fromarray(saturation_img)
                            ).enhance(contrast_factor / 100)
                        )
                        st.image(
                            contrast_img,
                            use_column_width="auto",
                            caption=f"Contrast: {contrast_factor}%",
                        )
                        if st.button(
                            "↩️ Reset Contrast",
                            on_click=_reset,
                            use_container_width=True,
                            kwargs={"key": "contrast_slider"},
                        ):
                            st.success("Contrast reset to original!")

                    with mcol.expander("Sharpness", expanded=True):
                        if "sharpness_slider" not in st.session_state:
                            st.session_state["sharpness_slider"] = 100
                        sharpness_factor = st.slider(
                            "Drag slider to change sharpness",
                            min_value=0,
                            max_value=1000,
                            value=st.session_state["sharpness_slider"],
                            key="sharpness_slider",
                        )
                        sharpness_img = np.asarray(
                            ImageEnhance.Sharpness(
                                Image.fromarray(contrast_img)
                            ).enhance(sharpness_factor / 100)
                        )
                        st.image(
                            sharpness_img,
                            use_column_width="auto",
                            caption=f"Sharpness: {sharpness_factor}%",
                        )
                        if st.button(
                            "↩️ Reset Sharpness",
                            on_click=_reset,
                            use_container_width=True,
                            kwargs={"key": "sharpness_slider"},
                        ):
                            st.success("Sharpness reset to original!")

        # ---------- FINAL OPERATIONS ----------
        st.subheader("🪄 Results")

        try:
            final_image = sharpness_img
        except NameError:
            final_image = rotated_img

        image_comparison(
            img1=img_arr,
            img2=final_image,
            label1=f"Original Image ({pil_img.size[0]} x {pil_img.size[1]})",
            label2=(
                f"Final Image ({final_image.shape[1]} x {final_image.shape[0]})"
                if flag
                else f"Final Image ({final_image.size[1]} x {final_image.size[0]})"
            ),
        )

        lcol, rcol = st.columns(2)

        lcol.image(
            img_arr,
            use_column_width="auto",
            caption=f"Original Image ({pil_img.size[0]} x {pil_img.size[1]})",
        )

        rcol.image(
            final_image,
            use_column_width="auto",
            caption=(
                f"Final Image ({final_image.shape[1]} x {final_image.shape[0]})"
                if flag
                else f"Final Image ({final_image.size[1]} x {final_image.size[0]})"
            ),
        )

        if flag:
            Image.fromarray(final_image).save("final_image.png")
        else:
            final_image.save("final_image.png")

        col1, col2, col3 = st.columns(3)

        if col1.button(
            "↩️ Reset All",
            on_click=_reset,
            use_container_width=True,
            kwargs={"key": "all"},
        ):
            st.success(body="Image reset to original!", icon="↩️")

        if col2.button(
            "🔀 Surprise Me!",
            on_click=_randomize,
            use_container_width=True,
        ):
            st.success(body="Random image generated", icon="🔀")

        with open("final_image.png", "rb") as file:
            col3.download_button(
                "💾 Download final image",
                data=file,
                mime="image/png",
                use_container_width=True,
            )
