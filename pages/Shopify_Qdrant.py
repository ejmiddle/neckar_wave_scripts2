import streamlit as st

from src.bestellungen_analyse import bestellungen_analyse

st.title("🛒 Shopify Bestellungen - Brot")
bestellungen_analyse()
