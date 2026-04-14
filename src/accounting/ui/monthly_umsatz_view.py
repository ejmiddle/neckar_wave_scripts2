from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from src.accounting.common import base_url, ensure_token
from src.accounting.master_data import load_stored_accounting_types
from src.accounting.monthly_umsatz import (
    MonthlyUmsatzFormatError,
    build_monthly_umsatz_voucher_payloads,
    extract_monthly_umsatz_json,
    previous_month_end,
)
from src.sevdesk.api import create_voucher, request_voucher_by_id
from src.sevdesk.voucher import (
    first_object_from_response,
    normalize_create_payload,
    validate_create_payload,
)

MONTHLY_UMSATZ_EXTRACTED_STATE_KEY = "monthly_umsatz_extracted_payload"
MONTHLY_UMSATZ_VOUCHERS_STATE_KEY = "monthly_umsatz_voucher_payloads"
MONTHLY_UMSATZ_FILE_SIGNATURE_STATE_KEY = "monthly_umsatz_file_signature"
MONTHLY_UMSATZ_BELEGDATUM_STATE_KEY = "monthly_umsatz_belegdatum"
MONTHLY_UMSATZ_UPLOAD_RESULTS_STATE_KEY = "monthly_umsatz_upload_results"
MONTHLY_UMSATZ_INPUT_DATE_KEY = "monthly_umsatz_belegdatum_input"
MONTHLY_VOUCHER_KIND_ORDER = ("umsatz", "voucher_verkauft", "voucher_eingeloest")
MONTHLY_VOUCHER_KIND_LABELS = {
    "umsatz": "Umsatz",
    "voucher_verkauft": "Voucher verkauft",
    "voucher_eingeloest": "Voucher eingelöst",
}


def _clear_state_keys(*keys: str) -> None:
    for key in keys:
        st.session_state.pop(key, None)


def _date_signature(value: date) -> str:
    return value.isoformat()


def _single_uploaded_file_signature(uploaded_file: Any | None) -> tuple[str, int] | None:
    if uploaded_file is None:
        return None
    return (str(uploaded_file.name), int(uploaded_file.size))


def _load_accounting_type_rows() -> list[dict[str, Any]]:
    accounting_type_rows = st.session_state.get("sevdesk_accounting_types_rows")
    if isinstance(accounting_type_rows, list):
        return accounting_type_rows
    return load_stored_accounting_types()


def _known_accounting_type_ids(accounting_type_rows: list[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("id", ""))
        for row in accounting_type_rows
        if isinstance(row, dict) and str(row.get("id", "")).strip()
    }


def _is_nested_monthly_payloads(payloads: dict[str, Any]) -> bool:
    sample_sheet = payloads.get("ALT")
    return isinstance(sample_sheet, dict) and any(
        isinstance(sample_sheet.get(kind), dict) for kind in MONTHLY_VOUCHER_KIND_ORDER
    )


def _voucher_results_for_sheet(
    upload_results: dict[str, Any],
    sheet_name: str,
    voucher_kind: str,
) -> dict[str, Any]:
    sheet_results = upload_results.get(sheet_name, {})
    if not isinstance(sheet_results, dict):
        return {}
    result = sheet_results.get(voucher_kind, {})
    if isinstance(result, dict):
        return result
    return {}


def _sync_single_upload_state(uploaded_file: Any | None) -> None:
    current_signature = _single_uploaded_file_signature(uploaded_file)
    previous_signature = st.session_state.get(MONTHLY_UMSATZ_FILE_SIGNATURE_STATE_KEY)
    if current_signature == previous_signature:
        return
    st.session_state[MONTHLY_UMSATZ_FILE_SIGNATURE_STATE_KEY] = current_signature
    _clear_state_keys(
        MONTHLY_UMSATZ_EXTRACTED_STATE_KEY,
        MONTHLY_UMSATZ_VOUCHERS_STATE_KEY,
        MONTHLY_UMSATZ_UPLOAD_RESULTS_STATE_KEY,
        MONTHLY_UMSATZ_BELEGDATUM_STATE_KEY,
    )


def _prepare_monthly_umsatz_payloads(
    *,
    belegdatum: date,
    accounting_type_rows: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, object]]] | None:
    extracted_payload = st.session_state.get(MONTHLY_UMSATZ_EXTRACTED_STATE_KEY)
    if not isinstance(extracted_payload, dict):
        return None

    current_date_signature = _date_signature(belegdatum)
    payloads = st.session_state.get(MONTHLY_UMSATZ_VOUCHERS_STATE_KEY)
    stored_date_signature = st.session_state.get(MONTHLY_UMSATZ_BELEGDATUM_STATE_KEY)
    if (
        isinstance(payloads, dict)
        and stored_date_signature == current_date_signature
        and _is_nested_monthly_payloads(payloads)
    ):
        return payloads

    payloads = build_monthly_umsatz_voucher_payloads(
        extracted_payload,
        belegdatum,
        accounting_type_rows=accounting_type_rows,
    )
    st.session_state[MONTHLY_UMSATZ_VOUCHERS_STATE_KEY] = payloads
    st.session_state[MONTHLY_UMSATZ_BELEGDATUM_STATE_KEY] = current_date_signature
    st.session_state[MONTHLY_UMSATZ_UPLOAD_RESULTS_STATE_KEY] = {}
    return payloads


