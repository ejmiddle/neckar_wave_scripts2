from src.sevdesk.voucher import build_voucher_accounting_type_update_payload_for_positions


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
