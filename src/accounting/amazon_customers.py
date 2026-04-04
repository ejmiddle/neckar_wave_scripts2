import json
import re
from pathlib import Path
from typing import Any

import streamlit as st

from src.accounting.common import base_url, report_error, safe_filename_token
from src.accounting.state import AMAZON_CUSTOMERS_SESSION_KEY
from src.logging_config import logger
from src.sevdesk.api import fetch_all_contacts, read_token
from src.sevdesk.voucher import first_object_from_response, write_json


def extract_contact_category_name(row: dict[str, Any]) -> str:
    category = row.get("category")
    if isinstance(category, dict):
        name = str(category.get("name", "")).strip()
        if name:
            return name
        category_id = str(category.get("id", "")).strip()
        if category_id:
            return category_id
    return str(category or "").strip()


def format_customer_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "name": str(row.get("name", "")).strip(),
        "name2": str(row.get("name2", "")).strip(),
        "customerNumber": str(row.get("customerNumber", "")).strip(),
        "categoryId": (
            str(row.get("category", {}).get("id", "")).strip()
            if isinstance(row.get("category"), dict)
            else ""
        ),
        "category": extract_contact_category_name(row),
        "status": row.get("status", ""),
        "email": str(row.get("email", "")).strip(),
        "vatNumber": str(row.get("vatNumber", "")).strip(),
        "zip": str(row.get("zip", "")).strip(),
        "city": str(row.get("city", "")).strip(),
        "country": str(row.get("country", "")).strip(),
    }


def looks_like_customer_contact(row: dict[str, Any]) -> bool:
    category_name = extract_contact_category_name(row).strip().lower()
    if category_name and any(token in category_name for token in ("customer", "kunde", "client")):
        return True
    return bool(str(row.get("customerNumber", "")).strip())


def find_customers_by_name_fragment(
    rows: list[dict[str, Any]],
    name_fragment: str,
) -> list[dict[str, Any]]:
    wanted = name_fragment.strip().lower()
    if not wanted:
        return []

    matches: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name", "")).strip().lower()
        name2 = str(row.get("name2", "")).strip().lower()
        if wanted in name or wanted in name2:
            matches.append(row)

    return sorted(
        matches,
        key=lambda row: (
            str(row.get("name", "")).strip().lower(),
            str(row.get("name2", "")).strip().lower(),
            str(row.get("id", "")).strip(),
        ),
    )


def normalize_vat_id(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def format_customer_display_name(row: dict[str, Any]) -> str:
    name = str(row.get("name", "")).strip()
    name2 = str(row.get("name2", "")).strip()
    if name and name2:
        return f"{name} {name2}"
    return name or name2 or str(row.get("id", "")).strip() or "-"


def find_customer_by_vat_id(rows: list[dict[str, Any]], vat_id: Any) -> dict[str, Any] | None:
    normalized_vat_id = normalize_vat_id(vat_id)
    if not normalized_vat_id:
        return None

    matches = [
        row
        for row in rows
        if normalize_vat_id(row.get("vatNumber")) == normalized_vat_id
    ]
    if not matches:
        return None

    matches = sorted(
        matches,
        key=lambda row: (
            str(row.get("status", "")) != "100",
            str(row.get("name", "")).strip().lower(),
            str(row.get("id", "")).strip(),
        ),
    )
    return matches[0]


def find_customer_by_name(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    wanted = name.strip().lower()
    if not wanted:
        return None

    matches = [
        row
        for row in rows
        if str(row.get("name", "")).strip().lower() == wanted
        or format_customer_display_name(row).strip().lower() == wanted
    ]
    if not matches:
        return None

    matches = sorted(
        matches,
        key=lambda row: (
            str(row.get("status", "")) != "100",
            str(row.get("name", "")).strip().lower(),
            str(row.get("id", "")).strip(),
        ),
    )
    return matches[0]


def customer_ref(customer_row: dict[str, Any]) -> dict[str, Any] | None:
    customer_id = str(customer_row.get("id", "")).strip()
    if not customer_id:
        return None
    return {
        "id": customer_id,
        "objectName": "Contact",
    }


def apply_customer_to_voucher_payload(
    payload: dict[str, Any],
    customer_row: dict[str, Any],
) -> dict[str, Any]:
    voucher = payload.get("voucher")
    if not isinstance(voucher, dict):
        return payload

    customer_reference = customer_ref(customer_row)
    if customer_reference is None:
        return payload

    voucher["supplier"] = customer_reference
    supplier_name = format_customer_display_name(customer_row)
    if supplier_name and supplier_name != "-":
        voucher["supplierName"] = supplier_name
    return payload


def sort_customer_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("name", "")).strip().lower(),
            str(row.get("name2", "")).strip().lower(),
            str(row.get("id", "")).strip(),
        ),
    )


