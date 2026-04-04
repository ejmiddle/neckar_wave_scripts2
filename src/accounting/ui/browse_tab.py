import io
import zipfile
from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from src.accounting.common import base_url, ensure_token, report_error, safe_filename_token
from src.accounting.master_data import load_stored_check_accounts
from src.accounting.sevdesk_browse import extract_voucher_tag_names
from src.accounting.ui.displays import show_selectable_vouchers, show_transactions
from src.logging_config import logger
from src.sevdesk.api import (
    download_voucher_document,
    fetch_latest_transactions_for_check_account,
    request_contacts,
    request_vouchers_with_tags,
    request_vouchers_with_tags_for_contacts,
)
from src.sevdesk.booking import book_voucher_to_check_account

LATEST_BELEGE_ROWS_KEY = "sevdesk_latest_belege_rows"
LATEST_BELEGE_LIMIT_KEY = "sevdesk_latest_belege_limit"
LATEST_BELEGE_STATUS_FILTER_KEY = "sevdesk_latest_belege_status_filter"
LATEST_BELEGE_STATUS_FILTER_OPTIONS_KEY = "sevdesk_latest_belege_status_filter_options"
LATEST_BELEGE_TAG_FILTER_KEY = "sevdesk_latest_belege_tag_filter"
LATEST_BELEGE_TAG_FILTER_OPTIONS_KEY = "sevdesk_latest_belege_tag_filter_options"
LATEST_BELEGE_SELECTION_TABLE_KEY = "sevdesk_latest_belege_selection_table"
LATEST_BELEGE_SELECTED_IDS_KEY = "sevdesk_latest_belege_selected_ids"
LATEST_BELEGE_UMBUCHEN_CHECK_ACCOUNT_KEY = "sevdesk_latest_belege_umbuchen_check_account"
LATEST_BELEGE_UMBUCHEN_RESULTS_KEY = "sevdesk_latest_belege_umbuchen_results"
EMPTY_STATUS_FILTER_LABEL = "(No status)"
NO_TAGS_FILTER_LABEL = "(No tags)"
LATEST_BELEGE_DOWNLOAD_PAYLOAD_KEY = "sevdesk_latest_belege_download_payload"
LATEST_BELEGE_START_DATE_KEY = "sevdesk_latest_belege_start_date"
LATEST_BELEGE_END_DATE_KEY = "sevdesk_latest_belege_end_date"
LATEST_BELEGE_API_STATUS_KEY = "sevdesk_latest_belege_api_status"
LATEST_BELEGE_HAS_DOCUMENT_KEY = "sevdesk_latest_belege_has_document"
LATEST_BELEGE_CONTACT_QUERY_KEY = "sevdesk_latest_belege_contact_query"
LATEST_BELEGE_CONTACT_MATCHES_KEY = "sevdesk_latest_belege_contact_matches"
MAX_SERVER_SIDE_CONTACT_MATCHES = 20

API_STATUS_OPTION_LABELS = {
    "": "Alle Status",
    "50": "50 - Draft",
    "100": "100 - Open",
    "1000": "1000 - Paid/Partially paid",
}
HAS_DOCUMENT_OPTION_LABELS = {
    "": "Mit oder ohne Dokument",
    "1": "Nur mit Dokument",
    "0": "Nur ohne Dokument",
}


def _contact_display_name(row: dict) -> str:
    organization_name = str(row.get("name", "")).strip()
    person_name = " ".join(
        part
        for part in (
            str(row.get("surename", "")).strip(),
            str(row.get("familyname", "")).strip(),
        )
        if part
    ).strip()
    display_name = organization_name or person_name or str(row.get("customerNumber", "")).strip() or "-"
    row_id = str(row.get("id", "")).strip() or "-"
    return f"{display_name} ({row_id})"


