from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.accounting.common import (
    format_sevdesk_date,
    parse_amount_value,
    parse_transaction_date,
)
from src.sevdesk.voucher import normalize_create_payload

TRANSFER_VOUCHER_SUPPLIER_OPTIONS: dict[str, dict[str, str]] = {
    "Neckar Wave Foods Finom": {
        "id": "122642810",
        "objectName": "Contact",
    },
    "PayPal Europe Services Ltd.": {
        "id": "78863970",
        "objectName": "Contact",
    },
}

TRANSFER_VOUCHER_DESCRIPTION = "Umbuchung"


def _base_transfer_voucher_payload(supplier_name: str) -> dict[str, Any]:
    supplier = TRANSFER_VOUCHER_SUPPLIER_OPTIONS.get(supplier_name)
    if supplier is None:
        raise RuntimeError(f"Unknown Geldtransfer supplier option: {supplier_name}")

    return {
        "voucher": {
            "objectName": "Voucher",
            "mapAll": True,
            "voucherType": "VOU",
            "creditDebit": "C",
            "status": 100,
            "currency": "EUR",
            "description": TRANSFER_VOUCHER_DESCRIPTION,
            "supplier": deepcopy(supplier),
            "supplierName": None,
            "taxRule": {"id": 9, "objectName": "TaxRule"},
            "taxType": "default",
            "costCentre": None,
            "document": None,
        },
        "voucherPosSave": [
            {
                "objectName": "VoucherPos",
                "mapAll": True,
                "net": False,
                "taxRate": 0.0,
                "accountingType": {"id": "40", "objectName": "AccountingType"},
            }
        ],
        "voucherPosDelete": None,
        "filename": None,
    }


def build_transfer_voucher_payload(
    transaction: dict[str, Any],
    supplier_name: str,
) -> dict[str, Any]:
    amount = parse_amount_value(transaction.get("amount"))
    if amount is None:
        transaction_id = str(transaction.get("id", "")).strip() or "-"
        raise RuntimeError(f"Payment id={transaction_id} has no usable amount.")
    if amount == 0:
        transaction_id = str(transaction.get("id", "")).strip() or "-"
        raise RuntimeError(f"Payment id={transaction_id} has amount 0.00 and cannot create a voucher.")

    voucher_date = parse_transaction_date(transaction)
    date_text = format_sevdesk_date(voucher_date)
    gross_amount = round(abs(amount), 2)
    transaction_id = str(transaction.get("id", "")).strip()
    comment_parts = [
        str(transaction.get("paymtPurpose", "")).strip(),
        str(transaction.get("entryText", "")).strip(),
    ]
    position_comment = next((part for part in comment_parts if part), "")
    if transaction_id:
        position_comment = (
            f"{position_comment} | Zahlung {transaction_id}"
            if position_comment
            else f"Zahlung {transaction_id}"
        )
    if not position_comment:
        position_comment = TRANSFER_VOUCHER_DESCRIPTION

    payload = _base_transfer_voucher_payload(supplier_name)
    voucher = payload["voucher"]
    voucher["creditDebit"] = "D" if amount > 0 else "C"
    voucher["voucherDate"] = date_text
    voucher["deliveryDate"] = date_text
    voucher["paymentDeadline"] = date_text

    position = payload["voucherPosSave"][0]
    position["sum"] = gross_amount
    position["sumNet"] = gross_amount
    position["sumGross"] = gross_amount
    position["comment"] = position_comment

    return normalize_create_payload(payload)


def build_transfer_voucher_payloads(
    transactions: list[dict[str, Any]],
    supplier_name: str,
) -> list[dict[str, Any]]:
    return [build_transfer_voucher_payload(transaction, supplier_name) for transaction in transactions]
