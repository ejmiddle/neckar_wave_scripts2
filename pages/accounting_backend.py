import streamlit as st

from src.accounting.amazon_customers import refresh_live_amazon_customers
from src.accounting.common import base_url
from src.accounting.state import (
    ACCOUNTING_TYPES_EXPORT_PATH,
    CHECK_ACCOUNTS_EXPORT_PATH,
    TAX_RULES_EXPORT_PATH,
    TAX_SETS_EXPORT_PATH,
    bootstrap_accounting_state,
)
from src.accounting.ui.master_data_tab import render_master_data_tab
from src.sevdesk.api import load_env_fallback

load_env_fallback()

st.title("🛠️ Accounting Backend")
st.caption(
    "sevDesk master data maintenance for accounting types, check accounts, tax sets, and tax rules."
)

with st.expander("Connection & Paths", expanded=False):
    st.code(base_url())
    st.caption(
        "Master data paths:"
        f" `{ACCOUNTING_TYPES_EXPORT_PATH}`"
        f" , `{CHECK_ACCOUNTS_EXPORT_PATH}`"
        f" , `{TAX_SETS_EXPORT_PATH}`"
        f" and `{TAX_RULES_EXPORT_PATH}`"
    )

bootstrap_accounting_state(refresh_live_amazon_customers)
render_master_data_tab()
