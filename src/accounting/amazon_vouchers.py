from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.accounting.amazon_customers import (
    apply_customer_to_voucher_payload,
    find_customer_by_name,
    find_customer_by_vat_id,
)
from src.accounting.amazon_extraction import format_amazon_payment_row
from src.accounting.amazon_extraction import get_amazon_booking_rows
from src.accounting.amazon_extraction import aggregate_amazon_booking_amount
from src.accounting.common import (
    compare_booking_after_receipt_window,
    find_check_account_by_name,
    flag_as_bool,
    format_sevdesk_date,
    parse_amount_value,
    parse_iso_date,
    parse_transaction_date,
    safe_filename_token,
)
from src.accounting.state import (
    AMAZON_BOOKING_MATCH_MAX_DELAY_DAYS,
    AMAZON_DEFAULT_CUSTOMER_NAME,
    AMAZON_VOUCHER_OUTPUT_DIR,
    SEVDESK_TAX_SET_INNER_COMMUNITY_SUPPLY,
    SEVDESK_TAX_RULE_DEFAULT_TAXABLE_EXPENSE,
    SEVDESK_TAX_RULE_INNER_COMMUNITY_EXPENSE,
    SPARKASSE_NAME_FRAGMENT,
)
from src.sevdesk.voucher import (
    default_create_template,
    normalize_create_payload,
    validate_create_payload,
    write_json,
)


def compute_sum_net(sum_gross: Any, tax_rate_percent: Any) -> float | None:
    gross_value = parse_amount_value(sum_gross)
    tax_rate_value = parse_amount_value(tax_rate_percent)
    if gross_value is None or tax_rate_value is None:
        return None
    divisor = 1 + (tax_rate_value / 100.0)
    if divisor <= 0:
        return None
    return round(gross_value / divisor, 2)


def active_accounting_type_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_rows = [
        row
        for row in rows
        if flag_as_bool(row.get("active", True)) and str(row.get("status", "100")) == "100"
    ]
    return active_rows or rows


def find_accounting_type_by_name_fragments(
    rows: list[dict[str, Any]],
    fragments: list[str],
) -> dict[str, Any] | None:
    candidates = active_accounting_type_rows(rows)
    for fragment in fragments:
        wanted = fragment.strip().lower()
        if not wanted:
            continue
        for row in candidates:
            name = str(row.get("name", "")).strip().lower()
            if wanted in name:
                return row
    return None


def find_accounting_type_by_exact_names(
    rows: list[dict[str, Any]],
    names: list[str],
) -> dict[str, Any] | None:
    candidates = active_accounting_type_rows(rows)
    wanted_names = [name.strip().lower() for name in names if name.strip()]
    for wanted in wanted_names:
        for row in candidates:
            name = str(row.get("name", "")).strip().lower()
            if name == wanted:
                return row
    return None


def select_accounting_type_for_purchase_category(
    rows: list[dict[str, Any]],
    purchase_category: str | None,
) -> dict[str, Any] | None:
    normalized = str(purchase_category or "").strip().lower()
    if normalized == "sonstiges material":
        match = find_accounting_type_by_exact_names(rows, ["Materialeinkauf"])
        if match is not None:
            return match
        match = find_accounting_type_by_name_fragments(
            rows,
            ["materialeinkauf", "material/waren", "material", "sonstiges"],
        )
        if match is not None:
            return match
    if normalized == "bürobedarf":
        match = find_accounting_type_by_exact_names(
            rows,
            ["Büromaterial", "Buromaterial", "Office stationery"],
        )
        if match is not None:
            return match
        match = find_accounting_type_by_name_fragments(
            rows,
            [
                "büromaterial",
                "buromaterial",
                "office stationery",
                "büro",
                "buero",
                "buro",
                "office",
                "sonstiges",
            ],
        )
        if match is not None:
            return match
    return find_accounting_type_by_name_fragments(rows, ["sonstiges"])


