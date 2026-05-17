from datetime import date

from src.accounting.u1_vouchers import (
    build_lohnkosten_voucher_payloads,
    build_u1_voucher_payloads,
)


def test_build_u1_voucher_payloads_marks_voucher_as_revenue() -> None:
    payloads = build_u1_voucher_payloads(
        [
            {
                "file_name": "u1.pdf",
                "pages": [
                    {
                        "page_number": 1,
                        "page_count": 1,
                        "extracted": {
                            "erstattungsbeitrag": "123,45",
                            "krankenkasse": "AOK Baden-Wuerttemberg",
                        },
                    }
                ],
            }
        ],
        date(2026, 4, 30),
        accounting_type_rows=[
            {"id": "42", "name": "Krankenkasse", "active": True, "status": "100"}
        ],
    )

    assert len(payloads) == 1
    assert payloads[0]["voucher_payload"]["voucher"]["creditDebit"] == "D"


def test_build_lohnkosten_voucher_payloads_keep_expense_direction(monkeypatch) -> None:
    monkeypatch.setattr("src.accounting.u1_vouchers.load_stored_tax_rules", lambda: [])

    payloads = build_lohnkosten_voucher_payloads(
        [
            {
                "file_name": "lohn.pdf",
                "extracted": {
                    "gesamtsumme_lohnueberweisungen": "1000,00",
                    "zwischensumme_krankenkasse": "200,00",
                    "zwischensumme_finanzamt": None,
                },
            }
        ],
        date(2026, 4, 30),
        accounting_type_rows=[
            {"id": "1", "name": "Lohn / Gehalt", "active": True, "status": "100"},
            {"id": "2", "name": "Krankenkasse", "active": True, "status": "100"},
        ],
    )

    voucher_payloads = [
        entry["voucher_payload"] for entry in payloads if isinstance(entry.get("voucher_payload"), dict)
    ]
    assert voucher_payloads
    assert {payload["voucher"]["creditDebit"] for payload in voucher_payloads} == {"C"}