def _contact_matches_query(row: dict, query: str) -> bool:
    normalized_query = query.strip().lower()
    if not normalized_query:
        return False

    haystacks = [
        str(row.get("name", "")).strip(),
        str(row.get("surename", "")).strip(),
        str(row.get("familyname", "")).strip(),
        str(row.get("customerNumber", "")).strip(),
    ]
    combined_person_name = " ".join(value for value in haystacks[1:3] if value).strip()
    if combined_person_name:
        haystacks.append(combined_person_name)

    return any(normalized_query in value.lower() for value in haystacks if value)


def _find_contacts_for_server_side_filter(token: str, contact_query: str) -> list[dict]:
    normalized_query = contact_query.strip()
    if not normalized_query:
        return []

    matched_rows: list[dict] = []
    offset = 0
    page_size = 200

    while True:
        page = request_contacts(
            base_url(),
            token,
            page_size,
            offset,
            "id",
            filters={"depth": "1"},
        )
        if not page:
            break

        for row in page:
            if _contact_matches_query(row, normalized_query):
                matched_rows.append(row)
                if len(matched_rows) > MAX_SERVER_SIDE_CONTACT_MATCHES:
                    raise RuntimeError(
                        "Lieferant/Kunde filter matches more than "
                        f"{MAX_SERVER_SIDE_CONTACT_MATCHES} contacts. Please refine the search."
                    )

        if len(page) < page_size:
            break
        offset += len(page)

    return matched_rows


def _sync_multiselect_options(selection_key: str, options_key: str, options: list[str]) -> None:
    previous_options = st.session_state.get(options_key)
    current_selection = st.session_state.get(selection_key, [])

    if not options:
        st.session_state[options_key] = []
        st.session_state[selection_key] = []
        return

    if previous_options != options:
        st.session_state[options_key] = options
        st.session_state[selection_key] = options
        return

    if selection_key not in st.session_state:
        st.session_state[selection_key] = options
        return

    filtered_selection = [option for option in current_selection if option in options]
    if not filtered_selection:
        st.session_state[selection_key] = options
        return
    if filtered_selection != current_selection:
        st.session_state[selection_key] = filtered_selection


def _voucher_status_value(row: dict) -> str:
    return str(row.get("status", "")).strip()


def _voucher_status_label(status: str) -> str:
    return status or EMPTY_STATUS_FILTER_LABEL


def _build_status_filter_options(rows: list[dict]) -> dict[str, str]:
    values = sorted({_voucher_status_value(row) for row in rows})
    return {_voucher_status_label(value): value for value in values}


def _build_tag_filter_options(rows: list[dict]) -> list[str]:
    tags = sorted(
        {
            tag_name
            for row in rows
            for tag_name in extract_voucher_tag_names(row)
            if str(tag_name).strip()
        }
    )
    has_untagged_rows = any(not extract_voucher_tag_names(row) for row in rows)
    if has_untagged_rows:
        return [*tags, NO_TAGS_FILTER_LABEL]
    return tags


def _row_matches_tag_filter(row: dict, selected_tags: set[str]) -> bool:
    row_tags = set(extract_voucher_tag_names(row))
    if not row_tags:
        return NO_TAGS_FILTER_LABEL in selected_tags
    return bool(row_tags.intersection(selected_tags))


def _active_check_account_rows(rows: list[dict]) -> list[dict]:
    active_rows = [row for row in rows if str(row.get("status", "")).strip() == "100"]
    return active_rows or rows


def _check_account_label(row: dict) -> str:
    name = str(row.get("name", "")).strip() or "Unnamed"
    row_id = str(row.get("id", "")).strip() or "-"
    accounting_number = str(row.get("accountingNumber", "")).strip()
    if accounting_number:
        return f"{name} ({accounting_number} / {row_id})"
    return f"{name} ({row_id})"


def _sevdesk_start_timestamp(value: date | None) -> int | None:
    if value is None:
        return None
    return int(datetime.combine(value, time.min).timestamp())


def _sevdesk_end_timestamp(value: date | None) -> int | None:
    if value is None:
        return None
    return int(datetime.combine(value, time.max).timestamp())


