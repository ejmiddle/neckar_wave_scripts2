from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.accounting.common import base_url, ensure_token
from src.accounting.lohn_belege_extraction import (
    extract_lohnkosten_from_pdf,
    extract_u1_pages_from_pdf,
)
from src.accounting.master_data import load_stored_accounting_types
from src.accounting.monthly_umsatz import previous_month_end
from src.accounting.u1_vouchers import (
    build_lohnkosten_voucher_payloads,
    build_u1_voucher_payloads,
)
from src.lieferscheine_llm import resolve_openai_api_key
from src.sevdesk.api import (
    create_voucher,
    request_voucher_by_id,
    upload_voucher_temp_file,
)
from src.sevdesk.voucher import (
    first_object_from_response,
    normalize_create_payload,
    validate_create_payload,
)

LOHN_BELEGE_UPLOAD_TYPE_KEY = "lohn_belege_upload_type"
LOHN_BELEGE_UPLOAD_TYPES = ("U1", "Lohnkosten")
LOHN_BELEGE_DATE_KEY = "lohn_belege_belegdatum"
LOHN_BELEGE_LOHNKOSTEN_RESULTS_KEY = "lohn_belege_lohnkosten_results"
LOHN_BELEGE_U1_RESULTS_KEY = "lohn_belege_u1_results"
LOHN_BELEGE_U1_VOUCHER_PAYLOADS_KEY = "lohn_belege_u1_voucher_payloads"
LOHN_BELEGE_LOHNKOSTEN_VOUCHER_PAYLOADS_KEY = "lohn_belege_lohnkosten_voucher_payloads"
LOHN_BELEGE_U1_FILE_SIGNATURE_STATE_KEY = "lohn_belege_u1_file_signature"
LOHN_BELEGE_LOHNKOSTEN_FILE_SIGNATURE_STATE_KEY = "lohn_belege_lohnkosten_file_signature"
LOHN_BELEGE_U1_BELEGDATUM_STATE_KEY = "lohn_belege_u1_belegdatum"
LOHN_BELEGE_LOHNKOSTEN_BELEGDATUM_STATE_KEY = "lohn_belege_lohnkosten_belegdatum"
LOHN_BELEGE_MODEL_NAME = "gpt-4o"


def _clear_state_keys(*keys: str) -> None:
    for key in keys:
        st.session_state.pop(key, None)


def _date_signature(value: date) -> str:
    return value.isoformat()


def _uploaded_files_signature(uploaded_files: list[Any] | None) -> tuple[tuple[str, int], ...] | None:
    if not uploaded_files:
        return None
    return tuple((str(uploaded_file.name), int(uploaded_file.size)) for uploaded_file in uploaded_files)


def _render_uploaded_files_table(uploaded_files: list[Any]) -> None:
    file_rows = [
        {
            "File Name": uploaded_file.name,
            "File Size": f"{uploaded_file.size / 1024:.1f} KB",
        }
        for uploaded_file in uploaded_files
    ]
    st.dataframe(pd.DataFrame(file_rows), width="stretch", hide_index=True)


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


def _resolve_lohn_belege_api_key() -> str | None:
    return resolve_openai_api_key(
        session_state=st.session_state,
        secrets=st.secrets,
    )


def _sync_multi_upload_state(
    *,
    uploaded_files: list[Any] | None,
    signature_key: str,
    reset_keys: tuple[str, ...],
) -> None:
    current_signature = _uploaded_files_signature(uploaded_files)
    previous_signature = st.session_state.get(signature_key)
    if current_signature == previous_signature:
        return
    st.session_state[signature_key] = current_signature
    _clear_state_keys(*reset_keys)


def _prepare_lohn_belege_payloads(
    *,
    results_key: str,
    payloads_key: str,
    belegdatum_key: str,
    belegdatum: date,
    builder: Any,
) -> list[dict[str, Any]] | None:
    results = st.session_state.get(results_key)
    if not isinstance(results, list):
        return None

    current_date_signature = _date_signature(belegdatum)
    payloads = st.session_state.get(payloads_key)
    stored_date_signature = st.session_state.get(belegdatum_key)
    if isinstance(payloads, list) and stored_date_signature == current_date_signature:
        return payloads

    payloads = builder(
        results,
        belegdatum,
        accounting_type_rows=_load_accounting_type_rows(),
    )
    st.session_state[payloads_key] = payloads
    st.session_state[belegdatum_key] = current_date_signature
    return payloads


def _build_u1_voucher_payloads(
    results: list[dict[str, Any]],
    belegdatum: date,
) -> list[dict[str, Any]]:
    payloads = build_u1_voucher_payloads(
        results,
        belegdatum,
        accounting_type_rows=_load_accounting_type_rows(),
    )
    st.session_state[LOHN_BELEGE_U1_VOUCHER_PAYLOADS_KEY] = payloads
    st.session_state[LOHN_BELEGE_U1_BELEGDATUM_STATE_KEY] = _date_signature(belegdatum)
    return payloads


