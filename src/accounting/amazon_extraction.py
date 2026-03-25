from pathlib import Path
from typing import Any

import pandas as pd

from src.accounting.common import (
    compare_amounts,
    compare_booking_after_receipt_window,
    format_bool_value,
    format_currency_value,
    format_match_value,
    parse_amount_value,
    parse_transaction_date,
)
from src.accounting.state import (
    AMAZON_BOOKING_MATCH_MAX_DELAY_DAYS,
    AMAZON_RECEIPTS_DIR,
    TRANSACTION_STATUS_LABELS,
)
from src.amazon_accounting_llm import build_document_user_content, extract_amazon_accounting_data
from src.amazon_accounting_prompt_config import DEFAULT_SYSTEM_PROMPT
from src.lieferscheine_sources import split_pdf_bytes_to_page_images


def extract_first_15_digits(value: Any) -> str:
    import re

    digits = "".join(re.findall(r"\d", str(value or "")))
    return digits[:15]


def extract_amazon_order_number(value: Any) -> str:
    import re

    match = re.search(r"(\d{3}-\d{7}-\d{7})(?=\s+AMZN\b)", str(value or ""))
    if match:
        return match.group(1)
    return ""


def format_amazon_payment_row(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status", ""))
    return {
        "id": str(row.get("id", "")),
        "valueDate": row.get("valueDate"),
        "entryDate": row.get("entryDate"),
        "amount": row.get("amount"),
        "payeePayerName": row.get("payeePayerName"),
        "paymtPurpose": row.get("paymtPurpose"),
        "status": status,
        "statusMeaning": TRANSACTION_STATUS_LABELS.get(status, "Unknown"),
        "orderNumber": extract_amazon_order_number(row.get("paymtPurpose")),
        "first15Digits": extract_first_15_digits(row.get("paymtPurpose")),
    }


def format_status_option(status: str) -> str:
    label = TRANSACTION_STATUS_LABELS.get(status, "Unknown")
    return f"{status} - {label}"


def build_amazon_selection_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    selection_rows: list[dict[str, Any]] = []
    for row in rows:
        formatted = format_amazon_payment_row(row)
        selection_rows.append(
            {
                "selected": False,
                "id": formatted["id"],
                "valueDate": formatted["valueDate"],
                "amount": formatted["amount"],
                "status": formatted["status"],
                "statusMeaning": formatted["statusMeaning"],
                "orderNumber": formatted["orderNumber"],
                "payeePayerName": formatted["payeePayerName"],
                "paymtPurpose": formatted["paymtPurpose"],
            }
        )
    return pd.DataFrame(selection_rows)


def find_receipt_pdfs(order_number: str) -> list[str]:
    if not order_number or not AMAZON_RECEIPTS_DIR.exists():
        return []

    order_dir = AMAZON_RECEIPTS_DIR / order_number
    if order_dir.is_dir():
        return sorted(
            str(path)
            for path in order_dir.rglob("*.pdf")
            if path.is_file() and path.stem != order_number
        )

    return sorted(
        str(path)
        for path in AMAZON_RECEIPTS_DIR.rglob("*.pdf")
        if order_number in path.name and path.stem != order_number
    )


def build_selected_pdf_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in rows:
        formatted = format_amazon_payment_row(row)
        pdf_matches = find_receipt_pdfs(formatted["orderNumber"])
        matches.append(
            {
                "id": formatted["id"],
                "valueDate": formatted["valueDate"],
                "amount": formatted["amount"],
                "status": formatted["status"],
                "statusMeaning": formatted["statusMeaning"],
                "orderNumber": formatted["orderNumber"],
                "pdfCount": len(pdf_matches),
                "pdfPaths": pdf_matches,
            }
        )
    return matches


def extract_accounting_data_from_pdf(
    *,
    pdf_path: str,
    provider: str,
    model_name: str,
    api_key: str,
) -> dict[str, Any]:
    pdf_file = Path(pdf_path)
    pdf_bytes = pdf_file.read_bytes()
    page_images = split_pdf_bytes_to_page_images(
        pdf_bytes,
        pdf_name=pdf_file.name,
        dpi=180,
        grayscale=False,
        max_image_bytes=1_500_000,
    )
    if not page_images:
        raise RuntimeError(f"PDF enthaelt keine verarbeitbaren Seiten: {pdf_file}")
    user_content = build_document_user_content(page_images)
    extracted = extract_amazon_accounting_data(
        provider=provider,
        api_key=api_key,
        model_name=model_name,
        user_content=user_content,
        system_prompt_base=DEFAULT_SYSTEM_PROMPT,
    )
    return {
        "pdfPath": str(pdf_file),
        "provider": provider,
        "model": model_name,
        "pageCount": len(page_images),
        "extracted": extracted,
    }


def build_accounting_comparison_rows(
    booking_row: dict[str, Any],
    extracted: dict[str, Any],
) -> list[dict[str, Any]]:
    formatted_booking = format_amazon_payment_row(booking_row)
    booking_date = parse_transaction_date(booking_row)
    return [
        {
            "field": "Betrag",
            "booking": format_currency_value(formatted_booking.get("amount")),
            "pdf": format_currency_value(extracted.get("amount")),
            "match": format_match_value(
                compare_amounts(formatted_booking.get("amount"), extracted.get("amount"))
            ),
        },
        {
            "field": "Datum",
            "booking": booking_date.isoformat() if booking_date else "-",
            "pdf": extracted.get("invoice_date") or "-",
            "match": format_match_value(
                compare_booking_after_receipt_window(
                    booking_date,
                    extracted.get("invoice_date"),
                    AMAZON_BOOKING_MATCH_MAX_DELAY_DAYS,
                )
            ),
        },
    ]


def sum_extracted_pdf_amounts(extraction_results: list[dict[str, Any]]) -> float | None:
    if not extraction_results:
        return None
    amounts: list[float] = []
    for extraction_result in extraction_results:
        extracted = extraction_result.get("extracted")
        if not isinstance(extracted, dict):
            return None
        amount = parse_amount_value(extracted.get("amount"))
        if amount is None:
            return None
        amounts.append(amount)
    return round(sum(amounts), 2)


def aggregate_booking_receipt_match(
    booking_row: dict[str, Any],
    extraction_results: list[dict[str, Any]],
) -> bool | None:
    summed_amount = sum_extracted_pdf_amounts(extraction_results)
    return compare_amounts(booking_row.get("amount"), summed_amount)


def build_aggregate_accounting_comparison_rows(
    booking_row: dict[str, Any],
    extraction_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    summed_amount = sum_extracted_pdf_amounts(extraction_results)
    return [
        {
            "field": "Betrag (Summe PDFs)",
            "booking": format_currency_value(booking_row.get("amount")),
            "pdf": format_currency_value(summed_amount),
            "match": format_match_value(compare_amounts(booking_row.get("amount"), summed_amount)),
        },
        {
            "field": "Anzahl PDFs",
            "booking": "-",
            "pdf": str(len(extraction_results)),
            "match": "-",
        },
    ]


def build_extracted_accounting_rows(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    vat_rate = extracted.get("vat_rate_percent")
    return [
        {"field": "Verkäufer", "value": extracted.get("seller_name") or "-"},
        {"field": "Betrag", "value": format_currency_value(extracted.get("amount"))},
        {"field": "Umsatzsteuer %", "value": f"{vat_rate}%" if vat_rate is not None else "-"},
        {"field": "USt-IdNr. Verkäufer", "value": extracted.get("seller_vat_id") or "-"},
        {
            "field": "Innergemeinschaftliche Lieferung",
            "value": format_bool_value(extracted.get("intra_community_supply")),
        },
        {"field": "Einkaufskategorie", "value": extracted.get("purchase_category") or "-"},
        {"field": "Belegnummer", "value": extracted.get("document_number") or "-"},
        {"field": "Rechnungsdatum", "value": extracted.get("invoice_date") or "-"},
        {"field": "Hinweis", "value": extracted.get("notes") or "-"},
    ]
