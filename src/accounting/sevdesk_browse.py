from typing import Any

from src.accounting.state import TRANSACTION_STATUS_LABELS
from src.sevdesk.voucher import format_amount, format_date, format_number, format_text


def _dedupe_texts(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    seen_values: set[str] = set()
    for value in values:
        cleaned_value = str(value).strip()
        if not cleaned_value or cleaned_value in seen_values:
            continue
        seen_values.add(cleaned_value)
        unique_values.append(cleaned_value)
    return unique_values


def _extract_voucher_tag_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return _dedupe_texts([value])
    if isinstance(value, list):
        collected_names: list[str] = []
        for item in value:
            collected_names.extend(_extract_voucher_tag_names(item))
        return _dedupe_texts(collected_names)
    if not isinstance(value, dict):
        return []

    collected_names: list[str] = []
    for key in ("tag", "tags", "objects", "object"):
        nested_value = value.get(key)
        if nested_value is not None:
            collected_names.extend(_extract_voucher_tag_names(nested_value))

    for key in ("name", "tagName", "label", "title", "text"):
        text_value = str(value.get(key, "")).strip()
        if text_value:
            collected_names.append(text_value)
            break

    return _dedupe_texts(collected_names)


def extract_voucher_tag_names(row: dict[str, Any]) -> list[str]:
    collected_names: list[str] = []
    for key in (
        "tags",
        "tag",
        "voucherTags",
        "taggings",
        "tagRelations",
        "tagRelation",
    ):
        value = row.get(key)
        if value is not None:
            collected_names.extend(_extract_voucher_tag_names(value))
    return _dedupe_texts(collected_names)


def _contact_display_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""

    organization_name = str(value.get("name", "")).strip()
    if organization_name:
        return organization_name

    person_name = " ".join(
        part
        for part in (
            str(value.get("surename", "")).strip(),
            str(value.get("familyname", "")).strip(),
        )
        if part
    ).strip()
    if person_name:
        return person_name

    return str(value.get("customerNumber", "")).strip()


def _voucher_contact_name(row: dict[str, Any]) -> str:
    supplier_name = str(row.get("supplierName", "")).strip()
    if supplier_name:
        return supplier_name

    for key in ("supplier", "contact"):
        contact_name = _contact_display_name(row.get(key))
        if contact_name:
            return contact_name

    return ""


def format_voucher_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "nummer": format_number(row),
        "datum": format_date(row),
        "betrag": format_amount(row),
        "beschreibung": format_text(row),
        "lieferant": _voucher_contact_name(row) or "-",
        "status": row.get("status", "-"),
        "tags": ", ".join(extract_voucher_tag_names(row)) or "-",
    }


def format_latest_voucher_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "nummer": format_number(row),
        "angelegt": str(row.get("create") or row.get("update") or "-"),
        "belegdatum": str(row.get("voucherDate") or row.get("invoiceDate") or "-"),
        "betrag": format_amount(row),
        "beschreibung": format_text(row),
        "lieferant": _voucher_contact_name(row) or "-",
        "status": row.get("status", "-"),
        "tags": ", ".join(extract_voucher_tag_names(row)) or "-",
    }


def format_transaction_row(row: dict[str, Any]) -> dict[str, Any]:
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
