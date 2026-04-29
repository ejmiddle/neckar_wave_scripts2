import re
from typing import Any

from src.accounting.common import parse_amount_value
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
    for key in ("supplier", "contact"):
        contact_name = _contact_display_name(row.get(key))
        if contact_name:
            return contact_name

    supplier_name_value = row.get("supplierName")
    supplier_name = str(supplier_name_value).strip() if supplier_name_value is not None else ""
    if supplier_name:
        return supplier_name

    supplier_name_at_save_value = row.get("supplierNameAtSave")
    supplier_name_at_save = (
        str(supplier_name_at_save_value).strip() if supplier_name_at_save_value is not None else ""
    )
    if supplier_name_at_save:
        return supplier_name_at_save

    return ""


def _voucher_positions(row: dict[str, Any]) -> list[dict[str, Any]]:
    positions_value = row.get("voucherPos") or row.get("voucherPosSave")
    if isinstance(positions_value, list):
        return [position for position in positions_value if isinstance(position, dict)]

    nested_voucher = row.get("voucher")
    if isinstance(nested_voucher, dict):
        nested_positions = nested_voucher.get("voucherPos") or nested_voucher.get("voucherPosSave")
        if isinstance(nested_positions, list):
            return [position for position in nested_positions if isinstance(position, dict)]

    return []


def _accounting_ref_label(value: Any) -> str:
    if not isinstance(value, dict):
        return ""

    name = str(value.get("name", "")).strip()
    account_number = str(
        value.get("accountNumber") or value.get("number") or value.get("skr03") or value.get("skr04") or ""
    ).strip()
    item_id = str(value.get("id", "")).strip()

    if name and account_number:
        return f"{name} ({account_number})"
    if name:
        return name
    if account_number:
        return account_number
    return item_id


def _voucher_accounting_type_name(row: dict[str, Any]) -> str:
    accounting_labels = _dedupe_texts(
        [
            _accounting_ref_label(position.get("accountingType"))
            or _accounting_ref_label(position.get("accountDatev"))
            for position in _voucher_positions(row)
        ]
    )
    if not accounting_labels:
        return "-"
    return ", ".join(accounting_labels)


def _invoice_contact_name(row: dict[str, Any]) -> str:
    customer_name = str(
        row.get("customerName")
        or row.get("contactName")
        or row.get("clientName")
        or row.get("recipientName")
        or row.get("addressName")
        or row.get("addressParentName")
        or row.get("addressName2")
        or ""
    ).strip()
    if customer_name:
        return customer_name

    for key in ("customer", "contact", "supplier", "debtor", "recipient"):
        contact_name = _contact_display_name(row.get(key))
        if contact_name:
            return contact_name

    return _voucher_contact_name(row)


def _invoice_amount_value(row: dict[str, Any]) -> Any:
    for key in ("sumGross", "totalGross", "sumNet"):
        if key in row and row.get(key) is not None:
            return row.get(key)
    return None


def _invoice_type_value(row: dict[str, Any]) -> str:
    return str(row.get("invoiceType", "")).strip()


def _invoice_is_storno(row: dict[str, Any]) -> bool:
    invoice_type = _invoice_type_value(row).casefold()
    if invoice_type == "sr":
        return True

    header = str(row.get("header", "")).strip().casefold()
    return "stornorechnung" in header or "storno" in header


def _invoice_description(row: dict[str, Any]) -> str:
    header = str(row.get("header", "")).strip()
    if header:
        return header
    return format_text(row)


def _invoice_subinfo(row: dict[str, Any]) -> str:
    search_fields = (
        row.get("header"),
        row.get("headText"),
        row.get("footText"),
        row.get("customerInternalNote"),
    )
    for value in search_fields:
        text = str(value or "").strip()
        if not text:
            continue
        match = re.search(r"#[A-Za-z0-9_-]+", text)
        if match:
            return match.group(0)
    return "-"


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


def format_voucher_position_row(
    row: dict[str, Any],
    *,
    accounting_type_lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    voucher = row.get("voucher")
    voucher_id = ""
    voucher_number = "-"
    voucher_description = "-"
    if isinstance(voucher, dict):
        voucher_id = str(voucher.get("id", "")).strip()
        voucher_number = (
            str(voucher.get("voucherNumber") or voucher.get("number") or voucher.get("id") or "-").strip() or "-"
        )
        voucher_description = str(voucher.get("description", "")).strip() or "-"

    amount_value = row.get("sumGross")
    if amount_value is None:
        amount_value = row.get("sumNet")

    accounting_type = row.get("accountingType")
    accounting_type_id = ""
    if isinstance(accounting_type, dict):
        accounting_type_id = str(accounting_type.get("id", "")).strip()
    accounting_type_master = (
        accounting_type_lookup.get(accounting_type_id, {})
        if accounting_type_lookup and accounting_type_id
        else {}
    )
    accounting_type_description = (
        str(accounting_type_master.get("description") or accounting_type_master.get("name") or "").strip()
        or _accounting_ref_label(accounting_type)
        or _accounting_ref_label(row.get("accountDatev"))
        or "-"
    )

    return {
        "positions_id": str(row.get("id", "")).strip(),
        "beleg_id": voucher_id or "-",
        "belegnummer": voucher_number,
        "beschreibung": voucher_description,
        "positionstext": str(row.get("text", "")).strip() or "-",
        "betrag": parse_amount_value(amount_value),
        "buchungskonto": _accounting_ref_label(row.get("accountingType"))
        or _accounting_ref_label(row.get("accountDatev"))
        or "-",
        "buchungskonto_beschreibung": accounting_type_description,
    }


def format_latest_invoice_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "nummer": str(row.get("invoiceNumber") or row.get("number") or row.get("id") or "-"),
        "kunde": _invoice_contact_name(row) or "-",
        "angelegt": str(row.get("create") or row.get("update") or "-"),
        "rechnungsdatum": str(row.get("invoiceDate") or row.get("voucherDate") or "-"),
        "betrag": parse_amount_value(_invoice_amount_value(row)),
        "rechnungstyp": _invoice_type_value(row) or "-",
        "storno": "Ja" if _invoice_is_storno(row) else "Nein",
        "subinfo": _invoice_subinfo(row),
        "beschreibung": _invoice_description(row),
        "status": row.get("status", "-"),
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
