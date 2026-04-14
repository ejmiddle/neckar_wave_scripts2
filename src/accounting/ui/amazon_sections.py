import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.accounting.amazon_customers import (
    build_customer_create_payload,
    coerce_created_customer_row,
    find_customer_by_name,
    find_customer_by_vat_id,
    format_customer_display_name,
    normalize_vat_id,
    persist_updated_voucher_entry,
    refresh_live_amazon_customers,
)
from src.accounting.amazon_extraction import (
    aggregate_amazon_booking_amount,
    aggregate_booking_receipt_match,
    build_accounting_comparison_rows,
    build_aggregate_accounting_comparison_rows,
    build_amazon_selection_dataframe,
    build_amazon_selection_groups,
    build_extracted_accounting_rows,
    build_selected_pdf_matches,
    extract_accounting_data_from_pdf,
    format_status_option,
    get_amazon_booking_rows,
    sum_extracted_pdf_amounts,
)
from src.accounting.amazon_vouchers import build_voucher_payload_entries
from src.accounting.common import (
    base_url,
    cache_json_payload,
    ensure_token,
    filter_rows_by_date_range,
    find_check_account_by_name,
    format_currency_value,
    report_error,
)
from src.accounting.master_data import load_stored_accounting_types, load_stored_check_accounts
from src.accounting.sevdesk_browse import format_voucher_row
from src.accounting.state import (
    AMAZON_CUSTOMERS_SESSION_KEY,
    AMAZON_DEFAULT_CUSTOMER_NAME,
    AMAZON_PAYEE_NAME,
    SPARKASSE_NAME_FRAGMENT,
    clear_amazon_analysis_state,
    matches_amazon_payee_name,
)
from src.accounting.ui.displays import (
    render_pdf_inline,
    show_amazon_customers,
    show_amazon_payments,
)
from src.amazon_accounting_llm import (
    LLM_MODELS_BY_PROVIDER,
    LLM_PROVIDER_OPENAI,
    resolve_llm_api_key,
)
from src.logging_config import logger
from src.sevdesk.api import (
    create_contact,
    create_voucher,
    fetch_all_transactions_for_check_account,
    request_voucher_by_id,
    request_vouchers_with_tags,
    upload_voucher_temp_file,
)
from src.sevdesk.voucher import first_object_from_response, normalize_create_payload

AMAZON_EXTRACTION_PROVIDER = LLM_PROVIDER_OPENAI
AMAZON_EXTRACTION_MODEL = LLM_MODELS_BY_PROVIDER[AMAZON_EXTRACTION_PROVIDER][0]
AMAZON_STATUS_FILTER_KEY = "sevdesk_sparkasse_amazon_status_filter"
AMAZON_STATUS_FILTER_OPTIONS_KEY = "sevdesk_sparkasse_amazon_status_filter_options"
AMAZON_PDF_MATCHES_KEY = "sevdesk_sparkasse_amazon_pdf_matches"
AMAZON_LLM_RESULTS_KEY = "sevdesk_sparkasse_amazon_llm_result"
AMAZON_VOUCHER_PAYLOADS_KEY = "sevdesk_sparkasse_amazon_voucher_payload"
AMAZON_RESULT_CURSOR_KEY = "sevdesk_sparkasse_amazon_result_cursor"


def _cache_payload_caption(label: str, cache_name: str, payload: Any) -> None:
    try:
        cache_path = cache_json_payload(cache_name, payload)
        st.caption(f"{label} cached at `{cache_path}`")
    except Exception as exc:
        report_error(
            f"Failed to cache {label.lower()}: {exc}",
            log_message=f"Failed to cache {label.lower()}",
            exc_info=True,
        )
AMAZON_DUPLICATE_ORDER_COLORS = [
    "#fde68a",
    "#bfdbfe",
    "#bbf7d0",
    "#fecaca",
    "#ddd6fe",
    "#fbcfe8",
]


def render_amazon_setup_section() -> list[dict[str, Any]] | None:
    st.markdown("**Amazon Customers**")
    show_amazon_customers(st.session_state.get(AMAZON_CUSTOMERS_SESSION_KEY))

    st.divider()
    st.subheader("Sparkasse Amazon Payments")
    st.caption(
        "Filters Sparkasse bookings where `payeePayerName` contains "
        f"`{', '.join(AMAZON_PAYEE_NAME)}`."
    )
    st.caption(
        f"PDF extraction uses `{AMAZON_EXTRACTION_PROVIDER}` / `{AMAZON_EXTRACTION_MODEL}`."
    )

    amazon_rows = st.session_state.get("sevdesk_sparkasse_amazon_rows")
    available_statuses = (
        sorted({str(row.get("status", "")) for row in amazon_rows}) if amazon_rows else []
    )
    status_options = {format_status_option(status): status for status in available_statuses}
    status_labels = list(status_options.keys())
    _sync_status_filter_options(status_labels)

    controls_col1, controls_col2, controls_col3 = st.columns([1, 1, 2])
    with controls_col1:
        default_end_date = st.session_state.get("amazon_end_date_default") or date.today()
        default_start_date = default_end_date - timedelta(days=30)
        amazon_start_date = st.date_input("Start date", value=default_start_date, key="amazon_start_date")
    with controls_col2:
        amazon_end_date = st.date_input("End date", value=default_end_date, key="amazon_end_date")
    with controls_col3:
        selected_statuses = st.multiselect(
            "Status filter",
            options=status_labels,
            key=AMAZON_STATUS_FILTER_KEY,
            disabled=not status_labels,
        )

    invalid_amazon_date_range = amazon_start_date > amazon_end_date
    if invalid_amazon_date_range:
        report_error("Start date must be before or equal to end date.")

    if st.button("Load Sparkasse Amazon Payments", width="stretch", disabled=invalid_amazon_date_range):
        token = ensure_token()
        if token:
            stored_check_accounts = st.session_state.get("sevdesk_check_accounts_rows")
            if stored_check_accounts is None:
                stored_check_accounts = load_stored_check_accounts()
            if not stored_check_accounts:
                report_error(
                    "No stored check accounts found. Open Accounting MD in the accounting app and fetch them first."
                )
            else:
                sparkasse_account = find_check_account_by_name(
                    stored_check_accounts,
                    SPARKASSE_NAME_FRAGMENT,
                )
                if sparkasse_account is None:
                    report_error(
                        "No check account containing `Sparkasse` was found in stored master data."
                    )
                else:
                    try:
                        clear_amazon_analysis_state()
                        rows = fetch_all_transactions_for_check_account(
                            base_url(),
                            token,
                            str(sparkasse_account.get("id", "")),
                        )
                        rows = filter_rows_by_date_range(rows, amazon_start_date, amazon_end_date)
                        filtered_rows = [
                            row
                            for row in rows
                            if matches_amazon_payee_name(row.get("payeePayerName", ""))
                        ]
                        logger.info(
                            "Loaded %s Sparkasse row(s), matched %s Amazon row(s) using payee fragments=%s.",
                            len(rows),
                            len(filtered_rows),
                            AMAZON_PAYEE_NAME,
                        )
                        st.session_state["sevdesk_sparkasse_amazon_rows"] = filtered_rows
                        st.rerun()
                    except Exception as exc:
                        report_error(
                            f"Failed to load Sparkasse Amazon payments: {exc}",
                            log_message="Failed to load Sparkasse Amazon payments",
                            exc_info=True,
                        )

    amazon_rows = st.session_state.get("sevdesk_sparkasse_amazon_rows")
    if amazon_rows:
        selected_status_values = {
            status_options[label] for label in selected_statuses if label in status_options
        }
        if status_labels:
            amazon_rows = [
                row
                for row in amazon_rows
                if str(row.get("status", "")) in selected_status_values
            ]

    return amazon_rows


