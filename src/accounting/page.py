import runpy

import streamlit as st

from src.accounting.common import base_url
from src.accounting.state import (
    ACCOUNTING_TYPES_EXPORT_PATH,
    CHECK_ACCOUNTS_EXPORT_PATH,
    PRODUCTS_EXPORT_PATH,
    TAX_RULES_EXPORT_PATH,
    TAX_SETS_EXPORT_PATH,
)
from src.sevdesk.api import load_env_fallback
from src.streamlit_apps.common import REPO_ROOT


def render_accounting_overview_page() -> None:
    from src.accounting.ui.browse_tab import render_bookings_by_check_account_section

    load_env_fallback()
    st.title("🧮 Accounting")
    st.caption(
        "sevDesk booking lookup, voucher and payment management, Amazon workflows, monthly Umsatz tooling, Lohn Belege processing, and master data tools."
    )
    render_bookings_by_check_account_section()


def render_accounting_md_page() -> None:
    from src.accounting.ui.master_data_tab import render_master_data_tab

    load_env_fallback()
    st.title("🛠️ Accounting MD")
    st.caption(
        "sevDesk master data maintenance for products, accounting types, check accounts, tax sets, and tax rules."
    )

    with st.expander("Connection & Paths", expanded=False):
        st.code(base_url())
        st.caption(
            "Master data paths:"
            f" `{PRODUCTS_EXPORT_PATH}`"
            f" , `{ACCOUNTING_TYPES_EXPORT_PATH}`"
            f" , `{CHECK_ACCOUNTS_EXPORT_PATH}`"
            f" , `{TAX_SETS_EXPORT_PATH}`"
            f" and `{TAX_RULES_EXPORT_PATH}`"
        )

    render_master_data_tab()


def render_accounting_amazon_page() -> None:
    from src.accounting.amazon_customers import refresh_live_amazon_customers
    from src.accounting.state import bootstrap_accounting_state
    from src.accounting.ui.amazon_tab import render_amazon_tab

    load_env_fallback()
    bootstrap_accounting_state(refresh_live_amazon_customers)
    st.title("🧮 Accounting / Amazon")
    st.caption("Amazon voucher workflows and Sparkasse booking analysis.")
    render_amazon_tab()


def render_accounting_belege_page() -> None:
    from src.accounting.ui.browse_tab import render_latest_belege_section

    load_env_fallback()
    st.title("🧾 Accounting / Belegverwaltung")
    st.caption("Inspect the latest sevDesk Belege and filter them by status and tags.")
    render_latest_belege_section()


def render_accounting_rechnungen_page() -> None:
    from src.accounting.ui.rechnungen_tab import render_rechnungen_section

    load_env_fallback()
    st.title("📄 Accounting / Rechnungsverwaltung")
    st.caption("Inspect the latest sevDesk Rechnungen and filter them by date, status, and text.")
    render_rechnungen_section()


def render_accounting_lieferscheine_page() -> None:
    load_env_fallback()
    runpy.run_path(str(REPO_ROOT / "pages/Lieferscheine.py"), run_name="__main__")


def render_accounting_payments_page() -> None:
    from src.accounting.ui.payments_tab import render_payments_section

    load_env_fallback()
    st.title("💸 Accounting / Zahlungsverwaltung")
    st.caption(
        "Inspect sevDesk payments, filter them meaningfully, export them, and move them between check accounts."
    )
    render_payments_section()


def render_accounting_monthly_umsatz_page() -> None:
    from src.accounting.ui.monthly_umsatz_view import render_monthly_umsatz_view

    load_env_fallback()
    render_monthly_umsatz_view()


def render_accounting_lohn_belege_page() -> None:
    from src.accounting.ui.lohn_belege_view import render_lohn_belege_view

    load_env_fallback()
    render_lohn_belege_view()