def _build_voucher_request_filters() -> dict[str, object]:
    filters: dict[str, object] = {}

    start_date = st.session_state.get(LATEST_BELEGE_START_DATE_KEY)
    if isinstance(start_date, date):
        filters["startDate"] = _sevdesk_start_timestamp(start_date)

    end_date = st.session_state.get(LATEST_BELEGE_END_DATE_KEY)
    if isinstance(end_date, date):
        filters["endDate"] = _sevdesk_end_timestamp(end_date)

    status = str(st.session_state.get(LATEST_BELEGE_API_STATUS_KEY, "")).strip()
    if status:
        filters["status"] = status

    has_document = str(st.session_state.get(LATEST_BELEGE_HAS_DOCUMENT_KEY, "")).strip()
    if has_document in {"0", "1"}:
        filters["hasDocument"] = has_document

    return filters


def _merge_updated_vouchers_into_session(updated_vouchers: list[dict]) -> None:
    existing_rows = st.session_state.get(LATEST_BELEGE_ROWS_KEY)
    if not isinstance(existing_rows, list) or not existing_rows:
        return

    existing_rows_by_id = {
        str(row.get("id", "")).strip(): row
        for row in existing_rows
        if isinstance(row, dict) and str(row.get("id", "")).strip()
    }
    updated_rows_by_id = {
        str(row.get("id", "")).strip(): row
        for row in updated_vouchers
        if isinstance(row, dict) and str(row.get("id", "")).strip()
    }
    if not updated_rows_by_id:
        return

    merged_rows: list[dict] = []
    for row in existing_rows:
        row_id = str(row.get("id", "")).strip()
        updated_row = updated_rows_by_id.get(row_id)
        if updated_row is None:
            merged_rows.append(row)
            continue

        existing_tags = row.get("tags")
        merged_row = {**row, **updated_row}
        if existing_tags is not None and updated_row.get("tags") is None:
            merged_row["tags"] = existing_tags
        merged_rows.append(merged_row)

    st.session_state[LATEST_BELEGE_ROWS_KEY] = merged_rows


def _render_latest_belege_umbuchen_results() -> None:
    results = st.session_state.get(LATEST_BELEGE_UMBUCHEN_RESULTS_KEY)
    if not isinstance(results, list) or not results:
        return

    st.markdown("**Umbuchung results**")
    success_count = sum(1 for row in results if row.get("result") == "success")
    error_count = len(results) - success_count
    if error_count:
        st.warning(f"{success_count} Belege booked successfully, {error_count} failed.")
    else:
        st.success(f"{success_count} Belege booked successfully.")
    st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)


def _unique_download_name(filename: str, seen_filenames: set[str]) -> str:
    candidate = filename.strip() or "beleg.pdf"
    if candidate not in seen_filenames:
        seen_filenames.add(candidate)
        return candidate

    stem, dot, suffix = candidate.rpartition(".")
    base_name = stem if dot else candidate
    extension = f".{suffix}" if dot else ""
    counter = 2
    while True:
        deduped_name = f"{base_name}_{counter}{extension}"
        if deduped_name not in seen_filenames:
            seen_filenames.add(deduped_name)
            return deduped_name
        counter += 1


