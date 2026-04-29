from src.accounting.amazon_extraction import (
    aggregate_booking_receipt_match,
    annotate_receipt_page_relevance,
    build_aggregate_accounting_comparison_rows,
    meaningful_receipt_extraction_results,
    sum_extracted_pdf_amounts,
)


def test_amazon_amount_sum_ignores_non_meaningful_continuation_pages() -> None:
    extraction_results = annotate_receipt_page_relevance(
        [
            {
                "extracted": {
                    "document_number": "CZ6000C3MKK11I",
                    "invoice_date": "2026-04-04",
                    "amount": 10.83,
                }
            },
            {
                "extracted": {
                    "document_number": "CZ6000C3MKK11I",
                    "invoice_date": None,
                    "amount": None,
                    "notes": "Seite 2 von 2, keine weiteren Informationen sichtbar.",
                }
            },
        ]
    )
    booking_row = {
        "amount": "-10.83",
        "valueDate": "2026-04-08T00:00:00+02:00",
    }

    assert len(meaningful_receipt_extraction_results(extraction_results)) == 1
    assert extraction_results[1]["isMeaningfulReceiptPage"] is False
    assert extraction_results[1]["skipReason"] == "missing amount"
    assert sum_extracted_pdf_amounts(extraction_results) == 10.83
    assert aggregate_booking_receipt_match(booking_row, extraction_results) is True

    comparison_rows = build_aggregate_accounting_comparison_rows(booking_row, extraction_results)
    ignored_row = next(row for row in comparison_rows if row["field"] == "Ignorierte Seiten")
    receipt_count_row = next(row for row in comparison_rows if row["field"] == "Anzahl Seiten-Belege")
    assert ignored_row["pdf"] == "1"
    assert receipt_count_row["pdf"] == "1"


def test_amazon_amount_sum_returns_none_when_no_meaningful_pages_exist() -> None:
    extraction_results = annotate_receipt_page_relevance(
        [
            {
                "extracted": {
                    "document_number": "CZ6000C3MKK11I",
                    "invoice_date": None,
                    "amount": None,
                }
            }
        ]
    )

    assert meaningful_receipt_extraction_results(extraction_results) == []
    assert sum_extracted_pdf_amounts(extraction_results) is None
