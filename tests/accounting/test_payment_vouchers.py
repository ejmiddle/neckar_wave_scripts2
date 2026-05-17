import json
from pathlib import Path

import pytest

from src.accounting.payment_vouchers import (
    TRANSFER_VOUCHER_SUPPLIER_OPTIONS,
    build_transfer_voucher_payload,
)

FINOM_TEMPLATE_CACHE = Path("data/sevdesk/cache/20260511T150725Z_belege_selection_raw_api_response.json")
PAYPAL_TEMPLATE_CACHE = Path("data/sevdesk/cache/20260511T151632Z_belege_selection_raw_api_response.json")


def test_build_transfer_voucher_payload_for_outgoing_transaction() -> None:
    payload = build_transfer_voucher_payload(
        {
            "id": "tx-1",
            "amount": "-12.34",
            "valueDate": "2026-04-13T00:00:00+02:00",
            "paymtPurpose": "Internal Finom",
        },
        "Neckar Wave Foods Finom",
    )

    voucher = payload["voucher"]
    position = payload["voucherPosSave"][0]
    assert voucher["creditDebit"] == "C"
    assert voucher["voucherDate"] == "13.04.2026"
    assert voucher["deliveryDate"] == "13.04.2026"
    assert voucher["paymentDeadline"] == "13.04.2026"
    assert voucher["supplier"] == {"id": "122642810", "objectName": "Contact"}
    assert position["sumGross"] == 12.34
    assert position["sumNet"] == 12.34
    assert position["taxRate"] == 0.0
    assert position["accountingType"] == {"id": "40", "objectName": "AccountingType"}
    assert position["comment"] == "Internal Finom | Zahlung tx-1"


def test_build_transfer_voucher_payload_for_incoming_transaction() -> None:
    payload = build_transfer_voucher_payload(
        {
            "id": "tx-2",
            "amount": "42,10",
            "entryDate": "2026-04-15T00:00:00+02:00",
            "entryText": "PayPal transfer",
        },
        "PayPal Europe Services Ltd.",
    )

    voucher = payload["voucher"]
    position = payload["voucherPosSave"][0]
    assert voucher["creditDebit"] == "D"
    assert voucher["voucherDate"] == "15.04.2026"
    assert voucher["supplier"] == {"id": "78863970", "objectName": "Contact"}
    assert position["sumGross"] == 42.1
    assert position["comment"] == "PayPal transfer | Zahlung tx-2"


def test_build_transfer_voucher_payload_rejects_zero_amount() -> None:
    with pytest.raises(RuntimeError, match="amount 0.00"):
        build_transfer_voucher_payload(
            {
                "id": "tx-3",
                "amount": "0",
                "valueDate": "2026-04-13T00:00:00+02:00",
            },
            "Neckar Wave Foods Finom",
        )


def test_build_transfer_voucher_payload_rejects_unknown_supplier() -> None:
    with pytest.raises(RuntimeError, match="Unknown Geldtransfer supplier"):
        build_transfer_voucher_payload(
            {
                "id": "tx-4",
                "amount": "12",
                "valueDate": "2026-04-13T00:00:00+02:00",
            },
            "Other Supplier",
        )


@pytest.mark.parametrize(
    ("cache_path", "supplier_name", "expected_credit_debit"),
    [
        (FINOM_TEMPLATE_CACHE, "Neckar Wave Foods Finom", "C"),
        (PAYPAL_TEMPLATE_CACHE, "PayPal Europe Services Ltd.", "D"),
    ],
)
def test_transfer_voucher_template_matches_cached_transfer_examples(
    cache_path: Path,
    supplier_name: str,
    expected_credit_debit: str,
) -> None:
    cached_rows = json.loads(cache_path.read_text(encoding="utf-8"))
    matching_rows = [
        row
        for row in cached_rows
        if row.get("supplierNameAtSave") == supplier_name
        and row.get("creditDebit") == expected_credit_debit
        and str(row.get("taxType", "")).strip() == "default"
        and str(row.get("voucherPos", [{}])[0].get("accountingType", {}).get("id", "")).strip()
        == "40"
    ]

    assert matching_rows
    template_row = matching_rows[0]
    template_position = template_row["voucherPos"][0]
    assert TRANSFER_VOUCHER_SUPPLIER_OPTIONS[supplier_name] == template_row["supplier"]
    assert template_row["voucherType"] == "VOU"
    assert template_position["taxRate"] == "0"
    assert template_position["accountingType"] == {"id": "40", "objectName": "AccountingType"}