def _build_selected_belege_download_payload(
    rows: list[dict],
    token: str,
) -> dict[str, object]:
    downloaded_documents: list[dict[str, object]] = []
    seen_filenames: set[str] = set()

    for row in rows:
        voucher_id = str(row.get("id", "")).strip()
        if not voucher_id:
            raise RuntimeError("Selected Beleg is missing an id.")

        downloaded_document = download_voucher_document(
            base_url(),
            token,
            voucher_id,
        )
        fallback_filename = f"beleg_{safe_filename_token(voucher_id)}.pdf"
        filename = _unique_download_name(
            str(downloaded_document.get("filename") or fallback_filename),
            seen_filenames,
        )
        downloaded_documents.append(
            {
                "voucher_id": voucher_id,
                "filename": filename,
                "mime_type": str(downloaded_document.get("mime_type") or "application/pdf"),
                "content": downloaded_document.get("content") or b"",
            }
        )

    selection_ids = sorted(str(row.get("id", "")).strip() for row in rows if str(row.get("id", "")).strip())
    if len(downloaded_documents) == 1:
        only_document = downloaded_documents[0]
        return {
            "selection_ids": selection_ids,
            "filename": only_document["filename"],
            "mime_type": only_document["mime_type"],
            "content": only_document["content"],
        }

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for document in downloaded_documents:
            archive.writestr(str(document["filename"]), document["content"])

    archive_filename = f"belege_{'_'.join(selection_ids[:3])}"
    if len(selection_ids) > 3:
        archive_filename += f"_{len(selection_ids)}_docs"
    archive_filename = f"{safe_filename_token(archive_filename)}.zip"
    return {
        "selection_ids": selection_ids,
        "filename": archive_filename,
        "mime_type": "application/zip",
        "content": archive_buffer.getvalue(),
    }


