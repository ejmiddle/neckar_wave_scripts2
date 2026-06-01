import streamlit as st

from src.bestellungen_analyse import BREAD_PRODUCT_TITLES, bestellungen_analyse

st.title("🛒 Shopify Bestellungen - Coffee")
bestellungen_analyse(
    title_filter_mode="exclude",
    default_titles=BREAD_PRODUCT_TITLES,
    state_prefix="shopify_coffee",
    show_orders_by_customer=False,
    default_days_back=7,
    show_unfulfilled_filter=True,
    show_notion_button=False,
)
