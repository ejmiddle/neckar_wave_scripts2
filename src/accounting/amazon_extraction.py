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
    explicit_order_number = str(row.get("orderNumber", "")).strip()
    explicit_status_meaning = str(row.get("statusMeaning", "")).strip()
    return {
        "id": str(row.get("id", "")),
        "valueDate": row.get("valueDate"),
        "entryDate": row.get("entryDate"),
        "amount": row.get("amount"),
        "payeePayerName": row.get("payeePayerName"),
        "paymtPurpose": row.get("paymtPurpose"),
        "status": status,
        "statusMeaning": explicit_status_meaning or TRANSACTION_STATUS_LABELS.get(status, "Unknown"),
        "orderNumber": explicit_order_number or extract_amazon_order_number(row.get("paymtPurpose")),
        "first15Digits": str(row.get("first15Digits", "")).strip()
        or extract_first_15_digits(row.get("paymtPurpose")),
    }


def format_status_option(status: str) -> str:
    label = TRANSACTION_STATUS_LABELS.get(status, "Unknown")
    return f"{status} - {label}"


def get_amazon_booking_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    grouped_rows = row.get("bookingRows")
    if isinstance(grouped_rows, list):
        return [booking_row for booking_row in grouped_rows if isinstance(booking_row, dict)] or [row]
    return [row]


def _amazon_booking_dates(row: dict[str, Any]) -> list:
    dates = [parse_transaction_date(booking_row) for booking_row in get_amazon_booking_rows(row)]
    return [booking_date for booking_date in dates if booking_date is not None]


def _format_booking_date_display(row: dict[str, Any]) -> str:
    booking_dates = sorted(_amazon_booking_dates(row))
    if not booking_dates:
        return "-"
    first_date = booking_dates[0].isoformat()
    last_date = booking_dates[-1].isoformat()
    if first_date == last_date:
        return first_date
    return f"{first_date} -> {last_date}"


def aggregate_amazon_booking_amount(row: dict[str, Any]) -> float | None:
    amounts = [
        parse_amount_value(booking_row.get("amount"))
        for booking_row in get_amazon_booking_rows(row)
    ]
    valid_amounts = [amount for amount in amounts if amount is not None]
    if not valid_amounts:
        return None
    return round(sum(valid_amounts), 2)


def _compare_group_booking_after_receipt_window(
    booking_row: dict[str, Any],
    extracted_value: Any,
) -> bool | None:
    booking_dates = _amazon_booking_dates(booking_row)
    if not booking_dates:
        return None
    comparisons = [
        compare_booking_after_receipt_window(
            booking_date,
            extracted_value,
            AMAZON_BOOKING_MATCH_MAX_DELAY_DAYS,
        )
        for booking_date in booking_dates
    ]
    if any(result is True for result in comparisons):
        return True
    if any(result is False for result in comparisons):
        return False
    return None


def _selection_group_key(order_number: str, formatted_row: dict[str, Any], group_size: int) -> str:
    if order_number and group_size > 1:
        return f"order:{order_number}"
    return str(formatted_row.get("id", ""))


def _build_amazon_selection_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_rows = [row for row in rows if isinstance(row, dict)]
    formatted_rows = [format_amazon_payment_row(row) for row in source_rows]
    first_formatted_row = formatted_rows[0]
    booking_dates = sorted(_amazon_booking_dates({"bookingRows": source_rows}))
    latest_booking_date = booking_dates[-1].isoformat() if booking_dates else None
    order_number = next(
        (str(formatted_row.get("orderNumber", "")).strip() for formatted_row in formatted_rows if str(formatted_row.get("orderNumber", "")).strip()),
        "",
    )
    unique_statuses = sorted(
        {str(formatted_row.get("status", "")).strip() for formatted_row in formatted_rows if str(formatted_row.get("status", "")).strip()}
    )
    unique_status_meanings = sorted(
        {
            str(formatted_row.get("statusMeaning", "")).strip()
            for formatted_row in formatted_rows
            if str(formatted_row.get("statusMeaning", "")).strip()
        }
    )
    booking_count = len(source_rows)
    is_joint_processing = booking_count > 1 and bool(order_number)
    combined_row = {
        "id": _selection_group_key(order_number, first_formatted_row, booking_count),
        "bookingRows": source_rows,
        "bookingIds": [str(formatted_row.get("id", "")).strip() for formatted_row in formatted_rows],
        "bookingRefs": ", ".join(
            str(formatted_row.get("id", "")).strip() or "-"
            for formatted_row in formatted_rows
        ),
        "bookingCount": booking_count,
        "jointProcessing": f"Grouped {booking_count} bookings" if is_joint_processing else "-",
        "isJointProcessing": is_joint_processing,
        "valueDate": latest_booking_date,
        "valueDateDisplay": _format_booking_date_display({"bookingRows": source_rows}),
        "amount": aggregate_amazon_booking_amount({"bookingRows": source_rows}),
        "status": ", ".join(unique_statuses) if len(unique_statuses) > 1 else (unique_statuses[0] if unique_statuses else ""),
        "statusMeaning": (
            "Multiple statuses"
            if len(unique_status_meanings) > 1
            else (unique_status_meanings[0] if unique_status_meanings else "Unknown")
        ),
        "orderNumber": order_number,
        "payeePayerName": str(first_formatted_row.get("payeePayerName", "")).strip(),
        "paymtPurpose": str(first_formatted_row.get("paymtPurpose", "")).strip(),
        "first15Digits": str(first_formatted_row.get("first15Digits", "")).strip(),
    }
    return combined_row


