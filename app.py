import numpy as np
import streamlit as st

from PIL import Image, ImageEnhance, ImageOps
from streamlit_cropper import st_cropper

VERSION = "0.4.4"

st.set_page_config(
    page_title="Image WorkDesk",
    page_icon="🖼️",
    menu_items={
        "About": f"Image WorkDesk v{VERSION}  "
        f"\nApp contact: [Siddhant Sadangi](mailto:siddhant.sadangi@gmail.com)",
        "Report a Bug": "https://github.com/SiddhantSadangi/ImageWorkdesk/issues/new",
        "Get help": None,
    },
)

# ---------- SIDEBAR ----------
with open("sidebar.html", "r", encoding="UTF-8") as sidebar_file:
    sidebar_html = sidebar_file.read().replace("{VERSION}", VERSION)

with st.sidebar:
    st.components.v1.html(sidebar_html, height=400)

# ---------- HEADER ----------
st.title("Welcome to Image WorkDesk!")
st.caption(
    "This app lets you crop images and play around with image properties like brightness, saturation, contrast, and sharpness. "
    "You can also randomize these properties and download the final image at the click of a button!"
)


# ---------- FUNCTIONS ----------
def _reset(key):
    if key == "all":
        st.session_state["rotate_slider"] = 0
        st.session_state["brightness_slider"] = st.session_state[
            "saturation_slider"
        ] = st.session_state["contrast_slider"] = 100
        st.session_state["crop"] = st.session_state["mirror"] = 0
    elif key == "rotate_slider":
        st.session_state["rotate_slider"] = 0
    else:
        st.session_state[key] = 100


def _randomize():
    st.session_state["mirror"] = np.random.choice([0, 1])
    st.session_state["rotate_slider"] = np.random.randint(0, 360)
    st.session_state["brightness_slider"] = np.random.randint(0, 200)
    st.session_state["saturation_slider"] = np.random.randint(0, 200)
    st.session_state["contrast_slider"] = np.random.randint(0, 200)
    st.session_state["sharpness_slider"] = np.random.randint(0, 200)


# ---------- OPERATIONS ----------
option = st.radio(
    label="Upload an image, or take one with your camera",
    options=("Upload an image", "Take a photo with my camera"),
    help="Uploaded images are deleted from the server when you\n* upload another image\n* clear the file uploader\n* close the browser tab",
)

if option == "Take a photo with my camera":
    upload_img = st.camera_input(
        label="Take a picture",
    )
else:
    upload_img = st.file_uploader(
        label="Upload an image",
        type=["bmp", "jpg", "jpeg", "png", "svg"],
    )

