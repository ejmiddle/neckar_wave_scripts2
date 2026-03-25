import streamlit as st

from src.bestellungen_analyse import bestellungen_analyse
from src.qdrant_eval import render_qdrant_tab

st.title("🛒 Shopify Bestellungen")

tabs = st.tabs(["Shopify", "Qdrant"])

with tabs[0]:
    bestellungen_analyse()

with tabs[1]:
    render_qdrant_tab()
