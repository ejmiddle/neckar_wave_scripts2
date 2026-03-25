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
    aggregate_booking_receipt_match,
    build_accounting_comparison_rows,
    build_aggregate_accounting_comparison_rows,
    build_amazon_selection_dataframe,
    build_extracted_accounting_rows,
    build_selected_pdf_matches,
    extract_accounting_data_from_pdf,
    format_status_option,
    sum_extracted_pdf_amounts,
)
from src.accounting.amazon_vouchers import build_voucher_payload_entries
from src.accounting.common import (
    base_url,
    ensure_token,
    filter_rows_by_date_range,
    find_check_account_by_name,
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
)
from src.accounting.ui.displays import (
    render_pdf_inline,
    show_amazon_customers,
    show_amazon_payments,
)
from src.amazon_accounting_llm import (
    LLM_MODELS_BY_PROVIDER,
    LLM_PROVIDER_GOOGLE,
    LLM_PROVIDER_OPENAI,
    resolve_llm_api_key,
)
from src.logging_config import logger
from src.sevdesk.api import (
    create_contact,
    create_voucher,
    fetch_all_transactions_for_check_account,
    request_voucher_by_id,
    request_vouchers,
    upload_voucher_temp_file,
)
from src.sevdesk.voucher import first_object_from_response, normalize_create_payload


def render_amazon_setup_section() -> tuple[str, str, list[dict[str, Any]] | None]:
    st.subheader("Sparkasse Amazon Payments")
    st.caption(
        "Filters Sparkasse bookings where `payeePayerName` contains "
        f"`{AMAZON_PAYEE_NAME}`."
    )

    setup_col, customers_col = st.columns([2, 3])
    with setup_col:
        llm_provider = st.selectbox(
            "LLM Provider",
            [LLM_PROVIDER_OPENAI, LLM_PROVIDER_GOOGLE],
            index=0,
            key="sevdesk_sparkasse_amazon_llm_provider",
            help="Wähle den LLM-Anbieter für die Beleganalyse.",
        )
        extract_model = st.selectbox(
            "Extraktionsmodell (API)",
            LLM_MODELS_BY_PROVIDER.get(llm_provider, LLM_MODELS_BY_PROVIDER[LLM_PROVIDER_OPENAI]),
            index=0,
            key="sevdesk_sparkasse_amazon_llm_model",
            help="Dieses Modell wird verwendet, um PDF-Seiten strukturiert auszulesen.",
        )
        default_end_date = st.session_state.get("amazon_end_date_default") or date.today()
        default_start_date = default_end_date - timedelta(days=30)
        amazon_start_date = st.date_input("Start date", value=default_start_date, key="amazon_start_date")
        amazon_end_date = st.date_input("End date", value=default_end_date, key="amazon_end_date")
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
                        "No stored check accounts found. Fetch them first in the Accounting Backend page."
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
                                if AMAZON_PAYEE_NAME in str(row.get("payeePayerName", ""))
                            ]
                            st.session_state["sevdesk_sparkasse_amazon_rows"] = filtered_rows
                        except Exception as exc:
                            report_error(
                                f"Failed to load Sparkasse Amazon payments: {exc}",
                                log_message="Failed to load Sparkasse Amazon payments",
                                exc_info=True,
                            )

    with customers_col:
        st.markdown("**Amazon Customers**")
        show_amazon_customers(st.session_state.get(AMAZON_CUSTOMERS_SESSION_KEY))

    amazon_rows = st.session_state.get("sevdesk_sparkasse_amazon_rows")
    if amazon_rows:
        available_statuses = sorted({str(row.get("status", "")) for row in amazon_rows})
        status_options = {format_status_option(status): status for status in available_statuses}
        selected_statuses = st.multiselect(
            "Status filter",
            options=list(status_options.keys()),
            default=list(status_options.keys()),
            key="sevdesk_sparkasse_amazon_status_filter",
        )
        amazon_rows = [
            row
            for row in amazon_rows
            if str(row.get("status", "")) in {status_options[label] for label in selected_statuses}
        ]

    return llm_provider, extract_model, amazon_rows