def build_voucher_description(
    booking_row: dict[str, Any],
    *,
    entry_index: int,
    total_entries: int,
) -> str:
    order_number = format_amazon_payment_row(booking_row).get("orderNumber") or ""
    if order_number:
        if total_entries > 1:
            return f"{order_number}-{entry_index}"
        return order_number
    return f"Amazon-Beleg-{booking_row.get('id', '-')}"


def determine_supplier_name(booking_row: dict[str, Any], extracted: dict[str, Any]) -> str:
    if extracted.get("intra_community_supply") is not True:
        return AMAZON_DEFAULT_CUSTOMER_NAME
    seller_name = str(extracted.get("seller_name") or "").strip()
    if seller_name:
        return seller_name
    payee_name = str(booking_row.get("payeePayerName") or "").strip()
    if payee_name:
        return payee_name
    return "Unbekannter Lieferant"


def select_tax_rule_for_extraction(extracted: dict[str, Any]) -> dict[str, Any]:
    if extracted.get("intra_community_supply") is True:
        return dict(SEVDESK_TAX_RULE_INNER_COMMUNITY_EXPENSE)
    return dict(SEVDESK_TAX_RULE_DEFAULT_TAXABLE_EXPENSE)


def select_tax_set_for_extraction(extracted: dict[str, Any]) -> dict[str, Any] | None:
    if extracted.get("intra_community_supply") is True:
        return dict(SEVDESK_TAX_SET_INNER_COMMUNITY_SUPPLY)
    return None