def _render_latest_belege_umbuchen_section(
    filtered_rows: list[dict] | None,
    total_loaded_rows: int | None,
) -> None:
    st.divider()
    st.markdown("**Umbuchen auf Check Account**")

    current_rows = filtered_rows or []
    visible_row_ids = {
        str(row.get("id", "")).strip() for row in current_rows if str(row.get("id", "")).strip()
    }
    existing_selected_ids = st.session_state.get(LATEST_BELEGE_SELECTED_IDS_KEY, [])
    selected_ids = {
        str(value).strip() for value in existing_selected_ids if str(value).strip() in visible_row_ids
    }
    st.session_state[LATEST_BELEGE_SELECTED_IDS_KEY] = sorted(selected_ids)

    selected_voucher_ids = show_selectable_vouchers(
        filtered_rows,
        total_count=total_loaded_rows,
        selection_key=LATEST_BELEGE_SELECTION_TABLE_KEY,
        selected_ids=selected_ids,
    )
    selected_voucher_id_set = {
        str(value).strip() for value in selected_voucher_ids if str(value).strip() in visible_row_ids
    }
    st.session_state[LATEST_BELEGE_SELECTED_IDS_KEY] = sorted(selected_voucher_id_set)
    selected_rows = [
        row for row in current_rows if str(row.get("id", "")).strip() in selected_voucher_id_set
    ]
    current_selection_ids = sorted(selected_voucher_id_set)
    if selected_rows:
        st.caption(f"Selected Belege: {len(selected_rows)}")
    else:
        st.caption("Select one or more Belege in the table above.")

    download_payload = st.session_state.get(LATEST_BELEGE_DOWNLOAD_PAYLOAD_KEY)
    if (
        not isinstance(download_payload, dict)
        or download_payload.get("selection_ids") != current_selection_ids
    ):
        download_payload = None

    if st.button("Prepare selected Beleg PDFs for download", width="stretch", disabled=not selected_rows):
        token = ensure_token()
        if token:
            try:
                st.session_state[LATEST_BELEGE_DOWNLOAD_PAYLOAD_KEY] = (
                    _build_selected_belege_download_payload(selected_rows, token)
                )
                download_payload = st.session_state[LATEST_BELEGE_DOWNLOAD_PAYLOAD_KEY]
            except Exception as exc:
                st.session_state.pop(LATEST_BELEGE_DOWNLOAD_PAYLOAD_KEY, None)
                report_error(
                    f"Failed to prepare Beleg download: {exc}",
                    log_message="Failed to prepare selected voucher downloads",
                    exc_info=True,
                )

    if (
        isinstance(download_payload, dict)
        and download_payload.get("selection_ids") == current_selection_ids
        and download_payload.get("content")
    ):
        st.download_button(
            "Download Beleg PDF",
            data=download_payload["content"],
            file_name=str(download_payload.get("filename") or "belege.zip"),
            mime=str(download_payload.get("mime_type") or "application/zip"),
            width="stretch",
        )

    check_accounts_for_selection = st.session_state.get("sevdesk_check_accounts_rows")
    if check_accounts_for_selection is None:
        check_accounts_for_selection = load_stored_check_accounts()
    active_check_accounts = _active_check_account_rows(check_accounts_for_selection)
    if not active_check_accounts:
        st.info(
            "No stored check accounts found. Open Accounting MD in the accounting app first "
            "so you can fetch them."
        )
        _render_latest_belege_umbuchen_results()
        return

    check_account_options = {
        _check_account_label(row): str(row.get("id", "")).strip()
        for row in active_check_accounts
        if str(row.get("id", "")).strip()
    }
    if not check_account_options:
        st.info("Stored check accounts are missing usable ids. Refresh them in Accounting MD first.")
        _render_latest_belege_umbuchen_results()
        return

    selected_check_account_label = st.selectbox(
        "Target check account",
        options=list(check_account_options.keys()),
        key=LATEST_BELEGE_UMBUCHEN_CHECK_ACCOUNT_KEY,
    )

    if st.button(
        "Umbuchen ausgewählte Belege",
        width="stretch",
        disabled=not selected_rows,
        type="primary",
    ):
        token = ensure_token()
        if token:
            results: list[dict[str, str]] = []
            successful_voucher_ids: set[str] = set()
            updated_vouchers: list[dict] = []
            with st.spinner("Umbuchung in sevDesk wird ausgefuehrt..."):
                for row in selected_rows:
                    voucher_id = str(row.get("id", "")).strip()
                    description = str(row.get("description", "")).strip() or "-"
                    try:
                        booking_result = book_voucher_to_check_account(
                            base_url(),
                            token,
                            voucher_id,
                            check_account_options[selected_check_account_label],
                        )
                        successful_voucher_ids.add(voucher_id)
                        updated_voucher = booking_result.get("updated_voucher")
                        if isinstance(updated_voucher, dict):
                            updated_vouchers.append(updated_voucher)
                        results.append(
                            {
                                "result": "success",
                                "id": voucher_id,
                                "beschreibung": description,
                                "fromStatus": booking_result["before_status"] or "-",
                                "toStatus": booking_result["after_status"] or "-",
                                "payDate": booking_result["pay_date"] or "-",
                                "message": "Booked successfully.",
                            }
                        )
                    except Exception as exc:
                        results.append(
                            {
                                "result": "error",
                                "id": voucher_id,
                                "beschreibung": description,
                                "fromStatus": "-",
                                "toStatus": "-",
                                "payDate": "-",
                                "message": str(exc),
                            }
                        )

            st.session_state[LATEST_BELEGE_UMBUCHEN_RESULTS_KEY] = results
            if successful_voucher_ids:
                remaining_selected_ids = [
                    voucher_id
                    for voucher_id in st.session_state.get(LATEST_BELEGE_SELECTED_IDS_KEY, [])
                    if voucher_id not in successful_voucher_ids
                ]
                st.session_state[LATEST_BELEGE_SELECTED_IDS_KEY] = remaining_selected_ids
                _merge_updated_vouchers_into_session(updated_vouchers)

    _render_latest_belege_umbuchen_results()