def build_amazon_selection_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted_rows = [format_amazon_payment_row(row) for row in rows]
    rows_by_order_number: dict[str, list[dict[str, Any]]] = {}
    for row, formatted_row in zip(rows, formatted_rows, strict=False):
        order_number = str(formatted_row.get("orderNumber", "")).strip()
        if order_number:
            rows_by_order_number.setdefault(order_number, []).append(row)

    selection_groups: list[dict[str, Any]] = []
    processed_group_keys: set[str] = set()
    for row, formatted_row in zip(rows, formatted_rows, strict=False):
        order_number = str(formatted_row.get("orderNumber", "")).strip()
        duplicate_rows = rows_by_order_number.get(order_number, [])
        if order_number and len(duplicate_rows) > 1:
            if order_number in processed_group_keys:
                continue
            selection_groups.append(_build_amazon_selection_group(duplicate_rows))
            processed_group_keys.add(order_number)
            continue
        selection_groups.append(_build_amazon_selection_group([row]))
    return selection_groups


def build_amazon_selection_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    selection_rows: list[dict[str, Any]] = []
    for row in build_amazon_selection_groups(rows):
        formatted = format_amazon_payment_row(row)
        selection_rows.append(
            {
                "selected": False,
                "bookingRefs": str(row.get("bookingRefs", formatted["id"])).strip() or "-",
                "bookingCount": row.get("bookingCount", 1),
                "jointProcessing": str(row.get("jointProcessing", "-")).strip() or "-",
                "valueDate": row.get("valueDateDisplay") or formatted["valueDate"],
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
                "bookingIds": row.get("bookingIds", [formatted["id"]]),
                "bookingCount": row.get("bookingCount", 1),
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
    booking_date_display = _format_booking_date_display(booking_row)
    return [
        {
            "field": "Betrag",
            "booking": format_currency_value(aggregate_amazon_booking_amount(booking_row)),
            "pdf": format_currency_value(extracted.get("amount")),
            "match": format_match_value(
                compare_amounts(aggregate_amazon_booking_amount(booking_row), extracted.get("amount"))
            ),
        },
        {
            "field": "Datum",
            "booking": booking_date_display,
            "pdf": extracted.get("invoice_date") or "-",
            "match": format_match_value(
                _compare_group_booking_after_receipt_window(booking_row, extracted.get("invoice_date"))
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
    return compare_amounts(aggregate_amazon_booking_amount(booking_row), summed_amount)


def build_aggregate_accounting_comparison_rows(
    booking_row: dict[str, Any],
    extraction_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    summed_amount = sum_extracted_pdf_amounts(extraction_results)
    return [
        {
            "field": "Betrag (Summe PDFs)",
            "booking": format_currency_value(aggregate_amazon_booking_amount(booking_row)),
            "pdf": format_currency_value(summed_amount),
            "match": format_match_value(compare_amounts(aggregate_amazon_booking_amount(booking_row), summed_amount)),
        },
        {
            "field": "Buchungen",
            "booking": str(len(get_amazon_booking_rows(booking_row))),
            "pdf": "-",
            "match": "-",
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