def build_amazon_voucher_payload(
    *,
    booking_row: dict[str, Any],
    extracted: dict[str, Any],
    pdf_path: str,
    entry_index: int,
    total_entries: int,
    accounting_type_rows: list[dict[str, Any]],
    check_account_rows: list[dict[str, Any]],
    customer_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    booking_date = parse_transaction_date(booking_row)
    source_booking_rows = get_amazon_booking_rows(booking_row)
    source_booking_ids = [str(row.get("id", "")).strip() for row in source_booking_rows]
    invoice_date = parse_iso_date(extracted.get("invoice_date")) or booking_date or date.today()
    payment_deadline = invoice_date + timedelta(days=14)
    gross_amount = parse_amount_value(extracted.get("amount")) or 0.0
    tax_rate_percent = parse_amount_value(extracted.get("vat_rate_percent")) or 0.0
    sum_net = compute_sum_net(gross_amount, tax_rate_percent)
    purchase_category = extracted.get("purchase_category")
    selected_accounting_type = select_accounting_type_for_purchase_category(
        accounting_type_rows,
        purchase_category,
    )
    sparkasse_account = find_check_account_by_name(check_account_rows, SPARKASSE_NAME_FRAGMENT)
    payload = default_create_template(
        default_buchunggskonto=selected_accounting_type,
        default_zahlungskonto=sparkasse_account,
    )

    voucher = payload["voucher"]
    voucher["voucherDate"] = format_sevdesk_date(invoice_date)
    voucher["deliveryDate"] = format_sevdesk_date(invoice_date)
    voucher["paymentDeadline"] = format_sevdesk_date(payment_deadline)
    voucher["description"] = build_voucher_description(
        booking_row,
        entry_index=entry_index,
        total_entries=total_entries,
    )
    voucher["supplierName"] = determine_supplier_name(booking_row, extracted)
    voucher["taxRule"] = select_tax_rule_for_extraction(extracted)
    selected_tax_set = select_tax_set_for_extraction(extracted)
    if selected_tax_set is not None:
        voucher["taxSet"] = selected_tax_set
    voucher["document"] = None
    if isinstance(customer_row, dict):
        apply_customer_to_voucher_payload(payload, customer_row)

    position = payload["voucherPosSave"][0]
    position["net"] = False
    position["taxRate"] = float(tax_rate_percent)
    position["sumGross"] = gross_amount
    if sum_net is not None:
        position["sumNet"] = sum_net
    position["comment"] = f"Amazon {purchase_category}" if purchase_category else "Amazon Beleg"

    payload["notes"] = {
        **payload.get("notes", {}),
        "amazon_receipt_match": {
            "booking_id": str(booking_row.get("id", "")),
            "booking_ids": source_booking_ids,
            "booking_count": len(source_booking_rows),
            "booking_date": booking_date.isoformat() if booking_date else None,
            "booking_amount": aggregate_amazon_booking_amount(booking_row),
            "order_number": format_amazon_payment_row(booking_row).get("orderNumber") or None,
            "matched_pdf_path": pdf_path,
            "match_rule": (
                f"amount exact and booking date within {AMAZON_BOOKING_MATCH_MAX_DELAY_DAYS} "
                "days after receipt date"
            ),
        },
        "extracted_pdf_fields": {
            "document_number": extracted.get("document_number"),
            "seller_name": extracted.get("seller_name"),
            "invoice_date": extracted.get("invoice_date"),
            "amount": parse_amount_value(extracted.get("amount")),
            "vat_rate_percent": parse_amount_value(extracted.get("vat_rate_percent")),
            "seller_vat_id": extracted.get("seller_vat_id"),
            "intra_community_supply": extracted.get("intra_community_supply"),
            "purchase_category": purchase_category,
            "notes": extracted.get("notes"),
        },
        "selected_tax_rule": voucher.get("taxRule"),
        "selected_tax_set": voucher.get("taxSet"),
        "generated_by": "apps/accounting.py",
    }
    return normalize_create_payload(payload)


def build_voucher_output_path(
    booking_row: dict[str, Any],
    extracted: dict[str, Any],
    pdf_path: str,
) -> Path:
    booking_id = safe_filename_token(booking_row.get("id"))
    descriptor = safe_filename_token(
        extracted.get("document_number") or format_amazon_payment_row(booking_row).get("orderNumber")
    )
    pdf_descriptor = safe_filename_token(Path(pdf_path).stem)
    return AMAZON_VOUCHER_OUTPUT_DIR / f"amazon_voucher_{booking_id}_{descriptor}_{pdf_descriptor}.json"


def build_voucher_payload_entries(
    *,
    booking_row: dict[str, Any],
    extraction_results: list[dict[str, Any]],
    accounting_type_rows: list[dict[str, Any]],
    check_account_rows: list[dict[str, Any]],
    customer_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    known_accounting_type_ids = {
        str(row.get("id"))
        for row in accounting_type_rows
        if row.get("id") is not None
    }
    entries: list[dict[str, Any]] = []
    total_entries = len(extraction_results)
    for entry_index, extraction_result in enumerate(extraction_results, start=1):
        pdf_path = str(extraction_result.get("pdfPath", "")).strip()
        extracted = extraction_result.get("extracted")
        if not pdf_path or not isinstance(extracted, dict):
            continue
        is_intra_community_supply = extracted.get("intra_community_supply") is True
        if is_intra_community_supply:
            matched_customer = find_customer_by_vat_id(customer_rows, extracted.get("seller_vat_id"))
        else:
            matched_customer = find_customer_by_name(customer_rows, AMAZON_DEFAULT_CUSTOMER_NAME)
        voucher_payload = build_amazon_voucher_payload(
            booking_row=booking_row,
            extracted=extracted,
            pdf_path=pdf_path,
            entry_index=entry_index,
            total_entries=total_entries,
            accounting_type_rows=accounting_type_rows,
            check_account_rows=check_account_rows,
            customer_row=matched_customer,
        )
        validation_errors = validate_create_payload(voucher_payload, known_accounting_type_ids)
        voucher_path = build_voucher_output_path(booking_row, extracted, pdf_path)
        write_json(voucher_path, voucher_payload)
        entries.append(
            {
                "matchedPdfPath": pdf_path,
                "path": str(voucher_path),
                "payload": voucher_payload,
                "sellerName": str(extracted.get("seller_name", "")).strip(),
                "sellerVatId": str(extracted.get("seller_vat_id", "")).strip(),
                "isIntraCommunitySupply": is_intra_community_supply,
                "customer": matched_customer,
                "validationErrors": validation_errors,
                "createResponse": None,
                "createCustomerResponse": None,
                "createdCustomer": None,
                "createdVoucher": None,
            }
        )
    return entries