def render_booking_selection_section(amazon_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    st.markdown("**Select Booking**")
    selection_groups = build_amazon_selection_groups(amazon_rows)
    selection_df = build_amazon_selection_dataframe(amazon_rows)
    selection_table = _style_joint_order_groups(selection_df)
    edited_selection_df = st.data_editor(
        selection_table,
        width="stretch",
        hide_index=True,
        disabled=[
            "bookingRefs",
            "bookingCount",
            "jointProcessing",
            "valueDate",
            "amount",
            "status",
            "statusMeaning",
            "orderNumber",
            "payeePayerName",
            "paymtPurpose",
        ],
        column_config={
            "selected": st.column_config.CheckboxColumn("Select"),
            "bookingRefs": st.column_config.TextColumn("Booking IDs"),
            "bookingCount": st.column_config.NumberColumn("Bookings"),
            "jointProcessing": st.column_config.TextColumn("Joint Processing"),
            "orderNumber": st.column_config.TextColumn("Order Number"),
        },
        key="sevdesk_sparkasse_amazon_selection_table",
    )
    selected_selection_indexes = edited_selection_df.index[edited_selection_df["selected"]].tolist()
    selected_booking_rows = [
        selection_groups[index]
        for index in selected_selection_indexes
        if 0 <= int(index) < len(selection_groups)
    ]

    if st.button("Process bookings", width="stretch"):
        logger.info(
            "Triggered 'Process bookings' from Streamlit UI with %s selected booking(s): %s.",
            len(selected_booking_rows),
            [str(row.get("id", "")) for row in selected_booking_rows],
        )
        _handle_identify_pdfs(selected_booking_rows)

    return selected_booking_rows


def _style_joint_order_groups(
    selection_df: pd.DataFrame,
) -> Any:
    if selection_df.empty or "orderNumber" not in selection_df.columns or "bookingCount" not in selection_df.columns:
        return selection_df

    grouped_rows = selection_df.loc[selection_df["bookingCount"].fillna(1).astype(int) > 1].copy()
    if grouped_rows.empty:
        return selection_df

    grouped_order_numbers = [
        order_number
        for order_number in grouped_rows["orderNumber"].fillna("").astype(str).str.strip().tolist()
        if order_number
    ]
    color_by_order_number = {
        order_number: AMAZON_DUPLICATE_ORDER_COLORS[index % len(AMAZON_DUPLICATE_ORDER_COLORS)]
        for index, order_number in enumerate(sorted(set(grouped_order_numbers)))
    }

    def _highlight_joint_processing_rows(row: pd.Series) -> list[str]:
        order_number = str(row.get("orderNumber", "")).strip()
        row_color = color_by_order_number.get(order_number, "")
        return [
            f"background-color: {row_color}"
            if row.index[column_index] in {"bookingRefs", "bookingCount", "jointProcessing", "orderNumber"}
            and row_color
            and int(row.get("bookingCount", 1) or 1) > 1
            else ""
            for column_index, _ in enumerate(row)
        ]

    return selection_df.style.apply(
        _highlight_joint_processing_rows,
        axis=1,
    )


def _sync_status_filter_options(status_labels: list[str]) -> None:
    previous_options = st.session_state.get(AMAZON_STATUS_FILTER_OPTIONS_KEY)
    current_selection = st.session_state.get(AMAZON_STATUS_FILTER_KEY)
    if not status_labels:
        st.session_state[AMAZON_STATUS_FILTER_OPTIONS_KEY] = []
        st.session_state[AMAZON_STATUS_FILTER_KEY] = []
        return
    if previous_options != status_labels:
        st.session_state[AMAZON_STATUS_FILTER_OPTIONS_KEY] = status_labels
        st.session_state[AMAZON_STATUS_FILTER_KEY] = status_labels
        return
    if not isinstance(current_selection, list):
        st.session_state[AMAZON_STATUS_FILTER_KEY] = status_labels
        return
    filtered_selection = [label for label in current_selection if label in status_labels]
    if filtered_selection != current_selection:
        st.session_state[AMAZON_STATUS_FILTER_KEY] = filtered_selection or status_labels


def render_processing_results_section(selected_booking_rows: list[dict[str, Any]]) -> None:
    if not selected_booking_rows:
        st.caption("Select at least one booking to review the processing result.")
        return

    result_items = _build_processing_result_items(selected_booking_rows)
    if not result_items:
        st.caption("Run `Process bookings` for the selected booking(s) to inspect the result here.")
        return

    result_index = _coerce_processing_result_index(len(result_items))
    current_result = result_items[result_index]
    booking_row = current_result["booking_row"]
    pdf_match = current_result["pdf_match"]
    llm_result = current_result["llm_result"]
    voucher_payload_state = current_result["voucher_payload_state"]
    booking_id = str(booking_row.get("id", ""))
    order_number = str(pdf_match.get("orderNumber", "")).strip() or "-"
    booking_count = len(get_amazon_booking_rows(booking_row))

    st.subheader("Processing Result")
    _render_processing_navigation(result_index=result_index, total_results=len(result_items))
    st.markdown(
        f"**Order `{order_number}` | {booking_count} booking(s) | Selection `{booking_id}`**"
    )
    _render_processing_summary(
        booking_row=booking_row,
        pdf_match=pdf_match,
        llm_result=llm_result,
        voucher_payload_state=voucher_payload_state,
    )
    _render_booking_upload_status(voucher_payload_state)

    if not current_result["cards"]:
        logger.warning(
            "Amazon processing result has no matching receipt PDFs for booking_id=%s order_number=%s",
            booking_id,
            order_number,
        )
        st.warning(
            "No matching receipt PDF was found for this order. "
            f"Booking `{booking_id}` | Order `{order_number}`"
        )
        return

    for card_index, card in enumerate(current_result["cards"], start=1):
        if card_index > 1:
            st.divider()
        _render_compact_processing_card(
            pdf_path=card["pdf_path"],
            entry_index=card["entry_index"],
            page_number=card.get("page_number"),
            extraction_entry=card["extraction_entry"],
            voucher_entry=card["voucher_entry"],
            voucher_entries=current_result["voucher_entries"],
            voucher_payload_state=voucher_payload_state,
            llm_result=llm_result,
        )


def _build_processing_result_items(
    selected_booking_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result_items: list[dict[str, Any]] = []
    for booking_row in selected_booking_rows:
        booking_id = str(booking_row.get("id", ""))
        pdf_match = _find_pdf_match_for_booking(booking_id)
        if pdf_match is None:
            continue

        llm_result = _get_booking_llm_result(booking_id)
        voucher_payload_state = _get_booking_voucher_payload(booking_id)
        pdf_extractions = _coerce_pdf_extractions(llm_result)
        voucher_entries = _coerce_voucher_entries(voucher_payload_state)
        extraction_entries_by_source = {
            str(entry.get("sourceKey", "")).strip(): entry
            for entry in pdf_extractions
            if isinstance(entry, dict) and str(entry.get("sourceKey", "")).strip()
        }
        voucher_entries_by_source = {
            str(entry.get("sourceKey", "")).strip(): entry
            for entry in voucher_entries
            if isinstance(entry, dict) and str(entry.get("sourceKey", "")).strip()
        }

        pdf_paths = pdf_match.get("pdfPaths", [])
        if not pdf_paths and not pdf_extractions:
            result_items.append(
                {
                    "booking_row": booking_row,
                    "pdf_match": pdf_match,
                    "llm_result": llm_result,
                    "voucher_payload_state": voucher_payload_state,
                    "voucher_entries": voucher_entries,
                    "cards": [],
                }
            )
            continue

        cards: list[dict[str, Any]] = []
        if pdf_extractions:
            for entry_index, extraction_entry in enumerate(pdf_extractions, start=1):
                pdf_path_value = str(extraction_entry.get("pdfPath", "")).strip()
                source_key = str(extraction_entry.get("sourceKey", "")).strip()
                cards.append(
                    {
                        "pdf_path": pdf_path_value,
                        "entry_index": entry_index,
                        "page_number": extraction_entry.get("pageNumber"),
                        "extraction_entry": extraction_entries_by_source.get(source_key),
                        "voucher_entry": voucher_entries_by_source.get(source_key),
                    }
                )
        else:
            for entry_index, pdf_path in enumerate(pdf_paths, start=1):
                pdf_path_value = str(pdf_path).strip()
                cards.append(
                    {
                        "pdf_path": pdf_path_value,
                        "entry_index": entry_index,
                        "page_number": None,
                        "extraction_entry": None,
                        "voucher_entry": None,
                    }
                )
        result_items.append(
            {
                "booking_row": booking_row,
                "pdf_match": pdf_match,
                "llm_result": llm_result,
                "voucher_payload_state": voucher_payload_state,
                "voucher_entries": voucher_entries,
                "cards": cards,
            }
        )

    return result_items


def _coerce_processing_result_index(total_results: int) -> int:
    current_index = st.session_state.get(AMAZON_RESULT_CURSOR_KEY, 0)
    if not isinstance(current_index, int):
        current_index = 0
    clamped_index = max(0, min(current_index, total_results - 1))
    if clamped_index != current_index:
        st.session_state[AMAZON_RESULT_CURSOR_KEY] = clamped_index
    return clamped_index


def _render_processing_navigation(*, result_index: int, total_results: int) -> None:
    nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])
    with nav_col1:
        if st.button(
            "Back",
            width="stretch",
            disabled=result_index <= 0,
            key="sevdesk_sparkasse_amazon_result_back",
        ):
            st.session_state[AMAZON_RESULT_CURSOR_KEY] = result_index - 1
            st.rerun()
    with nav_col2:
        st.caption(f"Order {result_index + 1} of {total_results}")
    with nav_col3:
        if st.button(
            "Next",
            width="stretch",
            disabled=result_index >= total_results - 1,
            key="sevdesk_sparkasse_amazon_result_next",
        ):
            st.session_state[AMAZON_RESULT_CURSOR_KEY] = result_index + 1
            st.rerun()