def render_latest_belege_section() -> None:
    st.subheader("Belegverwaltung")
    with st.form("sevdesk_latest_belege_form"):
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            latest_limit = st.number_input(
                "Voucher limit",
                min_value=1,
                max_value=1000,
                value=50,
                step=10,
                key=LATEST_BELEGE_LIMIT_KEY,
            )
            st.selectbox(
                "Status",
                options=list(API_STATUS_OPTION_LABELS.keys()),
                format_func=lambda option: API_STATUS_OPTION_LABELS.get(option, option),
                key=LATEST_BELEGE_API_STATUS_KEY,
            )
            st.text_input(
                "Lieferant / Kunde enthält",
                key=LATEST_BELEGE_CONTACT_QUERY_KEY,
                help=(
                    "Resolves matching sevDesk contacts first and then filters vouchers "
                    "server-side by those contact ids."
                ),
            )
        with filter_col2:
            st.date_input(
                "Belegdatum ab",
                value=st.session_state.get(LATEST_BELEGE_START_DATE_KEY),
                key=LATEST_BELEGE_START_DATE_KEY,
            )
            st.date_input(
                "Belegdatum bis",
                value=st.session_state.get(LATEST_BELEGE_END_DATE_KEY),
                key=LATEST_BELEGE_END_DATE_KEY,
            )
            st.selectbox(
                "Dokument vorhanden",
                options=list(HAS_DOCUMENT_OPTION_LABELS.keys()),
                format_func=lambda option: HAS_DOCUMENT_OPTION_LABELS.get(option, option),
                key=LATEST_BELEGE_HAS_DOCUMENT_KEY,
            )
        latest_submit = st.form_submit_button("Belege laden", width="stretch")

    if latest_submit:
        token = ensure_token()
        if token:
            try:
                start_date = st.session_state.get(LATEST_BELEGE_START_DATE_KEY)
                end_date = st.session_state.get(LATEST_BELEGE_END_DATE_KEY)
                if isinstance(start_date, date) and isinstance(end_date, date) and start_date > end_date:
                    st.error("`Belegdatum ab` darf nicht nach `Belegdatum bis` liegen.")
                    return

                request_filters = _build_voucher_request_filters()
                contact_query = str(st.session_state.get(LATEST_BELEGE_CONTACT_QUERY_KEY, "")).strip()
                logger.info(
                    "Triggered 'Belege laden' from Streamlit UI with limit=%s, filters=%s, contact_query=%r.",
                    int(latest_limit),
                    request_filters,
                    contact_query,
                )
                with st.spinner("Belege werden aus sevDesk geladen..."):
                    if contact_query:
                        matched_contacts = _find_contacts_for_server_side_filter(token, contact_query)
                        st.session_state[LATEST_BELEGE_CONTACT_MATCHES_KEY] = [
                            _contact_display_name(row) for row in matched_contacts
                        ]
                        if matched_contacts:
                            st.session_state[LATEST_BELEGE_ROWS_KEY] = (
                                request_vouchers_with_tags_for_contacts(
                                    base_url(),
                                    token,
                                    int(latest_limit),
                                    [str(row.get("id", "")).strip() for row in matched_contacts],
                                    filters=request_filters,
                                    fetch_all=False,
                                )
                            )
                        else:
                            st.session_state[LATEST_BELEGE_ROWS_KEY] = []
                    else:
                        st.session_state.pop(LATEST_BELEGE_CONTACT_MATCHES_KEY, None)
                        st.session_state[LATEST_BELEGE_ROWS_KEY] = request_vouchers_with_tags(
                            base_url(),
                            token,
                            int(latest_limit),
                            filters=request_filters,
                            fetch_all=False,
                        )
                st.session_state[LATEST_BELEGE_SELECTED_IDS_KEY] = []
                st.session_state.pop(LATEST_BELEGE_SELECTION_TABLE_KEY, None)
                st.session_state.pop(LATEST_BELEGE_UMBUCHEN_RESULTS_KEY, None)
                st.session_state.pop(LATEST_BELEGE_DOWNLOAD_PAYLOAD_KEY, None)
            except Exception as exc:
                report_error(
                    f"Failed to load Belege: {exc}",
                    log_message="Failed to load Belege",
                    exc_info=True,
                )

    rows = st.session_state.get(LATEST_BELEGE_ROWS_KEY) or []
    contact_query = str(st.session_state.get(LATEST_BELEGE_CONTACT_QUERY_KEY, "")).strip()
    if contact_query:
        matched_contact_labels = st.session_state.get(LATEST_BELEGE_CONTACT_MATCHES_KEY) or []
        if matched_contact_labels:
            shown_contact_labels = ", ".join(matched_contact_labels[:5])
            if len(matched_contact_labels) > 5:
                shown_contact_labels += f", ... (+{len(matched_contact_labels) - 5} more)"
            st.caption(
                "Server-side Lieferant/Kunde filter "
                f"`{contact_query}` matched {len(matched_contact_labels)} contact(s): {shown_contact_labels}"
            )
        elif st.session_state.get(LATEST_BELEGE_ROWS_KEY) is not None:
            st.caption(
                f"Server-side Lieferant/Kunde filter `{contact_query}` did not match any sevDesk contacts."
            )

    status_options = _build_status_filter_options(rows)
    status_labels = list(status_options.keys())
    tag_labels = _build_tag_filter_options(rows)
    _sync_multiselect_options(
        LATEST_BELEGE_STATUS_FILTER_KEY,
        LATEST_BELEGE_STATUS_FILTER_OPTIONS_KEY,
        status_labels,
    )
    _sync_multiselect_options(
        LATEST_BELEGE_TAG_FILTER_KEY,
        LATEST_BELEGE_TAG_FILTER_OPTIONS_KEY,
        tag_labels,
    )

    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        selected_status_labels = st.multiselect(
            "Status filter",
            options=status_labels,
            key=LATEST_BELEGE_STATUS_FILTER_KEY,
            disabled=not status_labels,
        )
    with filter_col2:
        selected_tag_labels = st.multiselect(
            "Tag filter",
            options=tag_labels,
            key=LATEST_BELEGE_TAG_FILTER_KEY,
            disabled=not tag_labels,
        )

    filtered_rows = st.session_state.get(LATEST_BELEGE_ROWS_KEY)
    if filtered_rows:
        selected_status_values = {
            status_options[label] for label in selected_status_labels if label in status_options
        }
        selected_tag_values = {label for label in selected_tag_labels if label in tag_labels}
        filtered_rows = [
            row
            for row in filtered_rows
            if _voucher_status_value(row) in selected_status_values
            and _row_matches_tag_filter(row, selected_tag_values)
        ]

    total_count = len(rows) if st.session_state.get(LATEST_BELEGE_ROWS_KEY) is not None else None
    _render_latest_belege_umbuchen_section(filtered_rows, total_count)


