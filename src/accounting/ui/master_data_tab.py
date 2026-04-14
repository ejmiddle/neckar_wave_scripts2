import streamlit as st

from src.accounting.common import base_url, ensure_token, report_error
from src.accounting.master_data import (
    export_accounting_types,
    export_check_accounts,
    export_products,
    export_tax_rules,
    export_tax_sets,
    load_stored_accounting_types,
    load_stored_check_accounts,
    load_stored_products,
    load_stored_tax_rules,
    load_stored_tax_sets,
)
from src.accounting.state import (
    ACCOUNTING_TYPES_EXPORT_PATH,
    CHECK_ACCOUNTS_EXPORT_PATH,
    PRODUCTS_EXPORT_PATH,
    TAX_RULES_EXPORT_PATH,
    TAX_SETS_EXPORT_PATH,
)
from src.accounting.ui.displays import (
    show_accounting_types,
    show_check_accounts,
    show_downloaded_payload,
    show_products,
    show_tax_rules,
    show_tax_sets,
)


def render_master_data_tab() -> None:
    with st.expander("Products", expanded=True):
        if st.button("Fetch all products and store master data", width="stretch"):
            token = ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_products_rows"] = export_products(
                        base_url(),
                        token,
                    )
                except Exception as exc:
                    report_error(
                        f"Failed to load products: {exc}",
                        log_message="Failed to load products",
                        exc_info=True,
                    )

        stored_products = st.session_state.get("sevdesk_products_rows")
        if stored_products is None:
            stored_products = load_stored_products()
            if stored_products:
                st.session_state["sevdesk_products_rows"] = stored_products
        show_products(stored_products)
        show_downloaded_payload("Raw products JSON", PRODUCTS_EXPORT_PATH)

    with st.expander("Accounting Types", expanded=True):
        if st.button("Fetch all accounting types and store master data", width="stretch"):
            token = ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_accounting_types_rows"] = export_accounting_types(
                        base_url(),
                        token,
                    )
                except Exception as exc:
                    report_error(
                        f"Failed to load accounting types: {exc}",
                        log_message="Failed to load accounting types",
                        exc_info=True,
                    )

        stored_accounting_types = st.session_state.get("sevdesk_accounting_types_rows")
        if stored_accounting_types is None:
            stored_accounting_types = load_stored_accounting_types()
            if stored_accounting_types:
                st.session_state["sevdesk_accounting_types_rows"] = stored_accounting_types
        show_accounting_types(stored_accounting_types)
        show_downloaded_payload("Raw accounting types JSON", ACCOUNTING_TYPES_EXPORT_PATH)

    with st.expander("Check Accounts", expanded=False):
        if st.button("Fetch all check accounts and store master data", width="stretch"):
            token = ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_check_accounts_rows"] = export_check_accounts(
                        base_url(),
                        token,
                    )
                except Exception as exc:
                    report_error(
                        f"Failed to fetch check accounts: {exc}",
                        log_message="Failed to fetch check accounts",
                        exc_info=True,
                    )

        stored_check_accounts = st.session_state.get("sevdesk_check_accounts_rows")
        if stored_check_accounts is None:
            stored_check_accounts = load_stored_check_accounts()
            if stored_check_accounts:
                st.session_state["sevdesk_check_accounts_rows"] = stored_check_accounts
        show_check_accounts(stored_check_accounts)
        show_downloaded_payload("Raw check accounts JSON", CHECK_ACCOUNTS_EXPORT_PATH)

    with st.expander("Tax Sets", expanded=False):
        if st.button("Fetch all tax sets and store master data", width="stretch"):
            token = ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_tax_sets_rows"] = export_tax_sets(
                        base_url(),
                        token,
                    )
                except Exception as exc:
                    report_error(
                        f"Failed to load tax sets: {exc}",
                        log_message="Failed to load tax sets",
                        exc_info=True,
                    )

        stored_tax_sets = st.session_state.get("sevdesk_tax_sets_rows")
        if stored_tax_sets is None:
            stored_tax_sets = load_stored_tax_sets()
            if stored_tax_sets:
                st.session_state["sevdesk_tax_sets_rows"] = stored_tax_sets
        show_tax_sets(stored_tax_sets)
        show_downloaded_payload("Raw tax sets JSON", TAX_SETS_EXPORT_PATH)

    with st.expander("Tax Rules", expanded=False):
        if st.button("Fetch all tax rules and store master data", width="stretch"):
            token = ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_tax_rules_rows"] = export_tax_rules(
                        base_url(),
                        token,
                    )
                except Exception as exc:
                    report_error(
                        f"Failed to load tax rules: {exc}",
                        log_message="Failed to load tax rules",
                        exc_info=True,
                    )

        stored_tax_rules = st.session_state.get("sevdesk_tax_rules_rows")
        if stored_tax_rules is None:
            stored_tax_rules = load_stored_tax_rules()
            if stored_tax_rules:
                st.session_state["sevdesk_tax_rules_rows"] = stored_tax_rules
        show_tax_rules(stored_tax_rules)
        show_downloaded_payload("Raw tax rules JSON", TAX_RULES_EXPORT_PATH)