def _find_pdf_match_for_booking(booking_id: str) -> dict[str, Any] | None:
    pdf_matches = st.session_state.get(AMAZON_PDF_MATCHES_KEY)
    if not isinstance(pdf_matches, list):
        return None
    for pdf_match in pdf_matches:
        if str(pdf_match.get("id", "")) == booking_id:
            return pdf_match
    return None


def _get_booking_llm_result(booking_id: str) -> dict[str, Any] | None:
    llm_state = st.session_state.get(AMAZON_LLM_RESULTS_KEY)
    if isinstance(llm_state, dict):
        if "bookingId" in llm_state:
            return llm_state if str(llm_state.get("bookingId", "")) == booking_id else None
        booking_result = llm_state.get(booking_id)
        return booking_result if isinstance(booking_result, dict) else None
    return None


def _get_booking_voucher_payload(booking_id: str) -> dict[str, Any] | None:
    voucher_state = st.session_state.get(AMAZON_VOUCHER_PAYLOADS_KEY)
    if isinstance(voucher_state, dict):
        if "bookingId" in voucher_state:
            return voucher_state if str(voucher_state.get("bookingId", "")) == booking_id else None
        booking_payload = voucher_state.get(booking_id)
        return booking_payload if isinstance(booking_payload, dict) else None
    return None


def _store_booking_llm_result(booking_id: str, aggregate_result: dict[str, Any]) -> None:
    llm_state = st.session_state.get(AMAZON_LLM_RESULTS_KEY)
    llm_results_by_booking = {} if not isinstance(llm_state, dict) or "bookingId" in llm_state else dict(llm_state)
    llm_results_by_booking[booking_id] = aggregate_result
    st.session_state[AMAZON_LLM_RESULTS_KEY] = llm_results_by_booking


def _store_booking_voucher_payload(booking_id: str, voucher_payload_state: dict[str, Any] | None) -> None:
    voucher_state = st.session_state.get(AMAZON_VOUCHER_PAYLOADS_KEY)
    voucher_payloads_by_booking = (
        {} if not isinstance(voucher_state, dict) or "bookingId" in voucher_state else dict(voucher_state)
    )
    if voucher_payload_state is None:
        voucher_payloads_by_booking.pop(booking_id, None)
    else:
        voucher_payloads_by_booking[booking_id] = voucher_payload_state
    st.session_state[AMAZON_VOUCHER_PAYLOADS_KEY] = voucher_payloads_by_booking


