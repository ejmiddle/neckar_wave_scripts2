from src.accounting.sevdesk_browse import format_latest_voucher_row, format_voucher_position_row


def test_format_latest_voucher_row_prefers_contact_name_over_supplier_name() -> None:
    row = {
        "id": "42",
        "supplierName": "Fallback Supplier",
        "supplier": {
            "id": "99",
            "objectName": "Contact",
            "surename": "Ada",
            "familyname": "Lovelace",
        },
        "voucherPos": [],
    }

    formatted = format_latest_voucher_row(row)

    assert formatted["lieferant"] == "Ada Lovelace"


def test_format_latest_voucher_row_falls_back_to_supplier_name_at_save() -> None:
    row = {
        "id": "42",
        "supplierName": None,
        "supplierNameAtSave": "Cata Export",
        "supplier": {
            "id": "85043961",
            "objectName": "Contact",
        },
        "voucherPos": [],
    }

    formatted = format_latest_voucher_row(row)

    assert formatted["lieferant"] == "Cata Export"


def test_format_latest_voucher_row_includes_buchungskonto_from_positions() -> None:
    row = {
        "id": "42",
        "voucherPos": [
            {
                "accountingType": {
                    "id": "7001",
                    "name": "Wareneingang",
                    "skr03": "3400",
                }
            }
        ],
    }

    formatted = format_latest_voucher_row(row)

    assert "buchungskonto" not in formatted


def test_format_voucher_position_row_includes_text_buchungskonto() -> None:
    row = {
        "id": "pos-1",
        "text": "Arabica 69kg",
        "sumGross": "2830",
        "accountingType": {
            "id": "7001",
            "name": "Wareneingang",
            "skr03": "3400",
        },
        "voucher": {
            "id": "42",
            "voucherNumber": "V-42",
            "description": "FAC00000676",
        },
    }

    formatted = format_voucher_position_row(row)

    assert formatted["positions_id"] == "pos-1"
    assert formatted["belegnummer"] == "V-42"
    assert formatted["beschreibung"] == "FAC00000676"
    assert formatted["positionstext"] == "Arabica 69kg"
    assert formatted["buchungskonto"] == "Wareneingang (3400)"