if upload_img is not None:
    name = upload_img.name.rsplit(".", 1)[0]
    ext = upload_img.name.split(".")[-1]
    pil_img = Image.open(upload_img).convert("RGB")
    img_arr = np.asarray(pil_img)

    # ---------- PROPERTIES ----------
    st.image(img_arr, use_column_width="auto", caption="Uploaded Image")
    orig_width, orig_height = pil_img.size
    st.text(f"Original width = {orig_width}px and height = {orig_height}px")

    st.caption("All changes are applied on top of the previous change.")

    # ---------- CROP ----------
    st.text("Crop image")
    cropped_img_coords = st_cropper(Image.fromarray(img_arr), return_type="box")

    # ---------- CREATE BOUNDING BOX ----------
    left = cropped_img_coords["left"]
    right = cropped_img_coords["left"] + cropped_img_coords["width"]
    top = cropped_img_coords["top"]
    bottom = cropped_img_coords["top"] + cropped_img_coords["height"]
    bb = (
        0 if left < 0 else left,
        0 if top < 0 else top,
        img_arr.shape[1] if right > img_arr.shape[1] else right,
        img_arr.shape[0] if bottom > img_arr.shape[0] else bottom,
    )

    cropped_img = Image.fromarray(img_arr).crop(bb)
    crp_width, crp_height = cropped_img.size
    st.text(f"Cropped width = {crp_width}px and height = {crp_height}px")

    with st.container():
        left_col, right_col = st.columns(2)
        crop = left_col.checkbox(
            label="Use cropped Image?",
            help="Select to use the cropped image in further operations",
            key="crop",
        )
        image = cropped_img if crop else Image.fromarray(img_arr)

        # ---------- MIRROR ----------
        mirror = right_col.checkbox(
            label="Mirror image?",
            help="Select to mirror the image",
            key="mirror",
        )

        image = ImageOps.mirror(image) if mirror else image

    # ---------- OTHER OPERATIONS ----------
    # ---------- 1ST ROW ----------
    with st.container():
        left_col, right_col = st.columns(2)

        # ---------- ROTATE ----------
        if "rotate_slider" not in st.session_state:
            st.session_state["rotate_slider"] = 0
        degrees = left_col.slider(
            "Drag slider to rotate image clockwise",
            min_value=0,
            max_value=360,
            value=st.session_state["rotate_slider"],
            key="rotate_slider",
        )
        rotated_img = image.rotate(360 - degrees)
        left_col.image(
            rotated_img,
            use_column_width="auto",
            caption=f"Rotated by {degrees} degrees clockwise",
        )
        if left_col.button(
            "Reset Rotation",
            on_click=_reset,
            kwargs={"key": "rotate_slider"},
        ):
            left_col.success(f"Rotation reset to original!")

        # ---------- BRIGHTNESS ----------
        if "brightness_slider" not in st.session_state:
            st.session_state["brightness_slider"] = 100
        brightness_factor = right_col.slider(
            "Drag slider to change brightness",
            min_value=0,
            max_value=200,
            value=st.session_state["brightness_slider"],
            key="brightness_slider",
        )
        brightness_img = np.asarray(
            ImageEnhance.Brightness(rotated_img).enhance(brightness_factor / 100)
        )
        right_col.image(
            brightness_img,
            use_column_width="auto",
            caption=f"Brightness: {brightness_factor}%",
        )
        if right_col.button(
            "Reset Brightness",
            on_click=_reset,
            kwargs={"key": "brightness_slider"},
        ):
            right_col.success(f"Brightness reset to original!")

    # ---------- 2ND ROW ----------
    with st.container():
        left_col, right_col = st.columns(2)
        # ---------- SATURATION ----------
        if "saturation_slider" not in st.session_state:
            st.session_state["saturation_slider"] = 100
        saturation_factor = left_col.slider(
            "Drag slider to change saturation",
            min_value=0,
            max_value=200,
            value=st.session_state["saturation_slider"],
            key="saturation_slider",
        )
        saturation_img = np.asarray(
            ImageEnhance.Color(Image.fromarray(brightness_img)).enhance(
                saturation_factor / 100
            )
        )
        left_col.image(
            saturation_img,
            use_column_width="auto",
            caption=f"Saturation: {saturation_factor}%",
        )
        if left_col.button(
            "Reset Saturation",
            on_click=_reset,
            kwargs={"key": "saturation_slider"},
        ):
            left_col.success(f"Saturation reset to original!")

        # ---------- CONTRAST ----------
        if "contrast_slider" not in st.session_state:
            st.session_state["contrast_slider"] = 100
        contrast_factor = right_col.slider(
            "Drag slider to change contrast",
            min_value=0,
            max_value=200,
            value=st.session_state["contrast_slider"],
            key="contrast_slider",
        )
        contrast_img = np.asarray(
            ImageEnhance.Contrast(Image.fromarray(saturation_img)).enhance(
                contrast_factor / 100
            )
        )
        right_col.image(
            contrast_img,
            use_column_width="auto",
            caption=f"Contrast: {contrast_factor}%",
        )
        if right_col.button(
            "Reset Contrast", on_click=_reset, kwargs={"key": "contrast_slider"}
        ):
            right_col.success(f"Contrast reset to original!")

        # ---------- 3RD ROW ----------
        with st.container():
            left_col, right_col = st.columns(2)
            # ---------- SHARPNESS ----------
            if "sharpness_slider" not in st.session_state:
                st.session_state["sharpness_slider"] = 100
            sharpness_factor = left_col.slider(
                "Drag slider to change sharpness",
                min_value=0,
                max_value=200,
                value=st.session_state["sharpness_slider"],
                key="sharpness_slider",
            )
            sharpness_img = np.asarray(
                ImageEnhance.Sharpness(Image.fromarray(contrast_img)).enhance(
                    sharpness_factor / 100
                )
            )
            left_col.image(
                sharpness_img,
                use_column_width="auto",
                caption=f"Sharpness: {sharpness_factor}%",
            )
            if left_col.button(
                "Reset Sharpness",
                on_click=_reset,
                kwargs={"key": "sharpness_slider"},
            ):
                left_col.success(f"Sharpness reset to original!")

    # ---------- FINAL OPERATIONS ----------
    st.subheader("View Results")
    left_col, right_col = st.columns(2)
    left_col.image(img_arr, use_column_width="auto", caption="Original Image")
    right_col.image(sharpness_img, use_column_width="auto", caption="Final Image")

    Image.fromarray(sharpness_img).save("final_image.png")

    col1, col2, col3 = st.columns(3)
    if col1.button("Reset All", on_click=_reset, kwargs={"key": "all"}):
        st.success(f"Image reset to original!")
    if col2.button("Surprise Me!", on_click=_randomize):
        st.success(f"Random image generated")
    with open("final_image.png", "rb") as file:
        col3.download_button("Download Final Image", data=file, mime="image/png")