def _render_processing_summary(
    *,
    booking_row: dict[str, Any],
    pdf_match: dict[str, Any],
    llm_result: dict[str, Any] | None,
    voucher_payload_state: dict[str, Any] | None,
) -> None:
    uploaded_count, total_voucher_entries = _booking_upload_counts(voucher_payload_state)
    booking_count = len(get_amazon_booking_rows(booking_row))
    booking_amount = aggregate_amazon_booking_amount(booking_row)
    summary_cols = st.columns(6)
    summary_cols[0].metric("Booking Amount", format_currency_value(booking_amount))
    summary_cols[1].metric("Bookings", str(booking_count))
    summary_cols[2].metric("Matched PDFs", str(pdf_match.get("pdfCount", 0)))
    summary_cols[3].metric(
        "PDF Sum",
        format_currency_value(llm_result.get("sumExtractedAmount")) if llm_result else "-",
    )
    summary_cols[4].metric(
        "Amount Match",
        _match_summary_label(llm_result.get("aggregateMatch") if llm_result else None),
    )
    summary_cols[5].metric("Uploaded", f"{uploaded_count}/{total_voucher_entries}")


def _booking_upload_counts(voucher_payload_state: dict[str, Any] | None) -> tuple[int, int]:
    voucher_entries = _coerce_voucher_entries(voucher_payload_state)
    uploaded_count = sum(
        1
        for voucher_entry in voucher_entries
        if isinstance(voucher_entry.get("createdVoucher"), dict)
    )
    return uploaded_count, len(voucher_entries)


def _render_booking_upload_status(voucher_payload_state: dict[str, Any] | None) -> None:
    uploaded_count, total_voucher_entries = _booking_upload_counts(voucher_payload_state)
    if total_voucher_entries == 0:
        st.info("Upload status: no voucher payload has been generated for this order yet.")
        return
    if uploaded_count == 0:
        st.info("Upload status: no voucher for this order has been uploaded yet.")
        return
    if uploaded_count == total_voucher_entries:
        st.success(
            f"Upload status: all vouchers for this order are already uploaded ({uploaded_count}/{total_voucher_entries})."
        )
        return
    st.warning(
        f"Upload status: {uploaded_count}/{total_voucher_entries} vouchers for this order are already uploaded."
    )


