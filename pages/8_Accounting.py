import base64
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.amazon_accounting_llm import (
    LLM_MODELS_BY_PROVIDER,
    LLM_PROVIDER_GOOGLE,
    LLM_PROVIDER_OPENAI,
    build_document_user_content,
    extract_amazon_accounting_data,
    resolve_llm_api_key,
)
from src.amazon_accounting_prompt_config import DEFAULT_SYSTEM_PROMPT
from src.lieferscheine_sources import split_pdf_bytes_to_page_images
from src.logging_config import logger
from src.sevdesk.api import (
    create_contact,
    create_voucher,
    fetch_all_accounting_types,
    fetch_all_check_accounts,
    fetch_all_contacts,
    fetch_all_tax_rules,
    fetch_all_transactions_for_check_account,
    fetch_latest_transactions_for_check_account,
    load_env_fallback,
    read_token,
    request_voucher_by_id,
    request_vouchers,
    upload_voucher_temp_file,
)
from src.sevdesk.constants import DEFAULT_BASE_URL
from src.sevdesk.voucher import (
    default_create_template,
    first_object_from_response,
    format_amount,
    format_date,
    format_number,
    format_text,
    load_rows,
    normalize_create_payload,
    validate_create_payload,
    write_json,
)

load_env_fallback()

ACCOUNTING_TYPES_EXPORT_PATH = Path("data/sevdesk/master_data/accounting_types.json")
CHECK_ACCOUNTS_EXPORT_PATH = Path("data/sevdesk/master_data/checkaccounts.json")
TAX_RULES_EXPORT_PATH = Path("data/sevdesk/master_data/tax_rules.json")
AMAZON_RECEIPTS_DIR = Path("data/sevdesk/Amazon_Belege")
AMAZON_VOUCHER_OUTPUT_DIR = Path("data/sevdesk/amazon_voucher_payloads")

st.title("🧮 Accounting")
st.caption("sevDesk lookups for Belege, accounting types, check accounts, and bookings.")

SPARKASSE_NAME_FRAGMENT = "Sparkasse"
AMAZON_PAYEE_NAME = "AMAZON PAYMENTS EUROPE S.C.A."
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
AMAZON_BOOKING_MATCH_MAX_DELAY_DAYS = 5
AMAZON_ANALYSIS_SESSION_KEYS = {
    "sevdesk_sparkasse_amazon_pdf_matches",
    "sevdesk_sparkasse_amazon_llm_result",
    "sevdesk_sparkasse_amazon_voucher_payload",
}
AMAZON_CUSTOMERS_SESSION_KEY = "sevdesk_amazon_customers_rows"


def _base_url() -> str:
    return os.getenv("SEVDESK_BASE_URL") or DEFAULT_BASE_URL


def _report_error(
    user_message: str,
    *,
    log_message: str | None = None,
    exc_info: bool = False,
) -> None:
    message = log_message or user_message
    if exc_info:
        logger.exception(message)
    else:
        logger.error(message)
    st.error(user_message)


def _ensure_token() -> str | None:
    token = read_token()
    if token:
        return token
    _report_error("No sevDesk API token found. Set `SEVDESK_KEY` in `.env`.")
    return None


def _format_voucher_row(row: dict[str, Any]) -> dict[str, Any]:
    supplier = row.get("supplierName")
    if not supplier and isinstance(row.get("supplier"), dict):
        supplier = row["supplier"].get("name")
    return {
        "id": str(row.get("id", "")),
        "nummer": format_number(row),
        "datum": format_date(row),
        "betrag": format_amount(row),
        "beschreibung": format_text(row),
        "lieferant": supplier or "-",
        "status": row.get("status", "-"),
    }


def _format_accounting_type_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "name": str(row.get("name", "")).strip(),
        "type": row.get("type", ""),
        "skr03": row.get("skr03"),
        "skr04": row.get("skr04"),
        "active": str(row.get("active", "0")) == "1",
        "status": row.get("status", ""),
    }


def _flag_as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip() == "1"


def _format_check_account_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "name": str(row.get("name", "")).strip(),
        "type": row.get("type", ""),
        "currency": row.get("currency"),
        "defaultAccount": _flag_as_bool(row.get("defaultAccount", False)),
        "status": row.get("status", ""),
        "accountingNumber": row.get("accountingNumber"),
        "iban": row.get("iban"),
        "bic": row.get("bic"),
        "bankServer": row.get("bankServer"),
        "lastSync": row.get("lastSync"),
    }


def _format_tax_rule_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "name": str(row.get("name", "")).strip(),
        "code": row.get("code"),
        "taxType": row.get("taxType"),
        "taxRate": row.get("taxRate", row.get("rate")),
        "status": row.get("status"),
        "isDefault": _flag_as_bool(row.get("isDefault", False)),
    }


def _extract_contact_category_name(row: dict[str, Any]) -> str:
    category = row.get("category")
    if isinstance(category, dict):
        name = str(category.get("name", "")).strip()
        if name:
            return name
        category_id = str(category.get("id", "")).strip()
        if category_id:
            return category_id
    return str(category or "").strip()


def _format_customer_row(row: dict[str, Any]) -> dict[str, Any]:
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
        "category": _extract_contact_category_name(row),
        "status": row.get("status", ""),
        "email": str(row.get("email", "")).strip(),
        "vatNumber": str(row.get("vatNumber", "")).strip(),
        "zip": str(row.get("zip", "")).strip(),
        "city": str(row.get("city", "")).strip(),
        "country": str(row.get("country", "")).strip(),
    }


def _looks_like_customer_contact(row: dict[str, Any]) -> bool:
    category_name = _extract_contact_category_name(row).strip().lower()
    if category_name and any(token in category_name for token in ("customer", "kunde", "client")):
        return True
    return bool(str(row.get("customerNumber", "")).strip())


def _format_transaction_row(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status", ""))
    return {
        "id": str(row.get("id", "")),
        "valueDate": row.get("valueDate"),
        "entryDate": row.get("entryDate"),
        "amount": row.get("amount"),
        "feeAmount": row.get("feeAmount"),
        "payeePayerName": row.get("payeePayerName"),
        "paymtPurpose": row.get("paymtPurpose"),
        "entryText": row.get("entryText"),
        "status": status,
        "statusMeaning": TRANSACTION_STATUS_LABELS.get(status, "Unknown"),
    }


def _extract_first_15_digits(value: Any) -> str:
    digits = "".join(re.findall(r"\d", str(value or "")))
    return digits[:15]


def _extract_amazon_order_number(value: Any) -> str:
    match = re.search(r"(\d{3}-\d{7}-\d{7})(?=\s+AMZN\b)", str(value or ""))
    if match:
        return match.group(1)
    return ""


def _parse_transaction_date(row: dict[str, Any]) -> date | None:
    for key in ("valueDate", "entryDate"):
        raw_value = row.get(key)
        if not raw_value:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw_value))
            return parsed.date()
        except ValueError:
            continue
    return None