def _build_lohnkosten_voucher_payloads(
    results: list[dict[str, Any]],
    belegdatum: date,
) -> list[dict[str, Any]]:
    payloads = build_lohnkosten_voucher_payloads(
        results,
        belegdatum,
        accounting_type_rows=_load_accounting_type_rows(),
    )
    st.session_state[LOHN_BELEGE_LOHNKOSTEN_VOUCHER_PAYLOADS_KEY] = payloads
    st.session_state[LOHN_BELEGE_LOHNKOSTEN_BELEGDATUM_STATE_KEY] = _date_signature(belegdatum)
    return payloads


def _upload_voucher_payloads(
    payloads: list[dict[str, Any]],
    *,
    section_name: str,
) -> list[dict[str, Any]]:
    token = ensure_token()
    if not token:
        return payloads

    uploaded_payloads: list[dict[str, Any]] = []
    known_accounting_type_ids = _known_accounting_type_ids(_load_accounting_type_rows())
    with st.spinner(f"Uploading {section_name} vouchers to sevDesk..."):
        for payload_entry in payloads:
            payload = payload_entry.get("voucher_payload") if isinstance(payload_entry, dict) else None
            if not isinstance(payload, dict):
                uploaded_payloads.append(payload_entry)
                continue

            try:
                request_payload = normalize_create_payload(payload)
                validation_errors = validate_create_payload(
                    request_payload,
                    known_accounting_type_ids=known_accounting_type_ids,
                )
                if validation_errors:
                    uploaded_payloads.append(
                        {
                            **payload_entry,
                            "upload_error": "Voucher payload validation failed: "
                            + "; ".join(validation_errors),
                        }
                    )
                    continue
                attachment_bytes = payload_entry.get("page_pdf_bytes") if isinstance(payload_entry, dict) else None
                attachment_name = (
                    str(payload_entry.get("page_pdf_name", "")).strip()
                    if isinstance(payload_entry, dict)
                    else ""
                )
                if isinstance(attachment_bytes, bytes) and attachment_bytes:
                    voucher = request_payload.get("voucher")
                    if isinstance(voucher, dict):
                        voucher["document"] = None
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_name = attachment_name or "u1_page.pdf"
                        temp_path = Path(temp_dir) / temp_name
                        temp_path.write_bytes(attachment_bytes)
                        remote_filename = upload_voucher_temp_file(
                            base_url(),
                            token,
                            temp_path,
                        )
                        request_payload["filename"] = remote_filename
                response_payload = create_voucher(base_url(), token, request_payload)
                created_summary = first_object_from_response(response_payload) or {}
                created_voucher_id = str(created_summary.get("id", "")).strip()
                created_voucher = (
                    request_voucher_by_id(base_url(), token, created_voucher_id)
                    if created_voucher_id
                    else None
                )
                uploaded_payloads.append(
                    {
                        **payload_entry,
                        "createResponse": response_payload,
                        "createdVoucher": created_voucher or created_summary,
                    }
                )
            except Exception as exc:
                uploaded_payloads.append(
                    {
                        **payload_entry,
                        "upload_error": str(exc),
                    }
                )

    return uploaded_payloads


def _render_prepared_voucher_payloads(
    payloads: list[dict[str, Any]],
    *,
    section_name: str,
    upload_button_label: str,
    state_key: str,
) -> None:
    if not payloads:
        st.info(f"No prepared {section_name} voucher payloads yet.")
        return

    known_accounting_type_ids = _known_accounting_type_ids(_load_accounting_type_rows())

    if st.button(upload_button_label, type="primary", width="stretch", key=f"{state_key}_upload_all"):
        updated_payloads = _upload_voucher_payloads(payloads, section_name=section_name)
        st.session_state[state_key] = updated_payloads
        payloads = updated_payloads
        st.success(f"{section_name} vouchers uploaded successfully.")

    summary_rows = []
    for item in payloads:
        payload = item.get("voucher_payload") if isinstance(item, dict) else {}
        voucher = payload.get("voucher", {}) if isinstance(payload, dict) else {}
        label = str(item.get("kind") or f"Page {item.get('page_number', '-')}")
        summary_rows.append(
            {
                "File": item.get("file_name", "-"),
                "Label": label,
                "Description": item.get("description") or voucher.get("description") or "-",
                "Amount": item.get("amount") or voucher.get("sumGross") or "-",
                "Attachment": item.get("page_pdf_name", "-") if item.get("page_pdf_bytes") else "-",
                "Status": (
                    "Upload error"
                    if item.get("upload_error")
                    else "Error"
                    if item.get("error")
                    else "Uploaded"
                    if item.get("createdVoucher")
                    else "Prepared"
                ),
            }
        )

    st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)

    for item in payloads:
        payload = item.get("voucher_payload") if isinstance(item, dict) else None
        label = str(item.get("kind") or f"Page {item.get('page_number', '-')}")
        with st.expander(
            f"{item.get('file_name', '-')} | {label}",
            expanded=False,
        ):
            if item.get("error"):
                st.error(str(item["error"]))
                continue
            if item.get("upload_error"):
                st.error(str(item["upload_error"]))
            if isinstance(item.get("createdVoucher"), dict):
                st.success("Voucher uploaded.")
                st.json(item["createdVoucher"], expanded=False)
            if not isinstance(payload, dict):
                st.info("No voucher payload prepared for this item yet.")
                continue

            validation_errors = validate_create_payload(
                payload,
                known_accounting_type_ids=known_accounting_type_ids,
            )
            if validation_errors:
                st.error("Voucher payload validation failed:")
                for error in validation_errors:
                    st.write(f"- {error}")
            else:
                st.success("Voucher payload validation passed.")

            st.markdown("**Prepared Voucher JSON**")
            st.json(payload, expanded=False)


