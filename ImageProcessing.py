import numpy as np
import streamlit as st

from PIL import Image, ImageEnhance


def _reset_slider(key):
    if key != "all":
        st.session_state[key] = 100
    else:
        st.session_state["brightness_slider"] = st.session_state[
            "saturation_slider"
        ] = st.session_state["contrast_slider"] = st.session_state[
            "sharpness_slider"
        ] = 100


def _randomize():
    st.session_state["brightness_slider"] = np.random.randint(0, 200)
    st.session_state["saturation_slider"] = np.random.randint(0, 200)
    st.session_state["contrast_slider"] = np.random.randint(0, 200)
    st.session_state["sharpness_slider"] = np.random.randint(0, 200)


st.header("Welcome to the Image WorkDesk!")
st.caption("A mini-app image-processing by Siddhant Sadangi")

uploaded_file = st.file_uploader(
    label="Upload an image", type=["bmp", "jpg", "jpeg", "png", "svg"]
)

if uploaded_file is not None:
    name = uploaded_file.name.rsplit(".", 1)[0]
    ext = uploaded_file.name.split(".")[-1]
    img_arr = np.asarray(Image.open(uploaded_file))
    st.image(img_arr, use_column_width="auto", caption="Uploaded Image")
    st.caption("All changes are applied on top of the previous change.")

    left_col, right_col = st.columns(2)

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
        ImageEnhance.Brightness(Image.fromarray(img_arr)).enhance(
            brightness_factor / 100
        )
    )
    left_col.image(
        brightness_img,
        use_column_width="auto",
        caption=f"Brightness: {brightness_factor}%",
    )
    if left_col.button(
        "Reset Brightness", on_click=_reset_slider, kwargs={"key": "brightness_slider"}
    ):
        left_col.success(f"Brightness reset to original!")

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
        "Reset Saturation", on_click=_reset_slider, kwargs={"key": "saturation_slider"}
    ):
        right_col.success(f"Saturation reset to original!")

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
        contrast_img, use_column_width="auto", caption=f"Contrast: {contrast_factor}%"
    )
    if left_col.button(
        "Reset Contrast", on_click=_reset_slider, kwargs={"key": "contrast_slider"}
    ):
        left_col.success(f"Contrast reset to original!")

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
        "Reset Sharpness", on_click=_reset_slider, kwargs={"key": "sharpness_slider"}
    ):
        right_col.success(f"Sharpness reset to original!")

    st.image(sharpness_img, use_column_width="auto", caption="Final Image")

    col1, col2, col3 = st.columns(3)
    if col1.button("Reset All", on_click=_reset_slider, kwargs={"key": "all"}):
        st.success(f"Image reset to original!")
    if col2.button("Surprise Me!", on_click=_randomize):
        st.success(f"Random image generated")
    col3.markdown(
        "Right click on the image and click on `Save Image As` to download image"
    )

    st.markdown(
        "Thanks for checking out this mini-project! :sparkling_heart:  \n"
        "I am working on additional features, and would love to hear your feedback and if you had some features which you would like to be added.  \n"
        "You can reach out to me at [siddhant.sadangi@gmail.com](mailto:siddhant.sadangi@gmail.com) and/or connect with me on [LinkedIn](https://linkedin.com/in/siddhantsadangi).",
        unsafe_allow_html=False,
    )
    st.caption(
        "If you can help me figure out how to download an image using streamlit.download_button(), please do reach out :)"
    )

# TODO: Flip and rotate, Remove background
