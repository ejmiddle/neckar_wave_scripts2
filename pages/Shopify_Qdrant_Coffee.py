import streamlit as st

from src.bestellungen_analyse import bestellungen_analyse

st.title("🛒 Shopify Bestellungen - Coffee")
bestellungen_analyse(
    title_filter_mode="exclude",
    default_titles=[
        "Gutes Brot nach Ziegelhausen/Schlierbach",
        "Unsere Brote",
    ],
    state_prefix="shopify_coffee",
    show_orders_by_customer=False,
    default_days_back=14,
    show_unfulfilled_filter=True,
    show_notion_button=False,
)
