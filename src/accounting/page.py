import streamlit as st

from src.accounting.common import base_url
from src.accounting.state import (
    ACCOUNTING_TYPES_EXPORT_PATH,
    CHECK_ACCOUNTS_EXPORT_PATH,
    TAX_RULES_EXPORT_PATH,
    TAX_SETS_EXPORT_PATH,
)
from src.sevdesk.api import load_env_fallback


ACCOUNTING_SUBPAGE_KEY = "accounting_subpage"
ACCOUNTING_MAIN_VIEW = "main"
ACCOUNTING_OPERATIONS_VIEW = "receipts_amazon"
ACCOUNTING_MD_VIEW = "accounting_md"
ACCOUNTING_LATEST_BELEGE_VIEW = "latest_belege"
ACCOUNTING_MONTHLY_UMSATZ_VIEW = "monthly_umsatz"
ACCOUNTING_LOHN_BELEGE_VIEW = "lohn_belege"


def _show_subpage(view: str) -> None:
    st.session_state[ACCOUNTING_SUBPAGE_KEY] = view
    st.rerun()


def _current_subpage() -> str:
    return st.session_state.get(ACCOUNTING_SUBPAGE_KEY, ACCOUNTING_MAIN_VIEW)


def _render_accounting_md_view() -> None:
    from src.accounting.ui.master_data_tab import render_master_data_tab

    st.title("🛠️ Accounting MD")
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

    render_master_data_tab()


def _render_amazon_operations_view() -> None:
    from src.accounting.amazon_customers import refresh_live_amazon_customers
    from src.accounting.state import bootstrap_accounting_state
    from src.accounting.ui.amazon_tab import render_amazon_tab

    bootstrap_accounting_state(refresh_live_amazon_customers)
    render_amazon_tab()


def _render_latest_belege_view() -> None:
    from src.accounting.ui.browse_tab import render_latest_belege_section

    render_latest_belege_section()


def _render_accounting_overview_sections() -> None:
    from src.accounting.ui.browse_tab import render_bookings_by_check_account_section

    render_bookings_by_check_account_section()


def _render_monthly_umsatz_page() -> None:
    from src.accounting.ui.monthly_umsatz_view import render_monthly_umsatz_view

    render_monthly_umsatz_view()


def _render_lohn_belege_page() -> None:
    from src.accounting.ui.lohn_belege_view import render_lohn_belege_view

    render_lohn_belege_view()


def _render_back_button() -> None:
    action_col, _ = st.columns([1, 5])
    with action_col:
        if st.button("Back to Accounting", width="stretch"):
            _show_subpage(ACCOUNTING_MAIN_VIEW)


def render_accounting_app() -> None:
    load_env_fallback()
    current_subpage = _current_subpage()

    if current_subpage == ACCOUNTING_OPERATIONS_VIEW:
        st.title("🧮 Accounting / Amazon")
        st.caption("Amazon voucher workflows and Sparkasse booking analysis.")
        _render_back_button()
        _render_amazon_operations_view()
        return

    if current_subpage == ACCOUNTING_LATEST_BELEGE_VIEW:
        st.title("🧾 Accounting / Belegverwaltung")
        st.caption("Inspect the latest sevDesk Belege and filter them by status and tags.")
        _render_back_button()
        _render_latest_belege_view()
        return

    if current_subpage == ACCOUNTING_MD_VIEW:
        _render_back_button()
        _render_accounting_md_view()
        return

    if current_subpage == ACCOUNTING_MONTHLY_UMSATZ_VIEW:
        _render_back_button()
        _render_monthly_umsatz_page()
        return

    if current_subpage == ACCOUNTING_LOHN_BELEGE_VIEW:
        _render_back_button()
        _render_lohn_belege_page()
        return

    st.title("🧮 Accounting")
    st.caption(
        "sevDesk booking lookup, latest vouchers, Amazon workflows, monthly Umsatz tooling, Lohn Belege processing, and master data tools."
    )

    action_col1, action_col2, action_col3, action_col4, action_col5, _ = st.columns(
        [1, 1, 1, 1, 1, 2]
    )
    with action_col1:
        if st.button("Belegverwaltung", width="stretch"):
            _show_subpage(ACCOUNTING_LATEST_BELEGE_VIEW)
    with action_col2:
        if st.button("Receipts & Amazon", width="stretch"):
            _show_subpage(ACCOUNTING_OPERATIONS_VIEW)
    with action_col3:
        if st.button("Accounting MD", width="stretch"):
            _show_subpage(ACCOUNTING_MD_VIEW)
    with action_col4:
        if st.button("Monthly Umsatz", width="stretch"):
            _show_subpage(ACCOUNTING_MONTHLY_UMSATZ_VIEW)
    with action_col5:
        if st.button("Lohn Belege", width="stretch"):
            _show_subpage(ACCOUNTING_LOHN_BELEGE_VIEW)

    _render_accounting_overview_sections()
