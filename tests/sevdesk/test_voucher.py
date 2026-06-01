from src.sevdesk.voucher import (
    build_voucher_accounting_type_update_payload_for_positions,
    build_voucher_field_update_payload,
)


def test_build_voucher_accounting_type_update_payload_for_positions_updates_only_selected_ids() -> None:
    existing_voucher = {
        "id": "voucher-1",
        "objectName": "Voucher",
        "voucherPos": [
            {
                "id": "pos-1",
                "objectName": "VoucherPos",
                "accountingType": {"id": "old-1", "objectName": "AccountingType"},
            },
            {
                "id": "pos-2",
                "objectName": "VoucherPos",
                "accountingType": {"id": "old-2", "objectName": "AccountingType"},
            },
        ],
    }
    accounting_type = {"id": "new-1", "name": "Wareneingang"}

    payload = build_voucher_accounting_type_update_payload_for_positions(
        existing_voucher,
        accounting_type,
        ["pos-2"],
    )

    payload_positions = payload["voucherPosSave"]
    assert len(payload_positions) == 2
    assert payload_positions[0]["id"] == "pos-1"
    assert payload_positions[0]["accountingType"]["id"] == "old-1"
    assert payload_positions[1]["id"] == "pos-2"
    assert payload_positions[1]["accountingType"]["id"] == "new-1"


def test_build_voucher_field_update_payload_preserves_positions() -> None:
    existing_voucher = {
        "id": "voucher-1",
        "objectName": "Voucher",
        "voucherDate": "2026-04-01T00:00:00+02:00",
        "deliveryDate": "2026-04-01T00:00:00+02:00",
        "description": "old-name",
        "create": "2026-04-01T08:00:00+02:00",
        "voucherPos": [
            {
                "id": "pos-1",
                "objectName": "VoucherPos",
                "text": "Coffee",
                "create": "2026-04-01T08:00:00+02:00",
                "accountingType": {"id": "old-1", "objectName": "AccountingType"},
            }
        ],
    }

    payload = build_voucher_field_update_payload(
        existing_voucher,
        voucher_date="02.04.2026",
        delivery_date="03.04.2026",
        description="new-name",
    )

    assert payload["voucher"]["id"] == "voucher-1"
    assert payload["voucher"]["voucherDate"] == "02.04.2026"
    assert payload["voucher"]["deliveryDate"] == "03.04.2026"
    assert payload["voucher"]["description"] == "new-name"
    assert "create" not in payload["voucher"]
    assert payload["voucherPosSave"][0]["id"] == "pos-1"
    assert payload["voucherPosSave"][0]["accountingType"]["id"] == "old-1"
    assert "create" not in payload["voucherPosSave"][0]