def render_booking_selection_section(
    amazon_rows: list[dict[str, Any]],
    *,
    llm_provider: str,
    extract_model: str,
) -> list[dict[str, Any]]:
    st.markdown("**Select Booking**")
    selection_df = build_amazon_selection_dataframe(amazon_rows)
    edited_selection_df = st.data_editor(
        selection_df,
        width="stretch",
        hide_index=True,
        disabled=[
            "id",
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
            "orderNumber": st.column_config.TextColumn("Order Number"),
        },
        key="sevdesk_sparkasse_amazon_selection_table",
    )
    selected_booking_ids = set(
        edited_selection_df.loc[edited_selection_df["selected"], "id"].astype(str).tolist()
    )
    selected_booking_rows = [
        row for row in amazon_rows if str(row.get("id", "")) in selected_booking_ids
    ]

    if st.button("Identify PDFs for selected bookings", width="stretch"):
        _handle_identify_pdfs(
            selected_booking_rows,
            llm_provider=llm_provider,
            extract_model=extract_model,
        )

    return selected_booking_rows


def _handle_identify_pdfs(
    selected_booking_rows: list[dict[str, Any]],
    *,
    llm_provider: str,
    extract_model: str,
) -> None:
    clear_amazon_analysis_state()
    if len(selected_booking_rows) != 1:
        report_error("Select exactly one booking before identifying PDFs.")
        return

    selected_booking = selected_booking_rows[0]
    pdf_matches = build_selected_pdf_matches([selected_booking])
    st.session_state["sevdesk_sparkasse_amazon_pdf_matches"] = pdf_matches
    if not pdf_matches or pdf_matches[0]["pdfCount"] == 0:
        st.warning("No matching receipt PDF was found for the selected booking.")
        return

    api_key = resolve_llm_api_key(
        llm_provider,
        session_state=st.session_state,
        secrets=st.secrets,
        environ=os.environ,
    )
    if not api_key:
        st.warning(
            f"{llm_provider}-API Key nicht gefunden. "
            "PDF wurde identifiziert, LLM-Extraktion wurde uebersprungen."
        )
        return

    matched_pdf_paths = pdf_matches[0]["pdfPaths"]
    with st.spinner(f"Analysing {len(matched_pdf_paths)} PDF(s) with LLM..."):
        try:
            extraction_results: list[dict[str, Any]] = []
            for matched_pdf_path in matched_pdf_paths:
                logger.info(
                    "Amazon accounting extraction requested booking_id=%s pdf=%s provider=%s model=%s",
                    selected_booking.get("id"),
                    matched_pdf_path,
                    llm_provider,
                    extract_model,
                )
                extraction_result = extract_accounting_data_from_pdf(
                    pdf_path=matched_pdf_path,
                    provider=llm_provider,
                    model_name=extract_model,
                    api_key=api_key,
                )
                extraction_result["bookingId"] = str(selected_booking.get("id", ""))
                extraction_result["comparison"] = build_accounting_comparison_rows(
                    selected_booking,
                    extraction_result["extracted"],
                )
                extraction_results.append(extraction_result)

            aggregate_match = aggregate_booking_receipt_match(selected_booking, extraction_results)
            aggregate_result = {
                "bookingId": str(selected_booking.get("id", "")),
                "provider": llm_provider,
                "model": extract_model,
                "pdfExtractions": extraction_results,
                "comparison": build_aggregate_accounting_comparison_rows(
                    selected_booking,
                    extraction_results,
                ),
                "sumExtractedAmount": sum_extracted_pdf_amounts(extraction_results),
                "aggregateMatch": aggregate_match,
            }
            accounting_type_rows = st.session_state.get("sevdesk_accounting_types_rows")
            if accounting_type_rows is None:
                accounting_type_rows = load_stored_accounting_types()
            check_account_rows = st.session_state.get("sevdesk_check_accounts_rows")
            if check_account_rows is None:
                check_account_rows = load_stored_check_accounts()
            customer_rows = st.session_state.get(AMAZON_CUSTOMERS_SESSION_KEY)
            if customer_rows is None:
                customer_rows = refresh_live_amazon_customers(report_errors=True)
            voucher_entries = build_voucher_payload_entries(
                booking_row=selected_booking,
                extraction_results=extraction_results,
                accounting_type_rows=accounting_type_rows,
                check_account_rows=check_account_rows,
                customer_rows=customer_rows or [],
            )
            if voucher_entries:
                st.session_state["sevdesk_sparkasse_amazon_voucher_payload"] = {
                    "bookingId": str(selected_booking.get("id", "")),
                    "aggregateMatch": aggregate_match,
                    "entries": voucher_entries,
                }
            else:
                st.session_state.pop("sevdesk_sparkasse_amazon_voucher_payload", None)
            st.session_state["sevdesk_sparkasse_amazon_llm_result"] = aggregate_result
        except Exception as exc:
            report_error(
                f"LLM extraction failed: {exc}",
                log_message=(
                    "Amazon accounting extraction failed "
                    f"booking_id={selected_booking.get('id')}"
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
        st.success("Match: Die Summe der extrahierten PDF-Betraege stimmt mit der Buchung ueberein.")
    elif aggregate_match is False:
        st.warning("Die Summe der extrahierten PDF-Betraege stimmt nicht mit der Buchung ueberein.")
    st.dataframe(pd.DataFrame(llm_result.get("comparison", [])), width="stretch")
    st.markdown("**Extracted Accounting Data**")
    st.caption(
        "LLM results for all matched PDFs of the currently selected booking. "
        f"Model: `{llm_result.get('provider')}` / `{llm_result.get('model')}`"
    )
    for index, extraction_entry in enumerate(pdf_extractions, start=1):
        extracted = extraction_entry.get("extracted", {})
        pdf_path = str(extraction_entry.get("pdfPath", "")).strip()
        pdf_label = Path(pdf_path).name if pdf_path else f"PDF {index}"
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
        with st.expander(f"Raw extraction payload #{index}"):
            st.json(extracted)


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
        st.success("A voucher JSON was generated for each matched PDF.")
    elif aggregate_match_for_create is False:
        st.warning(
            "Voucher JSON files were generated for each matched PDF, "
            "but API creation is disabled until the summed PDF amount matches the booking."
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
    validation_errors = voucher_entry.get("validationErrors", [])
    seller_name = str(voucher_entry.get("sellerName", "")).strip()
    seller_vat_id = str(voucher_entry.get("sellerVatId", "")).strip()
    is_intra_community_supply = voucher_entry.get("isIntraCommunitySupply") is True
    matched_customer = voucher_entry.get("customer")
    created_customer = voucher_entry.get("createdCustomer")
    created_voucher = voucher_entry.get("createdVoucher")
    create_customer_response = voucher_entry.get("createCustomerResponse")
    create_response = voucher_entry.get("createResponse")

    with st.container(border=True):
        st.markdown(f"**Voucher JSON {entry_index} | {pdf_label}**")
        st.caption(f"Saved to `{voucher_entry.get('path')}`")
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

        with st.expander("Voucher payload", expanded=False):
            st.json(voucher_entry.get("payload", {}))

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
                entry_index=entry_index,
            )

        if create_customer_response:
            with st.expander(f"Create customer API response #{entry_index}"):
                st.json(create_customer_response)

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
                entry_index=entry_index,
            )

        if create_response:
            with st.expander(f"Create voucher API response #{entry_index}"):
                st.json(create_response)


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
    entry_index: int,
) -> None:
    if not st.button(
        "Create customer in sevDesk and update voucher JSON",
        width="stretch",
        key=f"sevdesk_create_customer_{voucher_payload_state.get('bookingId', '')}_{entry_index}",
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

            st.session_state["sevdesk_sparkasse_amazon_voucher_payload"] = {
                **voucher_payload_state,
                "entries": updated_entries,
            }
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
    entry_index: int,
) -> None:
    if not st.button(
        "Create voucher via API for this PDF",
        width="stretch",
        disabled=bool(validation_errors) or aggregate_match_for_create is not True,
        key=f"sevdesk_create_voucher_{voucher_payload_state.get('bookingId', '')}_{entry_index}",
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
            st.session_state["sevdesk_sparkasse_amazon_voucher_payload"] = {
                **voucher_payload_state,
                "entries": updated_entries,
            }
            if created_voucher_id:
                st.session_state["sevdesk_latest_belege_rows"] = request_vouchers(
                    base_url(),
                    token,
                    10,
                )
            st.success(
                "Voucher created successfully in sevDesk."
                + (f" New id: `{created_voucher_id}`." if created_voucher_id else "")
            )
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