def render_monthly_umsatz_view() -> None:
    st.title("📈 Accounting / Monthly Umsatz")
    st.caption(
        "Upload a monthly Excel export, derive sevDesk voucher payloads from `ALT` and `WIE`, and upload them."
    )

    accounting_type_rows = _load_accounting_type_rows()
    default_belegdatum = previous_month_end(date.today())
    st.subheader("1. Input")
    belegdatum = st.date_input(
        "Belegdatum",
        value=st.session_state.get(MONTHLY_UMSATZ_INPUT_DATE_KEY, default_belegdatum),
        help="Defaults to the last day of the previous month. Updating it rebuilds the prepared vouchers.",
        format="DD.MM.YYYY",
        key=MONTHLY_UMSATZ_INPUT_DATE_KEY,
    )
    uploaded_file = st.file_uploader(
        "Upload monthly Umsatz Excel",
        type=["xlsx", "xls"],
        help="The workbook must contain `ALT` and `WIE` in the same validated layout.",
    )
    _sync_single_upload_state(uploaded_file)

    if uploaded_file is None:
        st.info("Upload an Excel file to extract the monthly Umsatz JSON.")
        return

    file_col1, file_col2, file_col3 = st.columns(3)
    with file_col1:
        st.metric("File Name", uploaded_file.name)
    with file_col2:
        st.metric("File Size", f"{uploaded_file.size / 1024:.1f} KB")
    with file_col3:
        st.metric("Prepared Sheets", "ALT + WIE")

    if st.button("Process Monthly Umsatz File", type="primary", width="stretch"):
        try:
            with st.spinner("Processing Excel file..."):
                extracted_payload = extract_monthly_umsatz_json(uploaded_file.getvalue())
                voucher_payloads = build_monthly_umsatz_voucher_payloads(
                    extracted_payload,
                    belegdatum,
                    accounting_type_rows=accounting_type_rows,
                )
            st.session_state[MONTHLY_UMSATZ_EXTRACTED_STATE_KEY] = extracted_payload
            st.session_state[MONTHLY_UMSATZ_VOUCHERS_STATE_KEY] = voucher_payloads
            st.session_state[MONTHLY_UMSATZ_BELEGDATUM_STATE_KEY] = _date_signature(belegdatum)
            st.session_state[MONTHLY_UMSATZ_UPLOAD_RESULTS_STATE_KEY] = {}
            st.success("Monthly Umsatz file processed successfully.")
        except MonthlyUmsatzFormatError as exc:
            st.error(f"Invalid monthly Umsatz workbook format: {exc}")
        except Exception as exc:
            st.error(f"Could not process the uploaded Excel file: {exc}")

    extracted_payload = st.session_state.get(MONTHLY_UMSATZ_EXTRACTED_STATE_KEY)
    voucher_payloads = _prepare_monthly_umsatz_payloads(
        belegdatum=belegdatum,
        accounting_type_rows=accounting_type_rows,
    )
    if not isinstance(extracted_payload, dict) or not isinstance(voucher_payloads, dict):
        return

    upload_results = st.session_state.get(MONTHLY_UMSATZ_UPLOAD_RESULTS_STATE_KEY, {})
    if not isinstance(upload_results, dict):
        upload_results = {}

    st.subheader("2. Review")
    with st.popover("Show extracted Umsatz JSON"):
        st.json(extracted_payload, expanded=True)

    known_accounting_type_ids = _known_accounting_type_ids(accounting_type_rows)
    summary_rows = []
    for sheet_name in ("ALT", "WIE"):
        sheet_payloads = voucher_payloads.get(sheet_name)
        if not isinstance(sheet_payloads, dict):
            continue
        for voucher_kind in MONTHLY_VOUCHER_KIND_ORDER:
            voucher_payload = sheet_payloads.get(voucher_kind)
            if not isinstance(voucher_payload, dict):
                continue
            voucher_core = voucher_payload.get("voucher", {})
            validation_errors = validate_create_payload(
                voucher_payload,
                known_accounting_type_ids=known_accounting_type_ids,
            )
            upload_result = _voucher_results_for_sheet(upload_results, sheet_name, voucher_kind)
            summary_rows.append(
                {
                    "Sheet": sheet_name,
                    "Voucher": MONTHLY_VOUCHER_KIND_LABELS[voucher_kind],
                    "Description": voucher_core.get("description") if isinstance(voucher_core, dict) else "-",
                    "Amount": voucher_core.get("sumGross") if isinstance(voucher_core, dict) else "-",
                    "Status": (
                        "Upload error"
                        if upload_result.get("upload_error")
                        else "Uploaded"
                        if upload_result.get("createdVoucher")
                        else "Validation error"
                        if validation_errors
                        else "Prepared"
                    ),
                }
            )
    if summary_rows:
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)

    for sheet_name in ("ALT", "WIE"):
        sheet_payloads = voucher_payloads.get(sheet_name)
        if not isinstance(sheet_payloads, dict):
            continue

        with st.expander(f"{sheet_name} vouchers", expanded=False):
            for index, voucher_kind in enumerate(MONTHLY_VOUCHER_KIND_ORDER):
                voucher_payload = sheet_payloads.get(voucher_kind)
                if not isinstance(voucher_payload, dict):
                    continue

                voucher_label = MONTHLY_VOUCHER_KIND_LABELS[voucher_kind]
                upload_result = _voucher_results_for_sheet(upload_results, sheet_name, voucher_kind)
                validation_errors = validate_create_payload(
                    voucher_payload,
                    known_accounting_type_ids=known_accounting_type_ids,
                )
                voucher_positions = voucher_payload.get("voucherPosSave", [])
                voucher_core = voucher_payload.get("voucher", {})

                if index > 0:
                    st.divider()
                st.markdown(f"### {voucher_label}")

                if upload_result.get("upload_error"):
                    st.error(str(upload_result["upload_error"]))
                if isinstance(upload_result.get("createdVoucher"), dict):
                    created_voucher = upload_result["createdVoucher"]
                    created_voucher_id = str(created_voucher.get("id", "")).strip()
                    st.success(
                        f"{voucher_label} uploaded successfully."
                        + (f" New id: `{created_voucher_id}`." if created_voucher_id else "")
                    )
                    st.json(created_voucher, expanded=False)

                if validation_errors:
                    st.error("Payload validation failed:")
                    for error in validation_errors:
                        st.write(f"- {error}")
                else:
                    st.success("Payload validation passed.")

                if isinstance(voucher_core, dict):
                    st.markdown("**Essential Fields**")
                    st.json(
                        {
                            "description": voucher_core.get("description"),
                            "sumGross": voucher_core.get("sumGross"),
                            "voucherDate": voucher_core.get("voucherDate"),
                            "creditDebit": voucher_core.get("creditDebit"),
                        },
                        expanded=False,
                    )
                    if isinstance(voucher_positions, list) and voucher_positions:
                        split_rows = []
                        for pos in voucher_positions:
                            if not isinstance(pos, dict):
                                continue
                            split_rows.append(
                                {
                                    "taxRate": pos.get("taxRate"),
                                    "sumGross": pos.get("sumGross"),
                                    "sumNet": pos.get("sumNet"),
                                    "comment": pos.get("comment"),
                                }
                            )

                        if split_rows:
                            st.markdown("**Tax Split**")
                            st.table(split_rows)

                st.markdown("**Prepared Voucher JSON**")
                st.json(voucher_payload, expanded=False)

                if st.button(
                    f"Upload {voucher_label} for {sheet_name} to sevDesk",
                    key=f"monthly_umsatz_upload_{sheet_name}_{voucher_kind}",
                    width="stretch",
                    disabled=bool(validation_errors),
                ):
                    token = ensure_token()
                    if token:
                        try:
                            with st.spinner(f"Uploading {voucher_label} for {sheet_name} to sevDesk..."):
                                request_payload = normalize_create_payload(voucher_payload)
                                response_payload = create_voucher(base_url(), token, request_payload)
                                created_summary = first_object_from_response(response_payload) or {}
                                created_voucher_id = str(created_summary.get("id", "")).strip()
                                created_voucher = (
                                    request_voucher_by_id(base_url(), token, created_voucher_id)
                                    if created_voucher_id
                                    else None
                                )
                            sheet_results = upload_results.get(sheet_name, {})
                            if not isinstance(sheet_results, dict):
                                sheet_results = {}
                            st.session_state[MONTHLY_UMSATZ_UPLOAD_RESULTS_STATE_KEY] = {
                                **upload_results,
                                sheet_name: {
                                    **sheet_results,
                                    voucher_kind: {"createdVoucher": created_voucher or created_summary},
                                },
                            }
                            st.rerun()
                        except Exception as exc:
                            sheet_results = upload_results.get(sheet_name, {})
                            if not isinstance(sheet_results, dict):
                                sheet_results = {}
                            st.session_state[MONTHLY_UMSATZ_UPLOAD_RESULTS_STATE_KEY] = {
                                **upload_results,
                                sheet_name: {
                                    **sheet_results,
                                    voucher_kind: {"upload_error": str(exc)},
                                },
                            }
                            st.rerun()