def fetch_live_amazon_customers(base_url_value: str, token: str) -> list[dict[str, Any]]:
    rows = fetch_all_contacts(base_url_value, token, 1000, "id")
    formatted_rows = [format_customer_row(row) for row in rows]
    return sort_customer_rows(find_customers_by_name_fragment(formatted_rows, "Amazon"))


def refresh_live_amazon_customers(
    *,
    token: str | None = None,
    report_errors: bool = False,
) -> list[dict[str, Any]] | None:
    effective_token = token or read_token()
    if not effective_token:
        return None

    try:
        rows = fetch_live_amazon_customers(base_url(), effective_token)
    except Exception as exc:
        if report_errors:
            report_error(
                f"Failed to load Amazon customers: {exc}",
                log_message="Failed to load Amazon customers",
                exc_info=True,
            )
        else:
            logger.error("Failed to load Amazon customers: %s", exc)
        return None

    st.session_state[AMAZON_CUSTOMERS_SESSION_KEY] = rows
    return rows


def build_customer_number(name: str, vat_id: str, customer_rows: list[dict[str, Any]]) -> str:
    normalized_vat_id = normalize_vat_id(vat_id)
    existing_numbers = {
        str(row.get("customerNumber", "")).strip()
        for row in customer_rows
        if str(row.get("customerNumber", "")).strip()
    }
    fallback_name = safe_filename_token(name).upper()[:12]
    base = normalized_vat_id or fallback_name or "CUSTOMER"
    candidate = base
    suffix = 2
    while candidate in existing_numbers:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def build_customer_create_payload(
    *,
    seller_name: str,
    seller_vat_id: str,
    customer_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    prefixed_name = f"Amazon {seller_name.strip()}".strip()
    return {
        "name": prefixed_name,
        "status": 100,
        "customerNumber": build_customer_number(seller_name, seller_vat_id, customer_rows),
        "vatNumber": seller_vat_id.strip(),
        "category": {
            "id": 3,
            "objectName": "Category",
        },
        "description": "Automatically created from Amazon receipt extraction in the accounting app",
    }


def persist_updated_voucher_entry(
    voucher_entry: dict[str, Any],
    *,
    customer_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = voucher_entry.get("payload")
    if not isinstance(payload, dict):
        return voucher_entry

    updated_payload = json.loads(json.dumps(payload))
    if isinstance(customer_row, dict):
        updated_payload = apply_customer_to_voucher_payload(updated_payload, customer_row)

    voucher_path_str = str(voucher_entry.get("path", "")).strip()
    if voucher_path_str:
        write_json(Path(voucher_path_str), updated_payload)

    return {
        **voucher_entry,
        "payload": updated_payload,
        "customer": customer_row if isinstance(customer_row, dict) else voucher_entry.get("customer"),
    }


def coerce_created_customer_row(
    response_payload: dict[str, Any],
    *,
    fallback_name: str,
    fallback_vat_id: str,
    fallback_customer_number: str,
) -> dict[str, Any]:
    created_summary = first_object_from_response(response_payload) or {}
    combined = {
        **created_summary,
        "name": created_summary.get("name") or fallback_name,
        "vatNumber": created_summary.get("vatNumber") or fallback_vat_id,
        "customerNumber": created_summary.get("customerNumber") or fallback_customer_number,
    }
    return format_customer_row(combined)