def _process_lohnkosten_uploads(uploaded_files: list[Any]) -> list[dict[str, Any]] | None:
    api_key = _resolve_lohn_belege_api_key()
    if not api_key:
        st.warning(
            "OpenAI API key not found. Add `OPENAI_API_KEY` or `openai_api_key` to process the PDFs."
        )
        return None

    results: list[dict[str, Any]] = []
    with st.spinner("Processing Lohnkosten PDFs..."):
        for uploaded_file in uploaded_files:
            try:
                results.append(
                    extract_lohnkosten_from_pdf(
                        pdf_bytes=uploaded_file.getvalue(),
                        pdf_name=uploaded_file.name,
                        api_key=api_key,
                        model_name=LOHN_BELEGE_MODEL_NAME,
                    )
                )
            except Exception as exc:
                results.append(
                    {
                        "source_type": "Lohnkosten",
                        "file_name": uploaded_file.name,
                        "error": str(exc),
                    }
                )

    st.session_state[LOHN_BELEGE_LOHNKOSTEN_RESULTS_KEY] = results
    return results


def _process_u1_uploads(uploaded_files: list[Any]) -> list[dict[str, Any]] | None:
    api_key = _resolve_lohn_belege_api_key()
    if not api_key:
        st.warning(
            "OpenAI API key not found. Add `OPENAI_API_KEY` or `openai_api_key` to process the PDFs."
        )
        return None

    results: list[dict[str, Any]] = []
    with st.spinner("Processing U1 PDFs page by page..."):
        for uploaded_file in uploaded_files:
            try:
                results.append(
                    extract_u1_pages_from_pdf(
                        pdf_bytes=uploaded_file.getvalue(),
                        pdf_name=uploaded_file.name,
                        api_key=api_key,
                        model_name=LOHN_BELEGE_MODEL_NAME,
                    )
                )
            except Exception as exc:
                results.append(
                    {
                        "source_type": "U1",
                        "file_name": uploaded_file.name,
                        "error": str(exc),
                        "pages": [],
                    }
                )

    st.session_state[LOHN_BELEGE_U1_RESULTS_KEY] = results
    return results


def _render_lohnkosten_section() -> None:
    st.subheader("Lohnkosten")
    st.caption("Upload one or more PDF files. Each PDF is processed as a full document.")

    st.markdown("**1. Input**")
    uploaded_files = st.file_uploader(
        "Upload Lohnkosten PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        key="lohn_belege_uploader_lohnkosten",
        help="PDF only. The output is a single JSON per PDF with the three summary values.",
    )
    _sync_multi_upload_state(
        uploaded_files=uploaded_files,
        signature_key=LOHN_BELEGE_LOHNKOSTEN_FILE_SIGNATURE_STATE_KEY,
        reset_keys=(
            LOHN_BELEGE_LOHNKOSTEN_RESULTS_KEY,
            LOHN_BELEGE_LOHNKOSTEN_VOUCHER_PAYLOADS_KEY,
            LOHN_BELEGE_LOHNKOSTEN_BELEGDATUM_STATE_KEY,
        ),
    )

    if uploaded_files:
        _render_uploaded_files_table(uploaded_files)

    process_clicked = st.button(
        "Process Lohnkosten PDFs",
        type="primary",
        width="stretch",
        disabled=not uploaded_files,
        key="lohn_belege_process_lohnkosten",
    )
    if process_clicked and uploaded_files:
        results = _process_lohnkosten_uploads(uploaded_files)
        if results is not None:
            _build_lohnkosten_voucher_payloads(results, st.session_state[LOHN_BELEGE_DATE_KEY])
            st.success("Lohnkosten PDFs processed successfully.")

    st.markdown("**2. Review & Upload**")
    payloads = _prepare_lohn_belege_payloads(
        results_key=LOHN_BELEGE_LOHNKOSTEN_RESULTS_KEY,
        payloads_key=LOHN_BELEGE_LOHNKOSTEN_VOUCHER_PAYLOADS_KEY,
        belegdatum_key=LOHN_BELEGE_LOHNKOSTEN_BELEGDATUM_STATE_KEY,
        belegdatum=st.session_state[LOHN_BELEGE_DATE_KEY],
        builder=build_lohnkosten_voucher_payloads,
    )
    if isinstance(payloads, list):
        _render_prepared_voucher_payloads(
            payloads,
            section_name="Lohnkosten",
            upload_button_label="Upload Lohnkosten vouchers to sevDesk",
            state_key=LOHN_BELEGE_LOHNKOSTEN_VOUCHER_PAYLOADS_KEY,
        )
    else:
        st.info("Upload PDFs and process them to prepare the Lohnkosten voucher JSON output.")


