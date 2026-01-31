import streamlit as st
from app_files.qdrant_eval import render_qdrant_tab
from app_files.bestellungen_analyse import bestellungen_analyse


tabs = st.tabs(["Shopify", "Qdrant"])

with tabs[0]:
    bestellungen_analyse()

with tabs[1]:
    render_qdrant_tab()