def render_bookings_by_check_account_section() -> None:
    st.subheader("Bookings by Check Account")
    check_accounts_for_selection = st.session_state.get("sevdesk_check_accounts_rows")
    if check_accounts_for_selection is None:
        check_accounts_for_selection = load_stored_check_accounts()

    if check_accounts_for_selection:
        account_options = {
            f"{row.get('name', 'Unnamed')} ({row.get('id', '-')})": str(row.get("id", ""))
            for row in check_accounts_for_selection
        }
        selected_account_label = st.selectbox(
            "Check account",
            options=list(account_options.keys()),
        )
        transactions_limit = st.slider("Number of bookings", min_value=1, max_value=200, value=25)
        if st.button("Load latest bookings", width="stretch"):
            token = ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_check_account_transactions_rows"] = (
                        fetch_latest_transactions_for_check_account(
                            base_url(),
                            token,
                            account_options[selected_account_label],
                            transactions_limit,
                        )
                    )
                except Exception as exc:
                    report_error(
                        f"Failed to load bookings: {exc}",
                        log_message="Failed to load bookings",
                        exc_info=True,
                    )
    else:
        st.info(
            "Open Accounting MD in the accounting app first so you can choose a stored check account here."
        )

    show_transactions(st.session_state.get("sevdesk_check_account_transactions_rows"))


def render_browse_tab() -> None:
    col1, col2 = st.columns(2)

    with col1:
        render_latest_belege_section()

    with col2:
        render_bookings_by_check_account_section()