def _render_u1_section() -> None:
    st.subheader("U1")
    st.caption("Upload one or more PDF files. Each page is processed sequentially.")

    st.markdown("**1. Input**")
    uploaded_files = st.file_uploader(
        "Upload U1 PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        key="lohn_belege_uploader_u1",
        help="PDF only. Each page will produce its own JSON result with the Erstattungsbeitrag.",
    )
    _sync_multi_upload_state(
        uploaded_files=uploaded_files,
        signature_key=LOHN_BELEGE_U1_FILE_SIGNATURE_STATE_KEY,
        reset_keys=(
            LOHN_BELEGE_U1_RESULTS_KEY,
            LOHN_BELEGE_U1_VOUCHER_PAYLOADS_KEY,
            LOHN_BELEGE_U1_BELEGDATUM_STATE_KEY,
        ),
    )

    if uploaded_files:
        _render_uploaded_files_table(uploaded_files)

    process_clicked = st.button(
        "Process U1 PDFs",
        type="primary",
        width="stretch",
        disabled=not uploaded_files,
        key="lohn_belege_process_u1",
    )
    if process_clicked and uploaded_files:
        results = _process_u1_uploads(uploaded_files)
        if results is not None:
            _build_u1_voucher_payloads(results, st.session_state[LOHN_BELEGE_DATE_KEY])
            st.success("U1 PDFs processed successfully.")

    st.markdown("**2. Review & Upload**")
    payloads = _prepare_lohn_belege_payloads(
        results_key=LOHN_BELEGE_U1_RESULTS_KEY,
        payloads_key=LOHN_BELEGE_U1_VOUCHER_PAYLOADS_KEY,
        belegdatum_key=LOHN_BELEGE_U1_BELEGDATUM_STATE_KEY,
        belegdatum=st.session_state[LOHN_BELEGE_DATE_KEY],
        builder=build_u1_voucher_payloads,
    )
    if isinstance(payloads, list):
        _render_prepared_voucher_payloads(
            payloads,
            section_name="U1",
            upload_button_label="Upload U1 vouchers to sevDesk",
            state_key=LOHN_BELEGE_U1_VOUCHER_PAYLOADS_KEY,
        )
    else:
        st.info("Upload PDFs and process them to prepare per-page U1 voucher JSON output.")


def render_lohn_belege_view() -> None:
    st.title("🧾 Accounting / Lohn Belege")
    st.caption("Upload payroll-related PDFs and process them by type.")

    st.subheader("Shared Settings")
    st.date_input(
        "Belegdatum",
        value=st.session_state.get(LOHN_BELEGE_DATE_KEY, previous_month_end(date.today())),
        help="Defaults to the last day of the previous month. Used for both U1 and Lohnkosten vouchers.",
        format="DD.MM.YYYY",
        key=LOHN_BELEGE_DATE_KEY,
    )

    upload_type = st.radio(
        "Select upload type",
        options=LOHN_BELEGE_UPLOAD_TYPES,
        horizontal=True,
        key=LOHN_BELEGE_UPLOAD_TYPE_KEY,
    )

    if upload_type == "U1":
        st.info(
            "U1 PDFs are processed page by page. Changing the Belegdatum rebuilds the prepared vouchers without reprocessing the files."
        )
        _render_u1_section()
    else:
        st.info(
            "Lohnkosten PDFs are processed as full documents. Changing the Belegdatum rebuilds the prepared vouchers without reprocessing the files."
        )
        _render_lohnkosten_section()