def _coerce_pdf_extractions(llm_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(llm_result, dict):
        return []
    pdf_extractions = llm_result.get("pdfExtractions")
    if isinstance(pdf_extractions, list):
        return [entry for entry in pdf_extractions if isinstance(entry, dict)]
    if isinstance(llm_result.get("extracted"), dict):
        return [llm_result]
    return []


def _coerce_voucher_entries(voucher_payload_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(voucher_payload_state, dict):
        return []
    voucher_entries = voucher_payload_state.get("entries")
    if isinstance(voucher_entries, list):
        return [entry for entry in voucher_entries if isinstance(entry, dict)]
    return []


def _render_compact_processing_card(
    *,
    pdf_path: str,
    entry_index: int,
    page_number: int | None,
    extraction_entry: dict[str, Any] | None,
    voucher_entry: dict[str, Any] | None,
    voucher_entries: list[dict[str, Any]],
    voucher_payload_state: dict[str, Any] | None,
    llm_result: dict[str, Any] | None,
) -> None:
    page_count = extraction_entry.get("pageCount") if isinstance(extraction_entry, dict) else None
    pdf_label = (
        f"{Path(pdf_path).name} | Page {page_number}"
        if pdf_path and isinstance(page_number, int)
        else (Path(pdf_path).name if pdf_path else f"PDF {entry_index}")
    )
    comparison_rows = extraction_entry.get("comparison", []) if isinstance(extraction_entry, dict) else []
    amount_match = _comparison_match_label(comparison_rows, "Betrag")
    if amount_match == "-":
        amount_match = _comparison_match_label(comparison_rows, "Betrag (Seite)")
    if amount_match == "-" and isinstance(llm_result, dict):
        amount_match = _match_summary_label(llm_result.get("aggregateMatch"))
    date_match = _comparison_match_label(comparison_rows, "Datum")
    amount_label = "Order amount" if isinstance(page_count, int) and page_count > 1 else "Amount"

    with st.container(border=True):
        title_col, match_col, detail_col = st.columns([4, 3, 1])
        with title_col:
            st.markdown(f"**{pdf_label}**")
            st.caption(pdf_path)
        with match_col:
            st.caption(
                " | ".join(
                    [
                        f"{amount_label}: {amount_match}",
                        f"Date: {date_match}",
                        f"Pages: {page_count if isinstance(page_count, int) else '-'}",
                    ]
                )
            )
        with detail_col:
            with st.popover("Details"):
                _render_processing_details_popover(
                    pdf_path=pdf_path,
                    extraction_entry=extraction_entry,
                    voucher_entry=voucher_entry,
                )

        pdf_col, result_col = st.columns([3, 2])
        with pdf_col:
            render_pdf_inline(pdf_path, height=480)
        with result_col:
            if isinstance(extraction_entry, dict):
                st.dataframe(
                    pd.DataFrame(build_extracted_accounting_rows(extraction_entry.get("extracted", {}))),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("No extraction result available for this PDF.")

            _render_compact_voucher_info(voucher_entry)

            if isinstance(voucher_entry, dict) and isinstance(voucher_payload_state, dict):
                _render_compact_voucher_actions(
                    voucher_entry=voucher_entry,
                    voucher_entries=voucher_entries,
                    voucher_payload_state=voucher_payload_state,
                    llm_result=llm_result,
                    entry_index=entry_index,
                )


def _render_processing_details_popover(
    *,
    pdf_path: str,
    extraction_entry: dict[str, Any] | None,
    voucher_entry: dict[str, Any] | None,
) -> None:
    st.caption(f"PDF: `{pdf_path}`")
    if isinstance(extraction_entry, dict):
        st.markdown("**Comparison**")
        st.dataframe(pd.DataFrame(extraction_entry.get("comparison", [])), width="stretch", hide_index=True)
        _cache_payload_caption("Raw extraction", f"amazon_raw_extraction_{Path(pdf_path).stem}", extraction_entry.get("extracted", {}))
    if isinstance(voucher_entry, dict):
        _cache_payload_caption("Voucher payload", f"amazon_voucher_payload_{Path(pdf_path).stem}", voucher_entry.get("payload", {}))
        create_customer_response = voucher_entry.get("createCustomerResponse")
        if create_customer_response:
            _cache_payload_caption(
                "Create customer response",
                f"amazon_create_customer_response_{Path(pdf_path).stem}",
                create_customer_response,
            )
        create_response = voucher_entry.get("createResponse")
        if create_response:
            _cache_payload_caption(
                "Create voucher response",
                f"amazon_create_voucher_response_{Path(pdf_path).stem}",
                create_response,
            )


def _render_compact_voucher_info(voucher_entry: dict[str, Any] | None) -> None:
    if not isinstance(voucher_entry, dict):
        st.caption("No voucher payload available for this PDF.")
        return

    payload = voucher_entry.get("payload", {})
    voucher = payload.get("voucher", {}) if isinstance(payload, dict) else {}
    voucher_pos = payload.get("voucherPosSave", []) if isinstance(payload, dict) else []
    first_position = voucher_pos[0] if voucher_pos and isinstance(voucher_pos[0], dict) else {}
    customer_summary = _customer_summary(voucher_entry)
    tax_rule_summary = _tax_rule_summary(voucher)
    mwst_summary = first_position.get("taxRate")
    tax_set_summary = _tax_set_summary(voucher)
    voucher_description = str(voucher.get("description", "")).strip() or "-"

    st.markdown(f"**Belegnummer**: {voucher_description}")
    st.markdown(f"**Customer**: {customer_summary}")
    st.markdown(f"**Tax Rule**: {tax_rule_summary}")
    st.markdown(f"**MWST**: {mwst_summary if mwst_summary is not None else '-'}%")
    if tax_set_summary != "-":
        st.markdown(f"**Tax Set**: {tax_set_summary}")

    validation_errors = voucher_entry.get("validationErrors", [])
    if validation_errors:
        st.caption(f"Validation: {len(validation_errors)} issue(s)")
    else:
        st.caption("Validation: ok")

    created_voucher = voucher_entry.get("createdVoucher")
    if isinstance(created_voucher, dict):
        created_voucher_id = str(created_voucher.get("id", "")).strip() or "-"
        st.caption(f"Uploaded voucher id: `{created_voucher_id}`")


def _render_compact_voucher_actions(
    *,
    voucher_entry: dict[str, Any],
    voucher_entries: list[dict[str, Any]],
    voucher_payload_state: dict[str, Any],
    llm_result: dict[str, Any] | None,
    entry_index: int,
) -> None:
    booking_id = str(voucher_payload_state.get("bookingId", "")).strip()
    seller_name = str(voucher_entry.get("sellerName", "")).strip()
    seller_vat_id = str(voucher_entry.get("sellerVatId", "")).strip()
    is_intra_community_supply = voucher_entry.get("isIntraCommunitySupply") is True
    matched_customer = voucher_entry.get("customer")
    created_customer = voucher_entry.get("createdCustomer")
    matched_pdf_path_for_create = str(
        voucher_entry.get("matchedPdfPath")
        or (llm_result.get("pdfPath") if isinstance(llm_result, dict) else "")
        or ""
    ).strip()
    validation_errors = voucher_entry.get("validationErrors", [])
    aggregate_match_for_create = voucher_payload_state.get("aggregateMatch")
    created_voucher = voucher_entry.get("createdVoucher")

    if not created_customer and created_voucher is None and (
        is_intra_community_supply
        and (not isinstance(matched_customer, dict))
        and seller_name
        and seller_vat_id
    ):
        _render_create_customer_button(
            voucher_entry=voucher_entry,
            seller_name=seller_name,
            seller_vat_id=seller_vat_id,
            voucher_entries=voucher_entries,
            voucher_payload_state=voucher_payload_state,
            booking_id=booking_id,
            entry_index=entry_index,
        )

    if created_voucher is None:
        _render_create_voucher_button(
            voucher_entry=voucher_entry,
            voucher_entries=voucher_entries,
            voucher_payload_state=voucher_payload_state,
            matched_pdf_path_for_create=matched_pdf_path_for_create,
            validation_errors=validation_errors,
            aggregate_match_for_create=aggregate_match_for_create,
            booking_id=booking_id,
            entry_index=entry_index,
        )


def _customer_summary(voucher_entry: dict[str, Any]) -> str:
    customer = voucher_entry.get("createdCustomer") or voucher_entry.get("customer")
    if isinstance(customer, dict):
        return f"{format_customer_display_name(customer)} ({customer.get('id', '-')})"
    if voucher_entry.get("isIntraCommunitySupply") is True:
        seller_vat_id = str(voucher_entry.get("sellerVatId", "")).strip()
        if seller_vat_id:
            return f"Missing match for VAT `{seller_vat_id}`"
        return "No VAT id extracted"
    return AMAZON_DEFAULT_CUSTOMER_NAME


def _tax_rule_summary(voucher: dict[str, Any]) -> str:
    tax_rule = voucher.get("taxRule")
    if not isinstance(tax_rule, dict):
        return "-"
    name = str(tax_rule.get("name", "")).strip()
    tax_rule_id = str(tax_rule.get("id", "")).strip()
    if name and tax_rule_id:
        return f"{name} ({tax_rule_id})"
    return name or tax_rule_id or "-"


def _tax_set_summary(voucher: dict[str, Any]) -> str:
    tax_set = voucher.get("taxSet")
    if not isinstance(tax_set, dict):
        return "-"
    name = str(tax_set.get("name", "")).strip()
    tax_set_id = str(tax_set.get("id", "")).strip()
    if name and tax_set_id:
        return f"{name} ({tax_set_id})"
    return name or tax_set_id or "-"


def _comparison_match_label(comparison_rows: list[dict[str, Any]], field_name: str) -> str:
    for row in comparison_rows:
        if str(row.get("field", "")) == field_name:
            return str(row.get("match", "-"))
    return "-"


def _match_summary_label(value: Any) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "-"


def _handle_identify_pdfs(selected_booking_rows: list[dict[str, Any]]) -> None:
    clear_amazon_analysis_state()
    if not selected_booking_rows:
        report_error("Select at least one booking before processing.")
        return

    st.session_state[AMAZON_RESULT_CURSOR_KEY] = 0
    selected_booking_ids = {str(row.get("id", "")) for row in selected_booking_rows}
    pdf_matches = [
        match
        for match in build_selected_pdf_matches(selected_booking_rows)
        if str(match.get("id", "")) in selected_booking_ids
    ]
    st.session_state[AMAZON_PDF_MATCHES_KEY] = pdf_matches

    api_key = resolve_llm_api_key(
        AMAZON_EXTRACTION_PROVIDER,
        session_state=st.session_state,
        secrets=st.secrets,
        environ=os.environ,
    )
    if not api_key:
        st.warning(
            f"{AMAZON_EXTRACTION_PROVIDER}-API Key nicht gefunden. "
            "PDF wurde identifiziert, LLM-Extraktion wurde uebersprungen."
        )
        return

    booking_rows_by_id = {str(row.get("id", "")): row for row in selected_booking_rows}
    matched_pdf_count = sum(len(match.get("pdfPaths", [])) for match in pdf_matches)
    with st.spinner(
        f"Analysing {matched_pdf_count} PDF(s) for {len(selected_booking_rows)} booking(s) with LLM..."
    ):
        try:
            accounting_type_rows = st.session_state.get("sevdesk_accounting_types_rows")
            if accounting_type_rows is None:
                accounting_type_rows = load_stored_accounting_types()
            check_account_rows = st.session_state.get("sevdesk_check_accounts_rows")
            if check_account_rows is None:
                check_account_rows = load_stored_check_accounts()
            customer_rows = st.session_state.get(AMAZON_CUSTOMERS_SESSION_KEY)
            if customer_rows is None:
                customer_rows = refresh_live_amazon_customers(report_errors=True)

            for pdf_match in pdf_matches:
                booking_id = str(pdf_match.get("id", ""))
                selected_booking = booking_rows_by_id.get(booking_id)
                if not isinstance(selected_booking, dict):
                    continue
                try:
                    matched_pdf_paths = pdf_match.get("pdfPaths", [])
                    if not matched_pdf_paths:
                        _store_booking_voucher_payload(booking_id, None)
                        continue

                    extraction_results: list[dict[str, Any]] = []
                    for matched_pdf_path in matched_pdf_paths:
                        logger.info(
                            "Amazon accounting extraction requested booking_id=%s pdf=%s provider=%s model=%s",
                            selected_booking.get("id"),
                            matched_pdf_path,
                            AMAZON_EXTRACTION_PROVIDER,
                            AMAZON_EXTRACTION_MODEL,
                        )
                        page_extraction_results = extract_accounting_data_from_pdf(
                            pdf_path=matched_pdf_path,
                            provider=AMAZON_EXTRACTION_PROVIDER,
                            model_name=AMAZON_EXTRACTION_MODEL,
                            api_key=api_key,
                        )
                        extraction_results.extend(page_extraction_results)

                    compare_amount_to_booking = len(extraction_results) == 1
                    for extraction_result in extraction_results:
                        extraction_result["bookingId"] = booking_id
                        extraction_result["comparison"] = build_accounting_comparison_rows(
                            selected_booking,
                            extraction_result["extracted"],
                            compare_amount_to_booking=compare_amount_to_booking,
                        )

                    aggregate_match = aggregate_booking_receipt_match(selected_booking, extraction_results)
                    aggregate_result = {
                        "bookingId": booking_id,
                        "provider": AMAZON_EXTRACTION_PROVIDER,
                        "model": AMAZON_EXTRACTION_MODEL,
                        "pdfExtractions": extraction_results,
                        "comparison": build_aggregate_accounting_comparison_rows(
                            selected_booking,
                            extraction_results,
                        ),
                        "sumExtractedAmount": sum_extracted_pdf_amounts(extraction_results),
                        "aggregateMatch": aggregate_match,
                    }
                    _store_booking_llm_result(booking_id, aggregate_result)

                    voucher_entries = build_voucher_payload_entries(
                        booking_row=selected_booking,
                        extraction_results=extraction_results,
                        accounting_type_rows=accounting_type_rows,
                        check_account_rows=check_account_rows,
                        customer_rows=customer_rows or [],
                    )
                    if voucher_entries:
                        _store_booking_voucher_payload(
                            booking_id,
                            {
                                "bookingId": booking_id,
                                "aggregateMatch": aggregate_match,
                                "entries": voucher_entries,
                            },
                        )
                    else:
                        _store_booking_voucher_payload(booking_id, None)
                except Exception as booking_exc:
                    report_error(
                        f"LLM extraction failed for booking {booking_id}: {booking_exc}",
                        log_message=f"Amazon accounting extraction failed booking_id={booking_id}",
                        exc_info=True,
                    )
        except Exception as exc:
            report_error(
                f"LLM extraction failed: {exc}",
                log_message=(
                    "Amazon accounting extraction failed "
                    f"booking_ids={sorted(selected_booking_ids)}"
                ),
                exc_info=True,
            )


def render_pdf_matches_section() -> None:
    pdf_matches = st.session_state.get("sevdesk_sparkasse_amazon_pdf_matches")
    if not pdf_matches:
        st.caption("Identify PDFs for a selected booking to inspect the matches here.")
        return

    st.dataframe(pd.DataFrame(pdf_matches), width="stretch")
    for match in pdf_matches:
        st.markdown(
            f"**Booking {match['id']} | Order {match['orderNumber']} | PDFs: {match['pdfCount']}**"
        )
        if not match["pdfPaths"]:
            st.info("No PDF found for this booking.")
            continue
        for pdf_path in match["pdfPaths"]:
            st.caption(pdf_path)


def render_extraction_results_section(selected_booking_rows: list[dict[str, Any]]) -> None:
    llm_result = st.session_state.get("sevdesk_sparkasse_amazon_llm_result")
    if not (
        llm_result
        and len(selected_booking_rows) == 1
        and llm_result.get("bookingId") == str(selected_booking_rows[0].get("id", ""))
    ):
        st.caption("Run PDF identification and extraction for a single selected booking to inspect results.")
        return

    pdf_extractions = llm_result.get("pdfExtractions")
    if not isinstance(pdf_extractions, list):
        pdf_extractions = [llm_result] if isinstance(llm_result.get("extracted"), dict) else []
    aggregate_match = llm_result.get("aggregateMatch")
    st.markdown("**Booking Comparison**")
    if aggregate_match is True:
        st.success("Match: Die Summe der extrahierten PDF-Seitenbetraege stimmt mit der Buchung ueberein.")
    elif aggregate_match is False:
        st.warning("Die Summe der extrahierten PDF-Seitenbetraege stimmt nicht mit der Buchung ueberein.")
    st.dataframe(pd.DataFrame(llm_result.get("comparison", [])), width="stretch")
    st.markdown("**Extracted Accounting Data**")
    st.caption(
        "LLM results for all matched PDF pages of the currently selected booking. "
        f"Model: `{llm_result.get('provider')}` / `{llm_result.get('model')}`"
    )
    for index, extraction_entry in enumerate(pdf_extractions, start=1):
        extracted = extraction_entry.get("extracted", {})
        pdf_path = str(extraction_entry.get("pdfPath", "")).strip()
        page_number = extraction_entry.get("pageNumber")
        pdf_label = Path(pdf_path).name if pdf_path else f"PDF {index}"
        if isinstance(page_number, int):
            st.markdown(f"**PDF {index} | {pdf_label} | Page {page_number}**")
        else:
            st.markdown(f"**PDF {index} | {pdf_label}**")
        pdf_col, extracted_col = st.columns(2)
        with pdf_col:
            if pdf_path:
                st.caption(pdf_path)
                render_pdf_inline(pdf_path, height=520)
            else:
                st.info("No PDF path available.")
        with extracted_col:
            st.dataframe(pd.DataFrame(build_extracted_accounting_rows(extracted)), width="stretch")
        _cache_payload_caption(
            f"Raw extraction payload #{index}",
            f"amazon_raw_extraction_{index}_{pdf_label}",
            extracted,
        )


def render_voucher_entries_section(selected_booking_rows: list[dict[str, Any]]) -> None:
    voucher_payload_state = st.session_state.get("sevdesk_sparkasse_amazon_voucher_payload")
    llm_result = st.session_state.get("sevdesk_sparkasse_amazon_llm_result")
    if not (
        voucher_payload_state
        and len(selected_booking_rows) == 1
        and voucher_payload_state.get("bookingId") == str(selected_booking_rows[0].get("id", ""))
    ):
        st.caption("Voucher JSON appears here after a successful extraction run for one booking.")
        return

    voucher_entries = voucher_payload_state.get("entries")
    if not isinstance(voucher_entries, list):
        legacy_payload = voucher_payload_state.get("payload")
        voucher_entries = (
            [
                {
                    "matchedPdfPath": voucher_payload_state.get("matchedPdfPath"),
                    "path": voucher_payload_state.get("path"),
                    "payload": legacy_payload,
                    "sellerName": "",
                    "sellerVatId": "",
                    "isIntraCommunitySupply": False,
                    "customer": None,
                    "validationErrors": voucher_payload_state.get("validationErrors", []),
                    "createCustomerResponse": None,
                    "createdCustomer": None,
                    "createResponse": voucher_payload_state.get("createResponse"),
                    "createdVoucher": voucher_payload_state.get("createdVoucher"),
                }
            ]
            if isinstance(legacy_payload, dict)
            else []
        )

    aggregate_match_for_create = voucher_payload_state.get("aggregateMatch")
    if aggregate_match_for_create is True:
        st.success("A voucher JSON was generated for each matched PDF page.")
    elif aggregate_match_for_create is False:
        st.warning(
            "Voucher JSON files were generated for each matched PDF page, "
            "but API creation is disabled until the summed page amount matches the booking."
        )
    else:
        st.info("Voucher JSON files were generated.")

    for entry_index, voucher_entry in enumerate(voucher_entries, start=1):
        _render_single_voucher_entry(
            voucher_entry=voucher_entry,
            entry_index=entry_index,
            voucher_entries=voucher_entries,
            voucher_payload_state=voucher_payload_state,
            llm_result=llm_result,
        )


def _render_single_voucher_entry(
    *,
    voucher_entry: dict[str, Any],
    entry_index: int,
    voucher_entries: list[dict[str, Any]],
    voucher_payload_state: dict[str, Any],
    llm_result: dict[str, Any] | None,
) -> None:
    matched_pdf_path_for_create = str(
        voucher_entry.get("matchedPdfPath")
        or (llm_result.get("pdfPath") if isinstance(llm_result, dict) else "")
        or ""
    ).strip()
    pdf_label = (
        Path(matched_pdf_path_for_create).name
        if matched_pdf_path_for_create
        else f"PDF {entry_index}"
    )
    page_number = voucher_entry.get("pageNumber")
    if isinstance(page_number, int):
        pdf_label = f"{pdf_label} | Page {page_number}"
    validation_errors = voucher_entry.get("validationErrors", [])
    seller_name = str(voucher_entry.get("sellerName", "")).strip()
    seller_vat_id = str(voucher_entry.get("sellerVatId", "")).strip()
    is_intra_community_supply = voucher_entry.get("isIntraCommunitySupply") is True
    matched_customer = voucher_entry.get("customer")
    created_customer = voucher_entry.get("createdCustomer")
    created_voucher = voucher_entry.get("createdVoucher")
    create_customer_response = voucher_entry.get("createCustomerResponse")
    create_response = voucher_entry.get("createResponse")
    payload = voucher_entry.get("payload", {})
    voucher = payload.get("voucher", {}) if isinstance(payload, dict) else {}
    voucher_description = str(voucher.get("description", "")).strip() or "-"

    with st.container(border=True):
        st.markdown(f"**Voucher JSON {entry_index} | {pdf_label}**")
        st.caption(f"Saved to `{voucher_entry.get('path')}`")
        st.caption(f"Belegnummer: `{voucher_description}`")
        if matched_pdf_path_for_create:
            st.caption(f"Matched PDF: `{matched_pdf_path_for_create}`")
        if seller_vat_id:
            st.caption(f"Extracted USt-IdNr.: `{seller_vat_id}`")

        _render_voucher_customer_status(
            matched_customer=matched_customer,
            is_intra_community_supply=is_intra_community_supply,
            seller_vat_id=seller_vat_id,
        )
        _render_voucher_validation_status(validation_errors)

        _cache_payload_caption(
            "Voucher payload",
            f"amazon_voucher_payload_{entry_index}_{pdf_label}",
            voucher_entry.get("payload", {}),
        )

        if created_customer:
            st.success(
                "Customer was created in sevDesk: "
                f"`{format_customer_display_name(created_customer)}` ({created_customer.get('id', '-')})"
            )
            st.dataframe(pd.DataFrame([created_customer]), width="stretch", hide_index=True)
        elif (
            is_intra_community_supply
            and (not isinstance(matched_customer, dict))
            and seller_name
            and seller_vat_id
        ):
            _render_create_customer_button(
                voucher_entry=voucher_entry,
                seller_name=seller_name,
                seller_vat_id=seller_vat_id,
                voucher_entries=voucher_entries,
                voucher_payload_state=voucher_payload_state,
                booking_id=str(voucher_payload_state.get("bookingId", "")).strip(),
                entry_index=entry_index,
            )

        if create_customer_response:
            _cache_payload_caption(
                f"Create customer API response #{entry_index}",
                f"amazon_create_customer_response_{entry_index}_{pdf_label}",
                create_customer_response,
            )

        if created_voucher:
            created_voucher_id = str(created_voucher.get("id", "")).strip() or "-"
            st.success(f"Voucher was created in sevDesk with id `{created_voucher_id}`.")
            st.dataframe(pd.DataFrame([format_voucher_row(created_voucher)]), width="stretch")
        else:
            _render_create_voucher_button(
                voucher_entry=voucher_entry,
                voucher_entries=voucher_entries,
                voucher_payload_state=voucher_payload_state,
                matched_pdf_path_for_create=matched_pdf_path_for_create,
                validation_errors=validation_errors,
                aggregate_match_for_create=voucher_payload_state.get("aggregateMatch"),
                booking_id=str(voucher_payload_state.get("bookingId", "")).strip(),
                entry_index=entry_index,
            )

        if create_response:
            _cache_payload_caption(
                f"Create voucher API response #{entry_index}",
                f"amazon_create_voucher_response_{entry_index}_{pdf_label}",
                create_response,
            )


def _render_voucher_customer_status(
    *,
    matched_customer: Any,
    is_intra_community_supply: bool,
    seller_vat_id: str,
) -> None:
    if isinstance(matched_customer, dict):
        customer_name = format_customer_display_name(matched_customer)
        if is_intra_community_supply:
            st.success(
                "Customer matched by USt-IdNr.: "
                f"`{customer_name}` ({matched_customer.get('id', '-')})"
            )
        else:
            st.success(
                "Customer fixed to non-innergemeinschaftliche default: "
                f"`{customer_name}` ({matched_customer.get('id', '-')})"
            )
        return

    if is_intra_community_supply and seller_vat_id:
        st.warning("No existing sevDesk customer was found for the extracted USt-IdNr.")
        return
    if is_intra_community_supply:
        st.info("No USt-IdNr. was extracted from this PDF, so no customer match is possible.")
        return
    st.warning(
        f"No live sevDesk customer named `{AMAZON_DEFAULT_CUSTOMER_NAME}` was found. "
        "The voucher JSON still uses that supplier name."
    )


def _render_voucher_validation_status(validation_errors: list[Any]) -> None:
    if validation_errors:
        st.warning("Voucher JSON was generated, but validation reported issues.")
        for error in validation_errors:
            st.write(f"- {error}")
    else:
        st.success("Voucher JSON generated and validated successfully.")


def _render_create_customer_button(
    *,
    voucher_entry: dict[str, Any],
    seller_name: str,
    seller_vat_id: str,
    voucher_entries: list[dict[str, Any]],
    voucher_payload_state: dict[str, Any],
    booking_id: str,
    entry_index: int,
) -> None:
    if not st.button(
        "Create customer in sevDesk and update voucher JSON",
        width="stretch",
        key=f"sevdesk_create_customer_{booking_id}_{entry_index}",
    ):
        return

    token = ensure_token()
    if not token:
        return

    with st.spinner("Creating customer in sevDesk..."):
        try:
            customer_rows = st.session_state.get(AMAZON_CUSTOMERS_SESSION_KEY)
            if customer_rows is None:
                customer_rows = refresh_live_amazon_customers(
                    token=token,
                    report_errors=True,
                )
            customer_payload = build_customer_create_payload(
                seller_name=seller_name,
                seller_vat_id=seller_vat_id,
                customer_rows=customer_rows or [],
            )
            customer_response = create_contact(
                base_url(),
                token,
                customer_payload,
            )
            created_customer_row = coerce_created_customer_row(
                customer_response,
                fallback_name=str(customer_payload.get("name", "")).strip(),
                fallback_vat_id=seller_vat_id,
                fallback_customer_number=str(customer_payload.get("customerNumber", "")).strip(),
            )
            refreshed_customer_rows = refresh_live_amazon_customers(
                token=token,
                report_errors=True,
            ) or []
            created_customer_row = (
                find_customer_by_vat_id(refreshed_customer_rows, seller_vat_id)
                or find_customer_by_name(
                    refreshed_customer_rows,
                    str(customer_payload.get("name", "")).strip(),
                )
                or created_customer_row
            )

            updated_entries: list[dict[str, Any]] = []
            for existing_entry in voucher_entries:
                entry_vat_id = existing_entry.get("sellerVatId")
                if normalize_vat_id(entry_vat_id) == normalize_vat_id(seller_vat_id):
                    updated_entry = persist_updated_voucher_entry(
                        {
                            **existing_entry,
                            "createCustomerResponse": customer_response,
                            "createdCustomer": created_customer_row,
                        },
                        customer_row=created_customer_row,
                    )
                else:
                    updated_entry = existing_entry
                updated_entries.append(updated_entry)

            _store_booking_voucher_payload(
                booking_id,
                {
                    **voucher_payload_state,
                    "entries": updated_entries,
                },
            )
            st.rerun()
        except Exception as exc:
            report_error(
                f"Failed to create customer: {exc}",
                log_message="Failed to create customer in sevDesk",
                exc_info=True,
            )


def _render_create_voucher_button(
    *,
    voucher_entry: dict[str, Any],
    voucher_entries: list[dict[str, Any]],
    voucher_payload_state: dict[str, Any],
    matched_pdf_path_for_create: str,
    validation_errors: list[Any],
    aggregate_match_for_create: Any,
    booking_id: str,
    entry_index: int,
) -> None:
    if not st.button(
        "Create voucher via API for this PDF",
        width="stretch",
        disabled=bool(validation_errors) or aggregate_match_for_create is not True,
        key=f"sevdesk_create_voucher_{booking_id}_{entry_index}",
    ):
        return

    token = ensure_token()
    if not token:
        return

    with st.spinner("Creating voucher in sevDesk..."):
        request_payload: dict[str, Any] = {}
        try:
            request_payload = normalize_create_payload(voucher_entry.get("payload", {}))
            voucher = request_payload.get("voucher")
            if isinstance(voucher, dict):
                voucher["document"] = None
            if matched_pdf_path_for_create:
                remote_filename = upload_voucher_temp_file(
                    base_url(),
                    token,
                    matched_pdf_path_for_create,
                )
                request_payload["filename"] = remote_filename
            else:
                request_payload["filename"] = None
            logger.info(
                "Amazon voucher upload payload booking_id=%s entry_index=%s pdf=%s payload=%s",
                booking_id,
                entry_index,
                matched_pdf_path_for_create or "-",
                json.dumps(request_payload, ensure_ascii=True, sort_keys=True, default=str, indent=2),
            )
            response_payload = create_voucher(
                base_url(),
                token,
                request_payload,
            )
            created_summary = first_object_from_response(response_payload) or {}
            created_voucher_id = str(created_summary.get("id", "")).strip()
            created_voucher = (
                request_voucher_by_id(base_url(), token, created_voucher_id)
                if created_voucher_id
                else None
            )
            updated_entries = list(voucher_entries)
            updated_entries[entry_index - 1] = {
                **voucher_entry,
                "matchedPdfPath": matched_pdf_path_for_create or None,
                "payload": request_payload,
                "createResponse": response_payload,
                "createdVoucher": created_voucher or created_summary,
            }
            _store_booking_voucher_payload(
                booking_id,
                {
                    **voucher_payload_state,
                    "entries": updated_entries,
                },
            )
            if created_voucher_id:
                st.session_state["sevdesk_latest_belege_rows"] = request_vouchers_with_tags(
                    base_url(),
                    token,
                    10,
                )
            st.success(
                "Voucher created successfully in sevDesk."
                + (f" New id: `{created_voucher_id}`." if created_voucher_id else "")
            )
            st.rerun()
        except Exception as exc:
            logger.error(
                "Failed create-voucher payload: %s",
                json.dumps(request_payload, ensure_ascii=True, sort_keys=True),
            )
            report_error(
                f"Failed to create voucher: {exc}",
                log_message="Failed to create voucher in sevDesk",
                exc_info=True,
            )
