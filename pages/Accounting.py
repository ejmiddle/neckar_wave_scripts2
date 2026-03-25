import streamlit as st

from src.accounting.amazon_customers import refresh_live_amazon_customers
from src.accounting.common import base_url
from src.accounting.state import bootstrap_accounting_state
from src.accounting.ui.amazon_tab import render_amazon_tab
from src.accounting.ui.browse_tab import render_browse_tab
from src.sevdesk.api import load_env_fallback

load_env_fallback()

st.title("🧮 Accounting")
st.caption("sevDesk lookups for Belege, bookings, and Amazon voucher workflows.")

st.subheader("Connection")
st.code(base_url())

bootstrap_accounting_state(refresh_live_amazon_customers)

tab1, tab2 = st.tabs(["Overview", "Operations"])

with tab1:
    st.info("Master data maintenance moved to the `Accounting Backend` support page.")
    if st.button("Open Accounting Backend", width="stretch"):
        st.switch_page("pages/accounting_backend.py")

with tab2:
    render_browse_tab()
    render_amazon_tab()
