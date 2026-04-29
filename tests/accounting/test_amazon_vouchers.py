from src.accounting.amazon_vouchers import build_voucher_payload_entries


def test_build_voucher_payload_entries_skips_non_meaningful_pages(monkeypatch) -> None:
    written_payloads: list[tuple[object, dict]] = []
    monkeypatch.setattr(
        "src.accounting.amazon_vouchers.write_json",
        lambda path, payload: written_payloads.append((path, payload)),
    )
    booking_row = {
        "id": "1846888816",
        "amount": "-10.83",
        "valueDate": "2026-04-08T00:00:00+02:00",
        "paymtPurpose": "028-2665542-5639518 AMZN Mktp DE 4DCEMIROYE6KD3AT",
    }
    extraction_results = [
        {
            "pdfPath": "20260404_Tax Invoice_028-2665542-5639518.pdf",
            "pageNumber": 1,
            "sourceKey": "pdf#page=1",
            "extracted": {
                "document_number": "CZ6000C3MKK11I",
                "seller_name": "TOP CHARGEUR",
                "invoice_date": "2026-04-04",
                "amount": 10.83,
                "vat_rate_percent": 0,
                "seller_vat_id": "CZ684634178",
                "intra_community_supply": True,
                "purchase_category": "Sonstiges Material",
            },
        },
        {
            "pdfPath": "20260404_Tax Invoice_028-2665542-5639518.pdf",
            "pageNumber": 2,
            "sourceKey": "pdf#page=2",
            "extracted": {
                "document_number": "CZ6000C3MKK11I",
                "invoice_date": None,
                "amount": None,
                "notes": "Seite 2 von 2, keine weiteren Informationen sichtbar.",
            },
        },
    ]

    entries = build_voucher_payload_entries(
        booking_row=booking_row,
        extraction_results=extraction_results,
        accounting_type_rows=[
            {"id": "18", "name": "Materialeinkauf", "active": True, "status": "100"}
        ],
        check_account_rows=[{"id": "5660932", "name": "Sparkasse", "status": "100"}],
        customer_rows=[
            {
                "id": "112294374",
                "name": "Amazon TOP CHARGEUR",
                "vatNumber": "CZ684634178",
                "status": "100",
            }
        ],
    )

    assert len(entries) == 1
    assert len(written_payloads) == 1
    assert entries[0]["sourceKey"] == "pdf#page=1"
    assert entries[0]["pageNumber"] == 1
    assert entries[0]["payload"]["voucher"]["description"] == "028-2665542-5639518"
    assert entries[0]["payload"]["voucherPosSave"][0]["sumGross"] == 10.83
