import base64
from pathlib import Path

import pandas as pd
import streamlit as st

from src.accounting.amazon_extraction import format_amazon_payment_row
from src.accounting.common import load_json_payload, report_error
from src.accounting.sevdesk_browse import (
    format_latest_voucher_row,
    format_transaction_row,
)
from src.accounting.state import (
    ACCOUNTING_TYPES_EXPORT_PATH,
    CHECK_ACCOUNTS_EXPORT_PATH,
    TAX_RULES_EXPORT_PATH,
    TAX_SETS_EXPORT_PATH,
)


def show_vouchers(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Load the latest sevDesk Belege to inspect them here.")
        return
    if not rows:
        st.info("No Belege found.")
        return
    st.success(f"Loaded {len(rows)} Belege.")
    st.dataframe(pd.DataFrame([format_latest_voucher_row(row) for row in rows]), width="stretch")
    with st.expander("Raw API response"):
        st.json(rows)


def show_accounting_types(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk accounting types to inspect them here.")
        return
    if not rows:
        st.info("No accounting types stored yet.")
        return
    st.success(f"Stored {len(rows)} accounting types in `{ACCOUNTING_TYPES_EXPORT_PATH}`.")


def show_check_accounts(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk check accounts to inspect them here.")
        return
    if not rows:
        st.info("No check accounts stored yet.")
        return
    st.success(f"Stored {len(rows)} check accounts in `{CHECK_ACCOUNTS_EXPORT_PATH}`.")


def show_tax_rules(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk tax rules to inspect them here.")
        return
    if not rows:
        st.info("No tax rules stored yet.")
        return
    st.success(f"Stored {len(rows)} tax rules in `{TAX_RULES_EXPORT_PATH}`.")


def show_tax_sets(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk tax sets to inspect them here.")
        return
    if not rows:
        st.info("No tax sets stored yet.")
        return
    st.success(f"Stored {len(rows)} tax sets in `{TAX_SETS_EXPORT_PATH}`.")


def show_amazon_customers(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Amazon customers are loaded live from sevDesk when the page starts.")
        return
    if not rows:
        st.info("No sevDesk customers with `Amazon` in the name were found.")
        return
    st.success(f"Loaded {len(rows)} live sevDesk customer entries with `Amazon` in the name.")
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def show_transactions(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Select a stored check account and load its latest bookings.")
        return
    if not rows:
        st.info("No bookings found for the selected check account.")
        return
    st.success(f"Loaded {len(rows)} bookings.")
    st.dataframe(pd.DataFrame([format_transaction_row(row) for row in rows]), width="stretch")
    with st.expander("Raw API response"):
        st.json(rows)


def show_amazon_payments(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Load Sparkasse bookings filtered for Amazon Payments Europe here.")
        return
    if not rows:
        st.info("No matching Amazon Payments Europe bookings found in the Sparkasse account.")
        return
    st.success(f"Loaded {len(rows)} matching Sparkasse bookings.")
    st.dataframe(pd.DataFrame([format_amazon_payment_row(row) for row in rows]), width="stretch")
    with st.expander("Raw API response"):
        st.json(rows)


def render_pdf_inline(path_str: str, *, height: int = 420) -> None:
    path = Path(path_str)
    if not path.exists():
        report_error(f"PDF not found: {path}")
        return
    encoded_pdf = base64.b64encode(path.read_bytes()).decode("ascii")
    st.markdown(
        (
            '<iframe src="data:application/pdf;base64,'
            f"{encoded_pdf}"
            f'" width="100%" height="{height}" type="application/pdf"></iframe>'
        ),
        unsafe_allow_html=True,
    )


def show_downloaded_payload(title: str, path: Path) -> None:
    st.markdown(f"**{title}**")
    st.caption(f"`{path}`")
    payload = load_json_payload(path)
    if payload is None:
        st.info("No downloaded data file found.")
        return
    st.json(payload)