def _filter_rows_by_date_range(
    rows: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        booking_date = _parse_transaction_date(row)
        if booking_date is None:
            continue
        if start_date <= booking_date <= end_date:
            filtered_rows.append(row)
    return filtered_rows


def _find_check_account_by_name(rows: list[dict[str, Any]], name_fragment: str) -> dict[str, Any] | None:
    wanted = name_fragment.strip().lower()
    for row in rows:
        name = str(row.get("name", "")).strip().lower()
        if wanted and wanted in name:
            return row
    return None


def _find_customers_by_name_fragment(rows: list[dict[str, Any]], name_fragment: str) -> list[dict[str, Any]]:
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


def _normalize_vat_id(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def _format_customer_display_name(row: dict[str, Any]) -> str:
    name = str(row.get("name", "")).strip()
    name2 = str(row.get("name2", "")).strip()
    if name and name2:
        return f"{name} {name2}"
    return name or name2 or str(row.get("id", "")).strip() or "-"


def _find_customer_by_vat_id(rows: list[dict[str, Any]], vat_id: Any) -> dict[str, Any] | None:
    normalized_vat_id = _normalize_vat_id(vat_id)
    if not normalized_vat_id:
        return None

    matches = [
        row
        for row in rows
        if _normalize_vat_id(row.get("vatNumber")) == normalized_vat_id
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


def _find_customer_by_name(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    wanted = name.strip().lower()
    if not wanted:
        return None

    matches = [
        row
        for row in rows
        if str(row.get("name", "")).strip().lower() == wanted
        or _format_customer_display_name(row).strip().lower() == wanted
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


def _customer_ref(customer_row: dict[str, Any]) -> dict[str, Any] | None:
    customer_id = str(customer_row.get("id", "")).strip()
    if not customer_id:
        return None
    return {
        "id": customer_id,
        "objectName": "Contact",
    }


def _apply_customer_to_voucher_payload(payload: dict[str, Any], customer_row: dict[str, Any]) -> dict[str, Any]:
    voucher = payload.get("voucher")
    if not isinstance(voucher, dict):
        return payload

    customer_ref = _customer_ref(customer_row)
    if customer_ref is None:
        return payload

    voucher["supplier"] = customer_ref
    supplier_name = _format_customer_display_name(customer_row)
    if supplier_name and supplier_name != "-":
        voucher["supplierName"] = supplier_name
    return payload


def _sort_customer_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("name", "")).strip().lower(),
            str(row.get("name2", "")).strip().lower(),
            str(row.get("id", "")).strip(),
        ),
    )


def _fetch_live_amazon_customers(base_url: str, token: str) -> list[dict[str, Any]]:
    rows = fetch_all_contacts(base_url, token, 1000, "id")
    formatted_rows = [_format_customer_row(row) for row in rows]
    return _sort_customer_rows(_find_customers_by_name_fragment(formatted_rows, "Amazon"))


def _refresh_live_amazon_customers(
    *,
    token: str | None = None,
    report_errors: bool = False,
) -> list[dict[str, Any]] | None:
    effective_token = token or read_token()
    if not effective_token:
        return None

    try:
        rows = _fetch_live_amazon_customers(_base_url(), effective_token)
    except Exception as exc:
        if report_errors:
            _report_error(
                f"Failed to load Amazon customers: {exc}",
                log_message="Failed to load Amazon customers",
                exc_info=True,
            )
        else:
            logger.error("Failed to load Amazon customers: %s", exc)
        return None

    st.session_state[AMAZON_CUSTOMERS_SESSION_KEY] = rows
    return rows


def _build_customer_number(name: str, vat_id: str, customer_rows: list[dict[str, Any]]) -> str:
    normalized_vat_id = _normalize_vat_id(vat_id)
    existing_numbers = {
        str(row.get("customerNumber", "")).strip()
        for row in customer_rows
        if str(row.get("customerNumber", "")).strip()
    }
    fallback_name = _safe_filename_token(name).upper()[:12]
    base = normalized_vat_id or fallback_name or "CUSTOMER"
    candidate = base
    suffix = 2
    while candidate in existing_numbers:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _build_customer_create_payload(
    *,
    seller_name: str,
    seller_vat_id: str,
    customer_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    prefixed_name = f"Amazon {seller_name.strip()}".strip()
    return {
        "name": prefixed_name,
        "status": 100,
        "customerNumber": _build_customer_number(seller_name, seller_vat_id, customer_rows),
        "vatNumber": seller_vat_id.strip(),
        "category": {
            "id": 3,
            "objectName": "Category",
        },
        "description": "Automatically created from Amazon receipt extraction in pages/8_Accounting.py",
    }


def _persist_updated_voucher_entry(
    voucher_entry: dict[str, Any],
    *,
    customer_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = voucher_entry.get("payload")
    if not isinstance(payload, dict):
        return voucher_entry

    updated_payload = json.loads(json.dumps(payload))
    if isinstance(customer_row, dict):
        updated_payload = _apply_customer_to_voucher_payload(updated_payload, customer_row)

    voucher_path_str = str(voucher_entry.get("path", "")).strip()
    if voucher_path_str:
        voucher_path = Path(voucher_path_str)
        write_json(voucher_path, updated_payload)

    return {
        **voucher_entry,
        "payload": updated_payload,
        "customer": customer_row if isinstance(customer_row, dict) else voucher_entry.get("customer"),
    }


def _coerce_created_customer_row(
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
    return _format_customer_row(combined)


def _format_amazon_payment_row(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status", ""))
    return {
        "id": str(row.get("id", "")),
        "valueDate": row.get("valueDate"),
        "entryDate": row.get("entryDate"),
        "amount": row.get("amount"),
        "payeePayerName": row.get("payeePayerName"),
        "paymtPurpose": row.get("paymtPurpose"),
        "status": status,
        "statusMeaning": TRANSACTION_STATUS_LABELS.get(status, "Unknown"),
        "orderNumber": _extract_amazon_order_number(row.get("paymtPurpose")),
        "first15Digits": _extract_first_15_digits(row.get("paymtPurpose")),
    }


def _format_status_option(status: str) -> str:
    label = TRANSACTION_STATUS_LABELS.get(status, "Unknown")
    return f"{status} - {label}"


def _clear_amazon_analysis_state() -> None:
    for key in AMAZON_ANALYSIS_SESSION_KEYS:
        st.session_state.pop(key, None)


def _parse_amount_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    cleaned = str(value).strip()
    if not cleaned:
        return None
    normalized = re.sub(r"[^\d,.\-]", "", cleaned)
    if not normalized:
        return None
    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif normalized.count(",") == 1:
        normalized = normalized.replace(",", ".")
    try:
        return round(float(normalized), 2)
    except ValueError:
        return None


def _format_currency_value(value: Any) -> str:
    amount = _parse_amount_value(value)
    if amount is None:
        return "-"
    return f"{amount:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


def _format_sevdesk_date(value: Any) -> str:
    parsed_date = value if isinstance(value, date) else _parse_iso_date(value)
    if parsed_date is None:
        parsed_date = date.today()
    return parsed_date.strftime("%d.%m.%Y")


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _normalize_compare_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _compare_document_values(left: Any, right: Any) -> bool | None:
    left_token = _normalize_compare_token(left)
    right_token = _normalize_compare_token(right)
    if not left_token or not right_token:
        return None
    return left_token in right_token or right_token in left_token


def _compare_amounts(booking_amount: Any, extracted_amount: Any) -> bool | None:
    booking_value = _parse_amount_value(booking_amount)
    extracted_value = _parse_amount_value(extracted_amount)
    if booking_value is None or extracted_value is None:
        return None
    return abs(abs(booking_value) - abs(extracted_value)) <= 0.01


def _compare_dates(booking_value: Any, extracted_value: Any) -> bool | None:
    booking_date = booking_value if isinstance(booking_value, date) else _parse_iso_date(booking_value)
    extracted_date = _parse_iso_date(extracted_value)
    if booking_date is None or extracted_date is None:
        return None
    return booking_date == extracted_date


def _compare_booking_after_receipt_window(booking_value: Any, extracted_value: Any) -> bool | None:
    booking_date = booking_value if isinstance(booking_value, date) else _parse_iso_date(booking_value)
    receipt_date = _parse_iso_date(extracted_value)
    if booking_date is None or receipt_date is None:
        return None
    delta_days = (booking_date - receipt_date).days
    return 0 <= delta_days <= AMAZON_BOOKING_MATCH_MAX_DELAY_DAYS


def _is_booking_receipt_match(booking_row: dict[str, Any], extracted: dict[str, Any]) -> bool | None:
    amount_matches = _compare_amounts(booking_row.get("amount"), extracted.get("amount"))
    date_matches = _compare_booking_after_receipt_window(
        _parse_transaction_date(booking_row),
        extracted.get("invoice_date"),
    )
    if amount_matches is None or date_matches is None:
        return None
    return amount_matches and date_matches


def _format_match_value(value: bool | None) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "-"


def _format_bool_value(value: Any) -> str:
    if value is True:
        return "Ja"
    if value is False:
        return "Nein"
    return "-"


def _safe_filename_token(value: Any) -> str:
    token = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip())
    token = token.strip("._-")
    return token or "unknown"


def _compute_sum_net(sum_gross: Any, tax_rate_percent: Any) -> float | None:
    gross_value = _parse_amount_value(sum_gross)
    tax_rate_value = _parse_amount_value(tax_rate_percent)
    if gross_value is None or tax_rate_value is None:
        return None
    divisor = 1 + (tax_rate_value / 100.0)
    if divisor <= 0:
        return None
    return round(gross_value / divisor, 2)


def _active_accounting_type_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_rows = [
        row
        for row in rows
        if _flag_as_bool(row.get("active", True)) and str(row.get("status", "100")) == "100"
    ]
    return active_rows or rows


def _find_accounting_type_by_name_fragments(
    rows: list[dict[str, Any]],
    fragments: list[str],
) -> dict[str, Any] | None:
    candidates = _active_accounting_type_rows(rows)
    for fragment in fragments:
        wanted = fragment.strip().lower()
        if not wanted:
            continue
        for row in candidates:
            name = str(row.get("name", "")).strip().lower()
            if wanted in name:
                return row
    return None


def _find_accounting_type_by_exact_names(
    rows: list[dict[str, Any]],
    names: list[str],
) -> dict[str, Any] | None:
    candidates = _active_accounting_type_rows(rows)
    wanted_names = [name.strip().lower() for name in names if name.strip()]
    for wanted in wanted_names:
        for row in candidates:
            name = str(row.get("name", "")).strip().lower()
            if name == wanted:
                return row
    return None


def _select_accounting_type_for_purchase_category(
    rows: list[dict[str, Any]],
    purchase_category: str | None,
) -> dict[str, Any] | None:
    normalized = str(purchase_category or "").strip().lower()
    if normalized == "sonstiges material":
        match = _find_accounting_type_by_exact_names(
            rows,
            ["Materialeinkauf"],
        )
        if match is not None:
            return match
        match = _find_accounting_type_by_name_fragments(
            rows,
            ["materialeinkauf", "material/waren", "material", "sonstiges"],
        )
        if match is not None:
            return match
    if normalized == "bürobedarf":
        match = _find_accounting_type_by_exact_names(
            rows,
            ["Büromaterial", "Buromaterial", "Office stationery"],
        )
        if match is not None:
            return match
        match = _find_accounting_type_by_name_fragments(
            rows,
            ["büromaterial", "buromaterial", "office stationery", "büro", "buero", "buro", "office", "sonstiges"],
        )
        if match is not None:
            return match
    return _find_accounting_type_by_name_fragments(rows, ["sonstiges"])


def _build_voucher_description(booking_row: dict[str, Any], extracted: dict[str, Any]) -> str:
    document_number = str(extracted.get("document_number") or "").strip()
    order_number = _format_amazon_payment_row(booking_row).get("orderNumber") or ""
    if document_number:
        return document_number
    if order_number:
        return order_number
    return f"Amazon-Beleg-{booking_row.get('id', '-')}"


def _determine_supplier_name(booking_row: dict[str, Any], extracted: dict[str, Any]) -> str:
    if extracted.get("intra_community_supply") is not True:
        return AMAZON_DEFAULT_CUSTOMER_NAME
    seller_name = str(extracted.get("seller_name") or "").strip()
    if seller_name:
        return seller_name
    payee_name = str(booking_row.get("payeePayerName") or "").strip()
    if payee_name:
        return payee_name
    return "Unbekannter Lieferant"


def _select_tax_rule_for_extraction(extracted: dict[str, Any]) -> dict[str, Any]:
    if extracted.get("intra_community_supply") is True:
        return dict(SEVDESK_TAX_RULE_INNER_COMMUNITY_EXPENSE)
    return dict(SEVDESK_TAX_RULE_DEFAULT_TAXABLE_EXPENSE)


def _build_amazon_voucher_payload(
    *,
    booking_row: dict[str, Any],
    extracted: dict[str, Any],
    pdf_path: str,
    accounting_type_rows: list[dict[str, Any]],
    check_account_rows: list[dict[str, Any]],
    customer_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    booking_date = _parse_transaction_date(booking_row)
    invoice_date = _parse_iso_date(extracted.get("invoice_date")) or booking_date or date.today()
    payment_deadline = invoice_date + timedelta(days=14)
    gross_amount = _parse_amount_value(extracted.get("amount")) or 0.0
    tax_rate_percent = _parse_amount_value(extracted.get("vat_rate_percent")) or 0.0
    sum_net = _compute_sum_net(gross_amount, tax_rate_percent)
    purchase_category = extracted.get("purchase_category")
    selected_accounting_type = _select_accounting_type_for_purchase_category(
        accounting_type_rows,
        purchase_category,
    )
    sparkasse_account = _find_check_account_by_name(check_account_rows, SPARKASSE_NAME_FRAGMENT)
    payload = default_create_template(
        default_buchunggskonto=selected_accounting_type,
        default_zahlungskonto=sparkasse_account,
    )

    voucher = payload["voucher"]
    voucher["voucherDate"] = _format_sevdesk_date(invoice_date)
    voucher["deliveryDate"] = _format_sevdesk_date(invoice_date)
    voucher["paymentDeadline"] = _format_sevdesk_date(payment_deadline)
    voucher["description"] = _build_voucher_description(booking_row, extracted)
    voucher["supplierName"] = _determine_supplier_name(booking_row, extracted)
    voucher["taxRule"] = _select_tax_rule_for_extraction(extracted)
    voucher["document"] = None
    if isinstance(customer_row, dict):
        _apply_customer_to_voucher_payload(payload, customer_row)

    position = payload["voucherPosSave"][0]
    position["net"] = False
    position["taxRate"] = float(tax_rate_percent)
    position["sumGross"] = gross_amount
    if sum_net is not None:
        position["sumNet"] = sum_net
    position["comment"] = (
        f"Amazon {purchase_category}" if purchase_category else "Amazon Beleg"
    )

    payload["notes"] = {
        **payload.get("notes", {}),
        "amazon_receipt_match": {
            "booking_id": str(booking_row.get("id", "")),
            "booking_date": booking_date.isoformat() if booking_date else None,
            "booking_amount": _parse_amount_value(booking_row.get("amount")),
            "order_number": _format_amazon_payment_row(booking_row).get("orderNumber") or None,
            "matched_pdf_path": pdf_path,
            "match_rule": (
                f"amount exact and booking date within {AMAZON_BOOKING_MATCH_MAX_DELAY_DAYS} "
                "days after receipt date"
            ),
        },
        "extracted_pdf_fields": {
            "document_number": extracted.get("document_number"),
            "seller_name": extracted.get("seller_name"),
            "invoice_date": extracted.get("invoice_date"),
            "amount": _parse_amount_value(extracted.get("amount")),
            "vat_rate_percent": _parse_amount_value(extracted.get("vat_rate_percent")),
            "seller_vat_id": extracted.get("seller_vat_id"),
            "intra_community_supply": extracted.get("intra_community_supply"),
            "purchase_category": purchase_category,
            "notes": extracted.get("notes"),
        },
        "selected_tax_rule": voucher.get("taxRule"),
        "generated_by": "pages/8_Accounting.py",
    }
    return normalize_create_payload(payload)


def _build_voucher_output_path(
    booking_row: dict[str, Any],
    extracted: dict[str, Any],
    pdf_path: str,
) -> Path:
    booking_id = _safe_filename_token(booking_row.get("id"))
    descriptor = _safe_filename_token(
        extracted.get("document_number") or _format_amazon_payment_row(booking_row).get("orderNumber")
    )
    pdf_descriptor = _safe_filename_token(Path(pdf_path).stem)
    return AMAZON_VOUCHER_OUTPUT_DIR / f"amazon_voucher_{booking_id}_{descriptor}_{pdf_descriptor}.json"


def _build_voucher_payload_entries(
    *,
    booking_row: dict[str, Any],
    extraction_results: list[dict[str, Any]],
    accounting_type_rows: list[dict[str, Any]],
    check_account_rows: list[dict[str, Any]],
    customer_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    known_accounting_type_ids = {
        str(row.get("id"))
        for row in accounting_type_rows
        if row.get("id") is not None
    }
    entries: list[dict[str, Any]] = []
    for extraction_result in extraction_results:
        pdf_path = str(extraction_result.get("pdfPath", "")).strip()
        extracted = extraction_result.get("extracted")
        if not pdf_path or not isinstance(extracted, dict):
            continue
        is_intra_community_supply = extracted.get("intra_community_supply") is True
        if is_intra_community_supply:
            matched_customer = _find_customer_by_vat_id(customer_rows, extracted.get("seller_vat_id"))
        else:
            matched_customer = _find_customer_by_name(customer_rows, AMAZON_DEFAULT_CUSTOMER_NAME)
        voucher_payload = _build_amazon_voucher_payload(
            booking_row=booking_row,
            extracted=extracted,
            pdf_path=pdf_path,
            accounting_type_rows=accounting_type_rows,
            check_account_rows=check_account_rows,
            customer_row=matched_customer,
        )
        validation_errors = validate_create_payload(
            voucher_payload,
            known_accounting_type_ids,
        )
        voucher_path = _build_voucher_output_path(
            booking_row,
            extracted,
            pdf_path,
        )
        write_json(voucher_path, voucher_payload)
        entries.append(
            {
                "matchedPdfPath": pdf_path,
                "path": str(voucher_path),
                "payload": voucher_payload,
                "sellerName": str(extracted.get("seller_name", "")).strip(),
                "sellerVatId": str(extracted.get("seller_vat_id", "")).strip(),
                "isIntraCommunitySupply": is_intra_community_supply,
                "customer": matched_customer,
                "validationErrors": validation_errors,
                "createResponse": None,
                "createCustomerResponse": None,
                "createdCustomer": None,
                "createdVoucher": None,
            }
        )
    return entries


def _export_check_accounts(base_url: str, token: str) -> list[dict[str, Any]]:
    rows = fetch_all_check_accounts(base_url, token, 1000, "id")
    essential_rows = [_format_check_account_row(row) for row in rows]
    payload = {
        "informationsart": "checkaccounts",
        "quelle": "sevdesk",
        "quelle_endpoint": "/CheckAccount",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "anzahl": len(essential_rows),
        "feldschema": list(essential_rows[0].keys()) if essential_rows else [],
        "daten": essential_rows,
    }
    write_json(CHECK_ACCOUNTS_EXPORT_PATH, payload)
    return essential_rows


def _export_accounting_types(base_url: str, token: str) -> list[dict[str, Any]]:
    rows = fetch_all_accounting_types(base_url, token, 1000, "id")
    essential_rows = [_format_accounting_type_row(row) for row in rows]
    payload = {
        "informationsart": "accounting_types",
        "quelle": "sevdesk",
        "quelle_endpoint": "/AccountingType",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "anzahl": len(essential_rows),
        "feldschema": list(essential_rows[0].keys()) if essential_rows else [],
        "daten": essential_rows,
    }
    write_json(ACCOUNTING_TYPES_EXPORT_PATH, payload)
    return essential_rows


def _export_tax_rules(base_url: str, token: str) -> list[dict[str, Any]]:
    rows = fetch_all_tax_rules(base_url, token, 1000, "id")
    essential_rows = [_format_tax_rule_row(row) for row in rows]
    payload = {
        "informationsart": "tax_rules",
        "quelle": "sevdesk",
        "quelle_endpoint": "/TaxRule",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "anzahl": len(essential_rows),
        "feldschema": list(essential_rows[0].keys()) if essential_rows else [],
        "daten": essential_rows,
    }
    write_json(TAX_RULES_EXPORT_PATH, payload)
    return essential_rows


def _load_stored_check_accounts() -> list[dict[str, Any]]:
    return load_rows(CHECK_ACCOUNTS_EXPORT_PATH)


def _load_stored_accounting_types() -> list[dict[str, Any]]:
    return load_rows(ACCOUNTING_TYPES_EXPORT_PATH)


def _load_stored_tax_rules() -> list[dict[str, Any]]:
    return load_rows(TAX_RULES_EXPORT_PATH)


def _load_json_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _show_vouchers(rows: list[dict[str, Any]] | None) -> None:
    if rows is None:
        st.caption("Load the latest sevDesk Belege to inspect them here.")
        return
    if not rows:
        st.info("No Belege found.")
        return
    st.success(f"Loaded {len(rows)} Belege.")
    st.dataframe(pd.DataFrame([_format_voucher_row(row) for row in rows]), width="stretch")
    with st.expander("Raw API response"):
        st.json(rows)


def _show_accounting_types(rows: list[dict[str, Any]] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk accounting types to inspect them here.")
        return
    if not rows:
        st.info("No accounting types stored yet.")
        return
    st.success(f"Stored {len(rows)} accounting types in `{ACCOUNTING_TYPES_EXPORT_PATH}`.")


def _show_check_accounts(rows: list[dict[str, Any]] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk check accounts to inspect them here.")
        return
    if not rows:
        st.info("No check accounts stored yet.")
        return
    st.success(f"Stored {len(rows)} check accounts in `{CHECK_ACCOUNTS_EXPORT_PATH}`.")


def _show_tax_rules(rows: list[dict[str, Any]] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk tax rules to inspect them here.")
        return
    if not rows:
        st.info("No tax rules stored yet.")
        return
    st.success(f"Stored {len(rows)} tax rules in `{TAX_RULES_EXPORT_PATH}`.")


def _show_amazon_customers(rows: list[dict[str, Any]] | None) -> None:
    if rows is None:
        st.caption("Amazon customers are loaded live from sevDesk when the page starts.")
        return
    if not rows:
        st.info("No sevDesk customers with `Amazon` in the name were found.")
        return
    st.success(f"Loaded {len(rows)} live sevDesk customer entries with `Amazon` in the name.")
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _show_transactions(rows: list[dict[str, Any]] | None) -> None:
    if rows is None:
        st.caption("Select a stored check account and load its latest bookings.")
        return
    if not rows:
        st.info("No bookings found for the selected check account.")
        return
    st.success(f"Loaded {len(rows)} bookings.")
    st.dataframe(pd.DataFrame([_format_transaction_row(row) for row in rows]), width="stretch")
    with st.expander("Raw API response"):
        st.json(rows)


def _show_amazon_payments(rows: list[dict[str, Any]] | None) -> None:
    if rows is None:
        st.caption("Load Sparkasse bookings filtered for Amazon Payments Europe here.")
        return
    if not rows:
        st.info("No matching Amazon Payments Europe bookings found in the Sparkasse account.")
        return
    st.success(f"Loaded {len(rows)} matching Sparkasse bookings.")
    st.dataframe(pd.DataFrame([_format_amazon_payment_row(row) for row in rows]), width="stretch")
    with st.expander("Raw API response"):
        st.json(rows)


def _build_amazon_selection_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    selection_rows: list[dict[str, Any]] = []
    for row in rows:
        formatted = _format_amazon_payment_row(row)
        selection_rows.append(
            {
                "selected": False,
                "id": formatted["id"],
                "valueDate": formatted["valueDate"],
                "amount": formatted["amount"],
                "status": formatted["status"],
                "statusMeaning": formatted["statusMeaning"],
                "orderNumber": formatted["orderNumber"],
                "payeePayerName": formatted["payeePayerName"],
                "paymtPurpose": formatted["paymtPurpose"],
            }
        )
    return pd.DataFrame(selection_rows)


def _find_receipt_pdfs(order_number: str) -> list[str]:
    if not order_number or not AMAZON_RECEIPTS_DIR.exists():
        return []

    order_dir = AMAZON_RECEIPTS_DIR / order_number
    if order_dir.is_dir():
        matches = sorted(
            str(path)
            for path in order_dir.rglob("*.pdf")
            if path.is_file() and path.stem != order_number
        )
        return matches

    matches = sorted(
        str(path)
        for path in AMAZON_RECEIPTS_DIR.rglob("*.pdf")
        if order_number in path.name and path.stem != order_number
    )
    return matches


def _build_selected_pdf_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in rows:
        formatted = _format_amazon_payment_row(row)
        pdf_matches = _find_receipt_pdfs(formatted["orderNumber"])
        matches.append(
            {
                "id": formatted["id"],
                "valueDate": formatted["valueDate"],
                "amount": formatted["amount"],
                "status": formatted["status"],
                "statusMeaning": formatted["statusMeaning"],
                "orderNumber": formatted["orderNumber"],
                "pdfCount": len(pdf_matches),
                "pdfPaths": pdf_matches,
            }
        )
    return matches


def _extract_accounting_data_from_pdf(
    *,
    pdf_path: str,
    provider: str,
    model_name: str,
    api_key: str,
) -> dict[str, Any]:
    pdf_file = Path(pdf_path)
    pdf_bytes = pdf_file.read_bytes()
    page_images = split_pdf_bytes_to_page_images(
        pdf_bytes,
        pdf_name=pdf_file.name,
        dpi=180,
        grayscale=False,
        max_image_bytes=1_500_000,
    )
    if not page_images:
        raise RuntimeError(f"PDF enthaelt keine verarbeitbaren Seiten: {pdf_file}")
    user_content = build_document_user_content(page_images)
    extracted = extract_amazon_accounting_data(
        provider=provider,
        api_key=api_key,
        model_name=model_name,
        user_content=user_content,
        system_prompt_base=DEFAULT_SYSTEM_PROMPT,
    )
    return {
        "pdfPath": str(pdf_file),
        "provider": provider,
        "model": model_name,
        "pageCount": len(page_images),
        "extracted": extracted,
    }


def _build_accounting_comparison_rows(
    booking_row: dict[str, Any],
    extracted: dict[str, Any],
) -> list[dict[str, Any]]:
    formatted_booking = _format_amazon_payment_row(booking_row)
    booking_date = _parse_transaction_date(booking_row)
    return [
        {
            "field": "Betrag",
            "booking": _format_currency_value(formatted_booking.get("amount")),
            "pdf": _format_currency_value(extracted.get("amount")),
            "match": _format_match_value(
                _compare_amounts(formatted_booking.get("amount"), extracted.get("amount"))
            ),
        },
        {
            "field": "Datum",
            "booking": booking_date.isoformat() if booking_date else "-",
            "pdf": extracted.get("invoice_date") or "-",
            "match": _format_match_value(
                _compare_booking_after_receipt_window(booking_date, extracted.get("invoice_date"))
            ),
        },
    ]


def _sum_extracted_pdf_amounts(extraction_results: list[dict[str, Any]]) -> float | None:
    if not extraction_results:
        return None
    amounts: list[float] = []
    for extraction_result in extraction_results:
        extracted = extraction_result.get("extracted")
        if not isinstance(extracted, dict):
            return None
        amount = _parse_amount_value(extracted.get("amount"))
        if amount is None:
            return None
        amounts.append(amount)
    return round(sum(amounts), 2)


def _aggregate_booking_receipt_match(
    booking_row: dict[str, Any],
    extraction_results: list[dict[str, Any]],
) -> bool | None:
    summed_amount = _sum_extracted_pdf_amounts(extraction_results)
    return _compare_amounts(booking_row.get("amount"), summed_amount)


def _build_aggregate_accounting_comparison_rows(
    booking_row: dict[str, Any],
    extraction_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    summed_amount = _sum_extracted_pdf_amounts(extraction_results)
    return [
        {
            "field": "Betrag (Summe PDFs)",
            "booking": _format_currency_value(booking_row.get("amount")),
            "pdf": _format_currency_value(summed_amount),
            "match": _format_match_value(_compare_amounts(booking_row.get("amount"), summed_amount)),
        },
        {
            "field": "Anzahl PDFs",
            "booking": "-",
            "pdf": str(len(extraction_results)),
            "match": "-",
        },
    ]


def _build_extracted_accounting_rows(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    vat_rate = extracted.get("vat_rate_percent")
    return [
        {"field": "Verkäufer", "value": extracted.get("seller_name") or "-"},
        {"field": "Betrag", "value": _format_currency_value(extracted.get("amount"))},
        {"field": "Umsatzsteuer %", "value": f"{vat_rate}%" if vat_rate is not None else "-"},
        {"field": "USt-IdNr. Verkäufer", "value": extracted.get("seller_vat_id") or "-"},
        {
            "field": "Innergemeinschaftliche Lieferung",
            "value": _format_bool_value(extracted.get("intra_community_supply")),
        },
        {"field": "Einkaufskategorie", "value": extracted.get("purchase_category") or "-"},
        {"field": "Belegnummer", "value": extracted.get("document_number") or "-"},
        {"field": "Rechnungsdatum", "value": extracted.get("invoice_date") or "-"},
        {"field": "Hinweis", "value": extracted.get("notes") or "-"},
    ]


def _render_pdf_inline(path_str: str, *, height: int = 420) -> None:
    path = Path(path_str)
    if not path.exists():
        _report_error(f"PDF not found: {path}")
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


def _show_downloaded_payload(title: str, path: Path) -> None:
    st.markdown(f"**{title}**")
    st.caption(f"`{path}`")
    payload = _load_json_payload(path)
    if payload is None:
        st.info("No downloaded data file found.")
        return
    st.json(payload)


st.subheader("Connection")
st.code(_base_url())
st.caption(
    "Master data paths:"
    f" `{ACCOUNTING_TYPES_EXPORT_PATH}`"
    f" , `{CHECK_ACCOUNTS_EXPORT_PATH}`"
    f" and `{TAX_RULES_EXPORT_PATH}`"
)

if AMAZON_CUSTOMERS_SESSION_KEY not in st.session_state:
    _refresh_live_amazon_customers()

tab1, tab2, tab3 = st.tabs(["Overview", "Operations", "Master Data"])

with tab1:
    st.caption("Reserved for later.")

with tab2:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Latest Belege")
        with st.form("sevdesk_latest_belege_form"):
            latest_limit = st.number_input("Voucher limit", min_value=1, max_value=100, value=10, step=1)
            latest_submit = st.form_submit_button("Load latest Belege", width="stretch")

        if latest_submit:
            token = _ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_latest_belege_rows"] = request_vouchers(
                        _base_url(),
                        token,
                        int(latest_limit),
                    )
                except Exception as exc:
                    _report_error(
                        f"Failed to load latest Belege: {exc}",
                        log_message="Failed to load latest Belege",
                        exc_info=True,
                    )

        _show_vouchers(st.session_state.get("sevdesk_latest_belege_rows"))

    with col2:
        st.subheader("Bookings by Check Account")
        check_accounts_for_selection = st.session_state.get("sevdesk_check_accounts_rows")
        if check_accounts_for_selection is None:
            check_accounts_for_selection = _load_stored_check_accounts()

        if check_accounts_for_selection:
            account_options = {
                f"{row.get('name', 'Unnamed')} ({row.get('id', '-')})": str(row.get("id", ""))
                for row in check_accounts_for_selection
            }
            selected_account_label = st.selectbox(
                "Check account",
                options=list(account_options.keys()),
            )
            transactions_limit = st.slider("Number of bookings", min_value=1, max_value=200, value=25)
            if st.button("Load latest bookings", width="stretch"):
                token = _ensure_token()
                if token:
                    try:
                        st.session_state["sevdesk_check_account_transactions_rows"] = (
                            fetch_latest_transactions_for_check_account(
                                _base_url(),
                                token,
                                account_options[selected_account_label],
                                transactions_limit,
                            )
                        )
                    except Exception as exc:
                        _report_error(
                            f"Failed to load bookings: {exc}",
                            log_message="Failed to load bookings",
                            exc_info=True,
                        )
        else:
            st.info("Fetch check accounts in the Master Data tab first so you can choose one here.")

        _show_transactions(st.session_state.get("sevdesk_check_account_transactions_rows"))

    st.divider()
    st.subheader("Sparkasse Amazon Payments")
    st.caption(
        "Filters Sparkasse bookings where `payeePayerName` contains "
        f"`{AMAZON_PAYEE_NAME}`."
    )
    amazon_customers = st.session_state.get(AMAZON_CUSTOMERS_SESSION_KEY)
    st.markdown("**Amazon Customers**")
    _show_amazon_customers(amazon_customers)
    llm_provider = st.selectbox(
        "LLM Provider",
        [LLM_PROVIDER_OPENAI, LLM_PROVIDER_GOOGLE],
        index=0,
        key="sevdesk_sparkasse_amazon_llm_provider",
        help="Wähle den LLM-Anbieter für die Beleganalyse.",
    )
    extract_model = st.selectbox(
        "Extraktionsmodell (API)",
        LLM_MODELS_BY_PROVIDER.get(llm_provider, LLM_MODELS_BY_PROVIDER[LLM_PROVIDER_OPENAI]),
        index=0,
        key="sevdesk_sparkasse_amazon_llm_model",
        help="Dieses Modell wird verwendet, um PDF-Seiten strukturiert auszulesen.",
    )
    default_end_date = date.today()
    default_start_date = default_end_date - timedelta(days=30)
    amazon_start_date = st.date_input("Start date", value=default_start_date, key="amazon_start_date")
    amazon_end_date = st.date_input("End date", value=default_end_date, key="amazon_end_date")
    invalid_amazon_date_range = amazon_start_date > amazon_end_date
    if invalid_amazon_date_range:
        _report_error("Start date must be before or equal to end date.")

    if st.button("Load Sparkasse Amazon Payments", width="stretch", disabled=invalid_amazon_date_range):
        token = _ensure_token()
        if token:
            stored_check_accounts = st.session_state.get("sevdesk_check_accounts_rows")
            if stored_check_accounts is None:
                stored_check_accounts = _load_stored_check_accounts()
            if not stored_check_accounts:
                _report_error("No stored check accounts found. Fetch them first in the Master Data tab.")
            else:
                sparkasse_account = _find_check_account_by_name(
                    stored_check_accounts,
                    SPARKASSE_NAME_FRAGMENT,
                )
                if sparkasse_account is None:
                    _report_error("No check account containing `Sparkasse` was found in stored master data.")
                else:
                    try:
                        _clear_amazon_analysis_state()
                        rows = fetch_all_transactions_for_check_account(
                            _base_url(),
                            token,
                            str(sparkasse_account.get("id", "")),
                        )
                        rows = _filter_rows_by_date_range(rows, amazon_start_date, amazon_end_date)
                        filtered_rows = [
                            row
                            for row in rows
                            if AMAZON_PAYEE_NAME in str(row.get("payeePayerName", ""))
                        ]
                        st.session_state["sevdesk_sparkasse_amazon_rows"] = filtered_rows
                    except Exception as exc:
                        _report_error(
                            f"Failed to load Sparkasse Amazon payments: {exc}",
                            log_message="Failed to load Sparkasse Amazon payments",
                            exc_info=True,
                        )

    amazon_rows = st.session_state.get("sevdesk_sparkasse_amazon_rows")
    if amazon_rows:
        available_statuses = sorted({str(row.get("status", "")) for row in amazon_rows})
        status_options = {_format_status_option(status): status for status in available_statuses}
        selected_statuses = st.multiselect(
            "Status filter",
            options=list(status_options.keys()),
            default=list(status_options.keys()),
            key="sevdesk_sparkasse_amazon_status_filter",
        )
        amazon_rows = [
            row
            for row in amazon_rows
            if str(row.get("status", "")) in {status_options[label] for label in selected_statuses}
        ]
    if amazon_rows is None:
        _show_amazon_payments(None)
    elif not amazon_rows:
        _show_amazon_payments([])
    else:
        st.success(f"Loaded {len(amazon_rows)} matching Sparkasse bookings.")
        selection_df = _build_amazon_selection_dataframe(amazon_rows)
        edited_selection_df = st.data_editor(
            selection_df,
            width="stretch",
            hide_index=True,
            disabled=[
                "id",
                "valueDate",
                "amount",
                "status",
                "statusMeaning",
                "orderNumber",
                "payeePayerName",
                "paymtPurpose",
            ],
            column_config={
                "selected": st.column_config.CheckboxColumn("Select"),
                "orderNumber": st.column_config.TextColumn("Order Number"),
            },
            key="sevdesk_sparkasse_amazon_selection_table",
        )
        selected_booking_ids = set(
            edited_selection_df.loc[edited_selection_df["selected"], "id"].astype(str).tolist()
        )
        selected_booking_rows = [
            row for row in amazon_rows if str(row.get("id", "")) in selected_booking_ids
        ]

        if st.button("Identify PDFs for selected bookings", width="stretch"):
            _clear_amazon_analysis_state()
            if len(selected_booking_rows) != 1:
                _report_error("Select exactly one booking before identifying PDFs.")
            else:
                selected_booking = selected_booking_rows[0]
                pdf_matches = _build_selected_pdf_matches([selected_booking])
                st.session_state["sevdesk_sparkasse_amazon_pdf_matches"] = pdf_matches
                if not pdf_matches or pdf_matches[0]["pdfCount"] == 0:
                    st.warning("No matching receipt PDF was found for the selected booking.")
                else:
                    api_key = resolve_llm_api_key(
                        llm_provider,
                        session_state=st.session_state,
                        secrets=st.secrets,
                        environ=os.environ,
                    )
                    if not api_key:
                        st.warning(
                            f"{llm_provider}-API Key nicht gefunden. "
                            "PDF wurde identifiziert, LLM-Extraktion wurde uebersprungen."
                        )
                    else:
                        matched_pdf_paths = pdf_matches[0]["pdfPaths"]
                        with st.spinner(f"Analysing {len(matched_pdf_paths)} PDF(s) with LLM..."):
                            try:
                                extraction_results: list[dict[str, Any]] = []
                                for matched_pdf_path in matched_pdf_paths:
                                    logger.info(
                                        "Amazon accounting extraction requested booking_id=%s pdf=%s provider=%s model=%s",
                                        selected_booking.get("id"),
                                        matched_pdf_path,
                                        llm_provider,
                                        extract_model,
                                    )
                                    extraction_result = _extract_accounting_data_from_pdf(
                                        pdf_path=matched_pdf_path,
                                        provider=llm_provider,
                                        model_name=extract_model,
                                        api_key=api_key,
                                    )
                                    extraction_result["bookingId"] = str(selected_booking.get("id", ""))
                                    extraction_result["comparison"] = _build_accounting_comparison_rows(
                                        selected_booking,
                                        extraction_result["extracted"],
                                    )
                                    extraction_results.append(extraction_result)

                                aggregate_match = _aggregate_booking_receipt_match(
                                    selected_booking,
                                    extraction_results,
                                )
                                aggregate_result = {
                                    "bookingId": str(selected_booking.get("id", "")),
                                    "provider": llm_provider,
                                    "model": extract_model,
                                    "pdfExtractions": extraction_results,
                                    "comparison": _build_aggregate_accounting_comparison_rows(
                                        selected_booking,
                                        extraction_results,
                                    ),
                                    "sumExtractedAmount": _sum_extracted_pdf_amounts(extraction_results),
                                    "aggregateMatch": aggregate_match,
                                }
                                accounting_type_rows = st.session_state.get("sevdesk_accounting_types_rows")
                                if accounting_type_rows is None:
                                    accounting_type_rows = _load_stored_accounting_types()
                                check_account_rows = st.session_state.get("sevdesk_check_accounts_rows")
                                if check_account_rows is None:
                                    check_account_rows = _load_stored_check_accounts()
                                customer_rows = st.session_state.get(AMAZON_CUSTOMERS_SESSION_KEY)
                                if customer_rows is None:
                                    customer_rows = _refresh_live_amazon_customers(report_errors=True)
                                voucher_entries = _build_voucher_payload_entries(
                                    booking_row=selected_booking,
                                    extraction_results=extraction_results,
                                    accounting_type_rows=accounting_type_rows,
                                    check_account_rows=check_account_rows,
                                    customer_rows=customer_rows or [],
                                )
                                if voucher_entries:
                                    st.session_state["sevdesk_sparkasse_amazon_voucher_payload"] = {
                                        "bookingId": str(selected_booking.get("id", "")),
                                        "aggregateMatch": aggregate_match,
                                        "entries": voucher_entries,
                                    }
                                else:
                                    st.session_state.pop("sevdesk_sparkasse_amazon_voucher_payload", None)
                                st.session_state["sevdesk_sparkasse_amazon_llm_result"] = aggregate_result
                            except Exception as exc:
                                _report_error(
                                    f"LLM extraction failed: {exc}",
                                    log_message=(
                                        "Amazon accounting extraction failed "
                                        f"booking_id={selected_booking.get('id')}"
                                    ),
                                    exc_info=True,
                                )

        pdf_matches = st.session_state.get("sevdesk_sparkasse_amazon_pdf_matches")
        if pdf_matches:
            st.subheader("PDF Matches")
            st.dataframe(pd.DataFrame(pdf_matches), width="stretch")
            for match in pdf_matches:
                st.markdown(
                    f"**Booking {match['id']} | Order {match['orderNumber']} | PDFs: {match['pdfCount']}**"
                )
                if not match["pdfPaths"]:
                    st.info("No PDF found for this booking.")
                    continue
                for pdf_path in match["pdfPaths"]:
                    st.caption(pdf_path)

        llm_result = st.session_state.get("sevdesk_sparkasse_amazon_llm_result")
        if (
            llm_result
            and len(selected_booking_rows) == 1
            and llm_result.get("bookingId") == str(selected_booking_rows[0].get("id", ""))
        ):
            pdf_extractions = llm_result.get("pdfExtractions")
            if not isinstance(pdf_extractions, list):
                pdf_extractions = [llm_result] if isinstance(llm_result.get("extracted"), dict) else []
            aggregate_match = llm_result.get("aggregateMatch")
            st.subheader("Booking Comparison")
            if aggregate_match is True:
                st.success(
                    "Match: Die Summe der extrahierten PDF-Betraege stimmt mit der Buchung ueberein."
                )
            elif aggregate_match is False:
                st.warning("Die Summe der extrahierten PDF-Betraege stimmt nicht mit der Buchung ueberein.")
            st.dataframe(pd.DataFrame(llm_result.get("comparison", [])), width="stretch")
            st.subheader("Extracted Accounting Data")
            st.caption(
                "LLM results for all matched PDFs of the currently selected booking. "
                f"Model: `{llm_result.get('provider')}` / `{llm_result.get('model')}`"
            )
            for index, extraction_entry in enumerate(pdf_extractions, start=1):
                extracted = extraction_entry.get("extracted", {})
                pdf_path = str(extraction_entry.get("pdfPath", "")).strip()
                pdf_label = Path(pdf_path).name if pdf_path else f"PDF {index}"
                st.markdown(f"**PDF {index} | {pdf_label}**")
                pdf_col, extracted_col = st.columns(2)
                with pdf_col:
                    if pdf_path:
                        st.caption(pdf_path)
                        _render_pdf_inline(pdf_path, height=520)
                    else:
                        st.info("No PDF path available.")
                with extracted_col:
                    st.dataframe(pd.DataFrame(_build_extracted_accounting_rows(extracted)), width="stretch")
                with st.expander(f"Raw extraction payload #{index}"):
                    st.json(extracted)

        voucher_payload_state = st.session_state.get("sevdesk_sparkasse_amazon_voucher_payload")
        if (
            voucher_payload_state
            and len(selected_booking_rows) == 1
            and voucher_payload_state.get("bookingId") == str(selected_booking_rows[0].get("id", ""))
        ):
            voucher_entries = voucher_payload_state.get("entries")
            if not isinstance(voucher_entries, list):
                legacy_payload = voucher_payload_state.get("payload")
                voucher_entries = (
                    [
                        {
                            "matchedPdfPath": voucher_payload_state.get("matchedPdfPath"),
                            "path": voucher_payload_state.get("path"),
                            "payload": legacy_payload,
                            "sellerName": "",
                            "sellerVatId": "",
                            "isIntraCommunitySupply": False,
                            "customer": None,
                            "validationErrors": voucher_payload_state.get("validationErrors", []),
                            "createCustomerResponse": None,
                            "createdCustomer": None,
                            "createResponse": voucher_payload_state.get("createResponse"),
                            "createdVoucher": voucher_payload_state.get("createdVoucher"),
                        }
                    ]
                    if isinstance(legacy_payload, dict)
                    else []
                )
            aggregate_match_for_create = voucher_payload_state.get("aggregateMatch")
            st.subheader("Generated Voucher JSON")
            if aggregate_match_for_create is True:
                st.success("A voucher JSON was generated for each matched PDF.")
            elif aggregate_match_for_create is False:
                st.warning(
                    "Voucher JSON files were generated for each matched PDF, "
                    "but API creation is disabled until the summed PDF amount matches the booking."
                )
            else:
                st.info("Voucher JSON files were generated.")
            for entry_index, voucher_entry in enumerate(voucher_entries, start=1):
                matched_pdf_path_for_create = str(
                    voucher_entry.get("matchedPdfPath")
                    or (llm_result.get("pdfPath") if isinstance(llm_result, dict) else "")
                    or ""
                ).strip()
                pdf_label = (
                    Path(matched_pdf_path_for_create).name
                    if matched_pdf_path_for_create
                    else f"PDF {entry_index}"
                )
                validation_errors = voucher_entry.get("validationErrors", [])
                seller_name = str(voucher_entry.get("sellerName", "")).strip()
                seller_vat_id = str(voucher_entry.get("sellerVatId", "")).strip()
                is_intra_community_supply = voucher_entry.get("isIntraCommunitySupply") is True
                matched_customer = voucher_entry.get("customer")
                created_customer = voucher_entry.get("createdCustomer")
                created_voucher = voucher_entry.get("createdVoucher")
                create_customer_response = voucher_entry.get("createCustomerResponse")
                create_response = voucher_entry.get("createResponse")
                st.markdown(f"**Voucher JSON {entry_index} | {pdf_label}**")
                st.caption(f"Saved to `{voucher_entry.get('path')}`")
                if matched_pdf_path_for_create:
                    st.caption(f"Matched PDF: `{matched_pdf_path_for_create}`")
                if seller_vat_id:
                    st.caption(f"Extracted USt-IdNr.: `{seller_vat_id}`")
                if isinstance(matched_customer, dict):
                    customer_name = _format_customer_display_name(matched_customer)
                    if is_intra_community_supply:
                        st.success(
                            "Customer matched by USt-IdNr.: "
                            f"`{customer_name}` ({matched_customer.get('id', '-')})"
                        )
                    else:
                        st.success(
                            "Customer fixed to non-innergemeinschaftliche default: "
                            f"`{customer_name}` ({matched_customer.get('id', '-')})"
                        )
                elif is_intra_community_supply and seller_vat_id:
                    st.warning("No existing sevDesk customer was found for the extracted USt-IdNr.")
                elif is_intra_community_supply:
                    st.info("No USt-IdNr. was extracted from this PDF, so no customer match is possible.")
                else:
                    st.warning(
                        f"No live sevDesk customer named `{AMAZON_DEFAULT_CUSTOMER_NAME}` was found. "
                        "The voucher JSON still uses that supplier name."
                    )
                if validation_errors:
                    st.warning("Voucher JSON was generated, but validation reported issues.")
                    for error in validation_errors:
                        st.write(f"- {error}")
                else:
                    st.success("Voucher JSON generated and validated successfully.")
                st.json(voucher_entry.get("payload", {}))
                if created_customer:
                    st.success(
                        "Customer was created in sevDesk: "
                        f"`{_format_customer_display_name(created_customer)}` ({created_customer.get('id', '-')})"
                    )
                    st.dataframe(pd.DataFrame([created_customer]), width="stretch", hide_index=True)
                elif is_intra_community_supply and (not isinstance(matched_customer, dict)) and seller_name and seller_vat_id:
                    if st.button(
                        "Create customer in sevDesk and update voucher JSON",
                        width="stretch",
                        key=(
                            "sevdesk_create_customer_"
                            f"{voucher_payload_state.get('bookingId', '')}_{entry_index}"
                        ),
                    ):
                        token = _ensure_token()
                        if token:
                            with st.spinner("Creating customer in sevDesk..."):
                                try:
                                    customer_rows = st.session_state.get(AMAZON_CUSTOMERS_SESSION_KEY)
                                    if customer_rows is None:
                                        customer_rows = _refresh_live_amazon_customers(
                                            token=token,
                                            report_errors=True,
                                        )
                                    customer_payload = _build_customer_create_payload(
                                        seller_name=seller_name,
                                        seller_vat_id=seller_vat_id,
                                        customer_rows=customer_rows or [],
                                    )
                                    customer_response = create_contact(
                                        _base_url(),
                                        token,
                                        customer_payload,
                                    )
                                    created_customer_row = _coerce_created_customer_row(
                                        customer_response,
                                        fallback_name=str(customer_payload.get("name", "")).strip(),
                                        fallback_vat_id=seller_vat_id,
                                        fallback_customer_number=str(
                                            customer_payload.get("customerNumber", "")
                                        ).strip(),
                                    )
                                    refreshed_customer_rows = _refresh_live_amazon_customers(
                                        token=token,
                                        report_errors=True,
                                    ) or []
                                    created_customer_row = (
                                        _find_customer_by_vat_id(refreshed_customer_rows, seller_vat_id)
                                        or _find_customer_by_name(
                                            refreshed_customer_rows,
                                            str(customer_payload.get("name", "")).strip(),
                                        )
                                        or created_customer_row
                                    )

                                    updated_entries: list[dict[str, Any]] = []
                                    for existing_entry in voucher_entries:
                                        entry_vat_id = existing_entry.get("sellerVatId")
                                        if _normalize_vat_id(entry_vat_id) == _normalize_vat_id(seller_vat_id):
                                            updated_entry = _persist_updated_voucher_entry(
                                                {
                                                    **existing_entry,
                                                    "createCustomerResponse": customer_response,
                                                    "createdCustomer": created_customer_row,
                                                },
                                                customer_row=created_customer_row,
                                            )
                                        else:
                                            updated_entry = existing_entry
                                        updated_entries.append(updated_entry)

                                    st.session_state["sevdesk_sparkasse_amazon_voucher_payload"] = {
                                        **voucher_payload_state,
                                        "entries": updated_entries,
                                    }
                                    st.rerun()
                                except Exception as exc:
                                    _report_error(
                                        f"Failed to create customer: {exc}",
                                        log_message="Failed to create customer in sevDesk",
                                        exc_info=True,
                                    )
                if create_customer_response:
                    with st.expander(f"Create customer API response #{entry_index}"):
                        st.json(create_customer_response)
                if created_voucher:
                    created_voucher_id = str(created_voucher.get("id", "")).strip() or "-"
                    st.success(f"Voucher was created in sevDesk with id `{created_voucher_id}`.")
                    st.dataframe(pd.DataFrame([_format_voucher_row(created_voucher)]), width="stretch")
                elif st.button(
                    "Create voucher via API for this PDF",
                    width="stretch",
                    disabled=bool(validation_errors) or aggregate_match_for_create is not True,
                    key=(
                        f"sevdesk_create_voucher_{voucher_payload_state.get('bookingId', '')}_{entry_index}"
                    ),
                ):
                    token = _ensure_token()
                    if token:
                        with st.spinner("Creating voucher in sevDesk..."):
                            request_payload: dict[str, Any] = {}
                            try:
                                request_payload = normalize_create_payload(
                                    voucher_entry.get("payload", {})
                                )
                                voucher = request_payload.get("voucher")
                                if isinstance(voucher, dict):
                                    voucher["document"] = None
                                if matched_pdf_path_for_create:
                                    remote_filename = upload_voucher_temp_file(
                                        _base_url(),
                                        token,
                                        matched_pdf_path_for_create,
                                    )
                                    request_payload["filename"] = remote_filename
                                else:
                                    request_payload["filename"] = None
                                response_payload = create_voucher(
                                    _base_url(),
                                    token,
                                    request_payload,
                                )
                                created_summary = first_object_from_response(response_payload) or {}
                                created_voucher_id = str(created_summary.get("id", "")).strip()
                                created_voucher = (
                                    request_voucher_by_id(_base_url(), token, created_voucher_id)
                                    if created_voucher_id
                                    else None
                                )
                                updated_entries = list(voucher_entries)
                                updated_entries[entry_index - 1] = {
                                    **voucher_entry,
                                    "matchedPdfPath": matched_pdf_path_for_create or None,
                                    "payload": request_payload,
                                    "createResponse": response_payload,
                                    "createdVoucher": created_voucher or created_summary,
                                }
                                st.session_state["sevdesk_sparkasse_amazon_voucher_payload"] = {
                                    **voucher_payload_state,
                                    "entries": updated_entries,
                                }
                                if created_voucher_id:
                                    st.session_state["sevdesk_latest_belege_rows"] = request_vouchers(
                                        _base_url(),
                                        token,
                                        10,
                                    )
                                st.success(
                                    "Voucher created successfully in sevDesk."
                                    + (f" New id: `{created_voucher_id}`." if created_voucher_id else "")
                                )
                            except Exception as exc:
                                logger.error(
                                    "Failed create-voucher payload: %s",
                                    json.dumps(request_payload, ensure_ascii=True, sort_keys=True),
                                )
                                _report_error(
                                    f"Failed to create voucher: {exc}",
                                    log_message="Failed to create voucher in sevDesk",
                                    exc_info=True,
                                )
                if create_response:
                    with st.expander(f"Create voucher API response #{entry_index}"):
                        st.json(create_response)

with tab3:
    col3, col4, col5 = st.columns(3)

    with col3:
        st.subheader("Accounting Types")
        if st.button("Fetch all accounting types and store master data", width="stretch"):
            token = _ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_accounting_types_rows"] = _export_accounting_types(
                        _base_url(),
                        token,
                    )
                except Exception as exc:
                    _report_error(
                        f"Failed to load accounting types: {exc}",
                        log_message="Failed to load accounting types",
                        exc_info=True,
                    )

        stored_accounting_types = st.session_state.get("sevdesk_accounting_types_rows")
        if stored_accounting_types is None:
            stored_accounting_types = _load_stored_accounting_types()
            if stored_accounting_types:
                st.session_state["sevdesk_accounting_types_rows"] = stored_accounting_types
        _show_accounting_types(stored_accounting_types)
        _show_downloaded_payload("Raw accounting types JSON", ACCOUNTING_TYPES_EXPORT_PATH)

    with col4:
        st.subheader("Check Accounts")
        if st.button("Fetch all check accounts and store master data", width="stretch"):
            token = _ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_check_accounts_rows"] = _export_check_accounts(
                        _base_url(),
                        token,
                    )
                except Exception as exc:
                    _report_error(
                        f"Failed to fetch check accounts: {exc}",
                        log_message="Failed to fetch check accounts",
                        exc_info=True,
                    )

        stored_check_accounts = st.session_state.get("sevdesk_check_accounts_rows")
        if stored_check_accounts is None:
            stored_check_accounts = _load_stored_check_accounts()
            if stored_check_accounts:
                st.session_state["sevdesk_check_accounts_rows"] = stored_check_accounts
        _show_check_accounts(stored_check_accounts)
        _show_downloaded_payload("Raw check accounts JSON", CHECK_ACCOUNTS_EXPORT_PATH)

    with col5:
        st.subheader("Tax Rules")
        if st.button("Fetch all tax rules and store master data", width="stretch"):
            token = _ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_tax_rules_rows"] = _export_tax_rules(
                        _base_url(),
                        token,
                    )
                except Exception as exc:
                    _report_error(
                        f"Failed to load tax rules: {exc}",
                        log_message="Failed to load tax rules",
                        exc_info=True,
                    )

        stored_tax_rules = st.session_state.get("sevdesk_tax_rules_rows")
        if stored_tax_rules is None:
            stored_tax_rules = _load_stored_tax_rules()
            if stored_tax_rules:
                st.session_state["sevdesk_tax_rules_rows"] = stored_tax_rules
        _show_tax_rules(stored_tax_rules)
        _show_downloaded_payload("Raw tax rules JSON", TAX_RULES_EXPORT_PATH)
