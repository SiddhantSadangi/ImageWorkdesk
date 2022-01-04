import numpy as np
import streamlit as st

from PIL import Image, ImageEnhance
from streamlit_cropper import st_cropper

VERSION = "0.3.1"


# ---------- FUNCTIONS ----------
def _reset(key):
    if key != "all":
        st.session_state[key] = 100
    else:
        st.session_state["brightness_slider"] = st.session_state[
            "saturation_slider"
        ] = st.session_state["contrast_slider"] = st.session_state[
            "sharpness_slider"
        ] = 100
        st.session_state["crop"] = False


def _randomize():
    st.session_state["brightness_slider"] = np.random.randint(0, 200)
    st.session_state["saturation_slider"] = np.random.randint(0, 200)
    st.session_state["contrast_slider"] = np.random.randint(0, 200)
    st.session_state["sharpness_slider"] = np.random.randint(0, 200)


# ---------- HEADER ----------
st.title("Welcome to the Image WorkDesk!")
st.markdown(
    "A mini image processing Streamlit app by [Siddhant Sadangi](https://linkedin.com/in/siddhantsadangi)."
)
st.caption(
    "This app lets you crop images and play around with image properties like brightness, saturation, contrast, and sharpness. "
    "You can also randomize these properties and download the final image at the click of a button!"
)
st.caption("More functionality coming soon... Stay tuned :)")

uploaded_file = st.file_uploader(
    label="Upload an image", type=["bmp", "jpg", "jpeg", "png", "svg"]
)

if uploaded_file is not None:
    name = uploaded_file.name.rsplit(".", 1)[0]
    ext = uploaded_file.name.split(".")[-1]
    img_arr = np.asarray(Image.open(uploaded_file))
    st.image(img_arr, use_column_width="auto", caption="Uploaded Image")

    st.caption("All changes are applied on top of the previous change.")

    # ---------- CROP ----------

    with st.container():
        left_col, right_col = st.columns(2)

        with left_col:
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

        with right_col:
            cropped_img = Image.fromarray(img_arr).crop(bb)
            st.text("Preview")
            st.image(
                cropped_img,
                use_column_width="auto",
            )

    crop = st.checkbox(
        label="Use cropped Image?",
        help="Select to use the cropped image in further operations",
        key="crop",
    )
    image = cropped_img if crop else Image.fromarray(img_arr)

    # ---------- OTHER OPERATIONS ----------
    with st.container():
        left_col, right_col = st.columns(2)

        # ---------- BRIGHTNESS ----------
        if "brightness_slider" not in st.session_state:
            st.session_state["brightness_slider"] = 100
        brightness_factor = left_col.slider(
            "Drag slider to change brightness",
            min_value=0,
            max_value=200,
            value=st.session_state["brightness_slider"],
            key="brightness_slider",
        )
        brightness_img = np.asarray(
            ImageEnhance.Brightness(image).enhance(brightness_factor / 100)
        )
        left_col.image(
            brightness_img,
            use_column_width="auto",
            caption=f"Brightness: {brightness_factor}%",
        )
        if left_col.button(
            "Reset Brightness",
            on_click=_reset,
            kwargs={"key": "brightness_slider"},
        ):
            left_col.success(f"Brightness reset to original!")

        # ---------- SATURATION ----------
        if "saturation_slider" not in st.session_state:
            st.session_state["saturation_slider"] = 100
        saturation_factor = right_col.slider(
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
        right_col.image(
            saturation_img,
            use_column_width="auto",
            caption=f"Saturation: {saturation_factor}%",
        )
        if right_col.button(
            "Reset Saturation",
            on_click=_reset,
            kwargs={"key": "saturation_slider"},
        ):
            right_col.success(f"Saturation reset to original!")

    with st.container():
        left_col, right_col = st.columns(2)
        # ---------- CONTRAST ----------
        if "contrast_slider" not in st.session_state:
            st.session_state["contrast_slider"] = 100
        contrast_factor = left_col.slider(
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
        left_col.image(
            contrast_img,
            use_column_width="auto",
            caption=f"Contrast: {contrast_factor}%",
        )
        if left_col.button(
            "Reset Contrast", on_click=_reset, kwargs={"key": "contrast_slider"}
        ):
            left_col.success(f"Contrast reset to original!")

        # ---------- SHARPNESS ----------
        if "sharpness_slider" not in st.session_state:
            st.session_state["sharpness_slider"] = 100
        sharpness_factor = right_col.slider(
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
        right_col.image(
            sharpness_img,
            use_column_width="auto",
            caption=f"Sharpness: {sharpness_factor}%",
        )
        if right_col.button(
            "Reset Sharpness",
            on_click=_reset,
            kwargs={"key": "sharpness_slider"},
        ):
            right_col.success(f"Sharpness reset to original!")

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

# ---------- FOOTER ----------
st.caption(
    "---\n"
    "Thanks for checking out this mini-project! :sparkling_heart:  \n"
    "I am working on additional features, and would love to hear your feedback and if you had some features which "
    "you would like to be added.  \n "
    "You can reach out to me at [siddhant.sadangi@gmail.com](mailto:siddhant.sadangi@gmail.com) and/or connect "
    "with me on [LinkedIn](https://linkedin.com/in/siddhantsadangi).",
)

col1, col2, col3 = st.columns(3)

with col2:
    st.components.v1.html(
        '<script type="text/javascript" src="https://cdnjs.buymeacoffee.com/1.0.0/button.prod.min.js" '
        'data-name="bmc-button" data-slug="siddhantsadangi" data-color="#000000" data-emoji=""  '
        'data-font="Cookie" data-text="Buy me a coffee" data-outline-color="#ffffff" '
        'data-font-color="#ffffff" data-coffee-color="#FFDD00" ></script>',
        height=60,
    )

st.components.v1.html(
    f'<div style="text-align:right; font-size:14px; color:grey"> v{VERSION} </div>',
    height=20,
)

# TODO: Flip and rotate, Remove background
