from typing import Any

from src.accounting.state import TRANSACTION_STATUS_LABELS
from src.sevdesk.voucher import format_amount, format_date, format_number, format_text


def format_voucher_row(row: dict[str, Any]) -> dict[str, Any]:
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


def format_latest_voucher_row(row: dict[str, Any]) -> dict[str, Any]:
    supplier = row.get("supplierName")
    if not supplier and isinstance(row.get("supplier"), dict):
        supplier = row["supplier"].get("name")
    return {
        "id": str(row.get("id", "")),
        "nummer": format_number(row),
        "angelegt": str(row.get("create") or row.get("update") or "-"),
        "belegdatum": str(row.get("voucherDate") or row.get("invoiceDate") or "-"),
        "betrag": format_amount(row),
        "beschreibung": format_text(row),
        "lieferant": supplier or "-",
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
