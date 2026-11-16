import os
import streamlit as st

st.set_page_config(
    page_title="AssetCompare Pro | Selector de Productos",
    page_icon="📈",
    layout="wide",
)

# Buscar el archivo index.html en la misma carpeta
html_file_path = os.path.join(os.path.dirname(__file__), "index.html")

if os.path.exists(html_file_path):
    with open(html_file_path, "r", encoding="utf-8") as f:
        html_code = f.read()
    st.components.v1.html(html_code, height=950, scrolling=True)
else:
    st.error(
        "⚠️ No se encuentra el archivo 'index.html'. Súbelo a la raíz del repositorio de GitHub junto con 'app.py'."
    )
