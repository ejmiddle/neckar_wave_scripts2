from collections.abc import Callable
from pathlib import Path
from typing import Any

import streamlit as st

ACCOUNTING_TYPES_EXPORT_PATH = Path("data/sevdesk/exports/master_data/accounting_types.json")
CHECK_ACCOUNTS_EXPORT_PATH = Path("data/sevdesk/exports/master_data/checkaccounts.json")
PRODUCTS_EXPORT_PATH = Path("data/sevdesk/exports/master_data/products.json")
TAX_RULES_EXPORT_PATH = Path("data/sevdesk/exports/master_data/tax_rules.json")
TAX_SETS_EXPORT_PATH = Path("data/sevdesk/exports/master_data/tax_sets.json")
SEVDESK_CACHE_DIR = Path("data/sevdesk/cache")
AMAZON_RECEIPTS_DIR = Path("data/sevdesk/inputs/Amazon_Belege")
AMAZON_VOUCHER_OUTPUT_DIR = Path("data/sevdesk/generated/amazon_voucher_payloads")

SPARKASSE_NAME_FRAGMENT = "Sparkasse"
AMAZON_PAYEE_NAME = (
    "AMAZON PAYMENTS",
    "AMAZON EU",
)
AMAZON_DEFAULT_CUSTOMER_NAME = "Amazon EU - DE"

TRANSACTION_STATUS_LABELS = {
    "100": "Created",
    "200": "Linked",
    "300": "Private",
    "400": "Booked",
}

SEVDESK_TAX_RULE_INNER_COMMUNITY_EXPENSE = {
    "id": 3,
    "objectName": "TaxRule",
}
SEVDESK_TAX_RULE_DEFAULT_TAXABLE_EXPENSE = {
    "id": 9,
    "objectName": "TaxRule",
}
SEVDESK_TAX_SET_INNER_COMMUNITY_SUPPLY = {
    "id": "121404",
    "objectName": "TaxSet",
}

AMAZON_BOOKING_MATCH_MAX_DELAY_DAYS = 5
AMAZON_ANALYSIS_SESSION_KEYS = {
    "sevdesk_sparkasse_amazon_pdf_matches",
    "sevdesk_sparkasse_amazon_llm_result",
    "sevdesk_sparkasse_amazon_voucher_payload",
    "sevdesk_sparkasse_amazon_result_cursor",
}
AMAZON_CUSTOMERS_SESSION_KEY = "sevdesk_amazon_customers_rows"


def clear_amazon_analysis_state() -> None:
    for key in AMAZON_ANALYSIS_SESSION_KEYS:
        st.session_state.pop(key, None)


def matches_amazon_payee_name(value: Any) -> bool:
    haystack = str(value or "").casefold()
    return any(fragment.casefold() in haystack for fragment in AMAZON_PAYEE_NAME)


def bootstrap_accounting_state(refresh_live_amazon_customers: Callable[[], Any]) -> None:
    if AMAZON_CUSTOMERS_SESSION_KEY not in st.session_state:
        refresh_live_amazon_customers()
