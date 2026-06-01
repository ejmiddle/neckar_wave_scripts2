import io
import zipfile
from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from src.accounting.common import base_url, ensure_token, report_error, safe_filename_token
from src.accounting.master_data import load_stored_accounting_types, load_stored_check_accounts
from src.accounting.sevdesk_browse import (
    extract_voucher_tag_names,
    format_latest_voucher_row,
    format_voucher_position_row,
)
from src.accounting.ui.displays import show_selectable_vouchers, show_transactions
from src.accounting.ui.filter_utils import (
    build_status_filter_options,
    matches_text_query,
    selected_option_values,
    sync_multiselect_options,
    validate_date_range,
)
from src.logging_config import logger
from src.sevdesk.api import (
    attach_voucher_details,
    attach_voucher_positions,
    attach_voucher_tags,
    download_voucher_document,
    fetch_latest_transactions_for_check_account,
    request_contacts,
    request_vouchers,
    request_vouchers_for_contacts,
    request_vouchers_with_tags,
)
from src.sevdesk.booking import (
    book_voucher_to_check_account,
    update_voucher_accounting_type_for_positions,
    update_voucher_fields,
)

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
LATEST_BELEGE_UMBUCHEN_ACCOUNTING_TYPE_KEY = "sevdesk_latest_belege_umbuch_accounting_type"
LATEST_BELEGE_UMBUCHEN_ACCOUNTING_RESULTS_KEY = "sevdesk_latest_belege_umbuch_accounting_results"
LATEST_BELEGE_FIELD_UPDATE_RESULTS_KEY = "sevdesk_latest_belege_field_update_results"
LATEST_BELEGE_POSITION_SELECTION_TABLE_KEY = "sevdesk_latest_belege_position_selection_table"
LATEST_BELEGE_SELECTED_POSITION_IDS_KEY = "sevdesk_latest_belege_selected_position_ids"
LATEST_BELEGE_POSITION_SOURCE_IDS_KEY = "sevdesk_latest_belege_position_source_ids"
NO_TAGS_FILTER_LABEL = "(No tags)"
LATEST_BELEGE_DOWNLOAD_PAYLOAD_KEY = "sevdesk_latest_belege_download_payload"
LATEST_BELEGE_START_DATE_KEY = "sevdesk_latest_belege_start_date"
LATEST_BELEGE_END_DATE_KEY = "sevdesk_latest_belege_end_date"
LATEST_BELEGE_API_STATUS_KEY = "sevdesk_latest_belege_api_status"
LATEST_BELEGE_HAS_DOCUMENT_KEY = "sevdesk_latest_belege_has_document"
LATEST_BELEGE_CONTACT_QUERY_KEY = "sevdesk_latest_belege_contact_query"
LATEST_BELEGE_CONTACT_MATCHES_KEY = "sevdesk_latest_belege_contact_matches"
LATEST_BELEGE_TEXT_QUERY_KEY = "sevdesk_latest_belege_text_query"
LATEST_BELEGE_LOAD_MODE_KEY = "sevdesk_latest_belege_load_mode"
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
VOUCHER_LOAD_MODE_LABELS = {
    "fast": "Schnell: Basisdaten",
    "tags": "Mit Tags",
    "full": "Vollständig: Details, Positionen, Tags",
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


def _voucher_status_value(row: dict) -> str:
    return str(row.get("status", "")).strip()


def _build_tag_filter_options(rows: list[dict]) -> list[str]:
    if not any("tags" in row or "tag" in row or "voucherTags" in row for row in rows):
        return []

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


def _latest_belege_enrichment_state(rows: list[dict]) -> dict[str, int]:
    return {
        "details": sum("supplier" in row or "sumGross" in row or "document" in row for row in rows),
        "positions": sum(isinstance(row.get("voucherPos") or row.get("voucherPosSave"), list) for row in rows),
        "tags": sum("tags" in row or "tag" in row or "voucherTags" in row for row in rows),
    }


def _render_latest_belege_enrichment_controls(rows: list[dict]) -> None:
    if not rows:
        return

    enrichment_state = _latest_belege_enrichment_state(rows)
    row_count = len(rows)
    with st.expander("Daten für Downstream-Aufgaben erweitern", expanded=False):
        st.caption(
            "Basisdaten sind schnell und reichen für Liste, Suche, Status, Lieferant/Kunde und PDF-Download. "
            "Tags werden für den Tag-Filter benötigt. Details liefern den vollständigen Voucher-Payload. "
            "Buchungspositionen werden für Buchungskonto-Auswertungen und Umbuchungen benötigt."
        )
        st.caption(
            "Aktueller Stand: "
            f"Details {enrichment_state['details']}/{row_count}, "
            f"Buchungspositionen {enrichment_state['positions']}/{row_count}, "
            f"Tags {enrichment_state['tags']}/{row_count}."
        )
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            load_tags = st.button("Tags nachladen", width="stretch")
        with col2:
            load_details = st.button("Details nachladen", width="stretch")
        with col3:
            load_positions = st.button("Positionen nachladen", width="stretch")
        with col4:
            load_full = st.button("Alles nachladen", width="stretch")

        if not any((load_tags, load_details, load_positions, load_full)):
            return

        token = ensure_token()
        if not token:
            return

        try:
            with st.spinner("Belege werden fuer Downstream-Aufgaben erweitert..."):
                _enrich_latest_belege_session_rows(
                    token,
                    details=load_details or load_full,
                    positions=load_positions or load_full,
                    tags=load_tags or load_full,
                )
            st.success("Geladene Belege wurden erweitert.")
            st.rerun()
        except Exception as exc:
            report_error(
                f"Failed to enrich Belege: {exc}",
                log_message="Failed to enrich loaded vouchers",
                exc_info=True,
            )


def _row_matches_tag_filter(row: dict, selected_tags: set[str]) -> bool:
    row_tags = set(extract_voucher_tag_names(row))
    if not row_tags:
        return NO_TAGS_FILTER_LABEL in selected_tags
    return bool(row_tags.intersection(selected_tags))


def _voucher_text_matches(row: dict, query: str) -> bool:
    formatted_row = format_latest_voucher_row(row)
    return matches_text_query(
        query,
        [
            row.get("id"),
            row.get("voucherNumber"),
            row.get("number"),
            row.get("description"),
            row.get("name"),
            row.get("supplierName"),
            row.get("invoiceDate"),
            row.get("voucherDate"),
            row.get("status"),
            formatted_row.get("lieferant"),
            *extract_voucher_tag_names(row),
        ],
    )


def _active_check_account_rows(rows: list[dict]) -> list[dict]:
    active_rows = [row for row in rows if str(row.get("status", "")).strip() == "100"]
    return active_rows or rows


def _active_accounting_type_rows(rows: list[dict]) -> list[dict]:
    active_rows = [
        row
        for row in rows
        if str(row.get("status", "")).strip() == "100" and bool(row.get("active", True))
    ]
    return active_rows or rows


def _check_account_label(row: dict) -> str:
    name = str(row.get("name", "")).strip() or "Unnamed"
    row_id = str(row.get("id", "")).strip() or "-"
    accounting_number = str(row.get("accountingNumber", "")).strip()
    if accounting_number:
        return f"{name} ({accounting_number} / {row_id})"
    return f"{name} ({row_id})"


def _accounting_type_label(row: dict) -> str:
    name = str(row.get("name", "")).strip() or "Unnamed"
    row_id = str(row.get("id", "")).strip() or "-"
    skr03 = str(row.get("skr03", "")).strip()
    skr04 = str(row.get("skr04", "")).strip()
    details: list[str] = [row_id]
    if skr03:
        details.append(f"SKR03 {skr03}")
    if skr04:
        details.append(f"SKR04 {skr04}")
    return f"{name} ({' / '.join(details)})"


def _sevdesk_start_timestamp(value: date | None) -> int | None:
    if value is None:
        return None
    return int(datetime.combine(value, time.min).timestamp())


def _sevdesk_end_timestamp(value: date | None) -> int | None:
    if value is None:
        return None
    return int(datetime.combine(value, time.max).timestamp())


def _parse_voucher_date_for_input(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass

    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _sevdesk_date_string(value: date) -> str:
    return value.strftime("%d.%m.%Y")


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


def _load_vouchers_for_current_filters(
    *,
    token: str,
    limit: int,
    filters: dict[str, object],
    contact_query: str,
    load_mode: str,
) -> list[dict]:
    contact_ids: list[str] | None = None
    if contact_query:
        matched_contacts = _find_contacts_for_server_side_filter(token, contact_query)
        st.session_state[LATEST_BELEGE_CONTACT_MATCHES_KEY] = [
            _contact_display_name(row) for row in matched_contacts
        ]
        if not matched_contacts:
            return []
        contact_ids = [str(row.get("id", "")).strip() for row in matched_contacts]
    else:
        st.session_state.pop(LATEST_BELEGE_CONTACT_MATCHES_KEY, None)

    if load_mode == "full":
        if contact_ids is not None:
            rows = request_vouchers_for_contacts(
                base_url(),
                token,
                limit,
                contact_ids,
                filters=filters,
                fetch_all=False,
            )
            return _enrich_voucher_rows(rows, token, details=True, positions=True, tags=True)
        return request_vouchers_with_tags(
            base_url(),
            token,
            limit,
            filters=filters,
            fetch_all=False,
        )

    if contact_ids is not None:
        rows = request_vouchers_for_contacts(
            base_url(),
            token,
            limit,
            contact_ids,
            filters=filters,
            fetch_all=False,
        )
    else:
        rows = request_vouchers(
            base_url(),
            token,
            limit,
            filters=filters,
            fetch_all=False,
        )

    if load_mode == "tags":
        rows = _enrich_voucher_rows(rows, token, tags=True)
    return rows


def _enrich_voucher_rows(
    rows: list[dict],
    token: str,
    *,
    details: bool = False,
    positions: bool = False,
    tags: bool = False,
) -> list[dict]:
    enriched_rows = rows
    if details:
        enriched_rows = attach_voucher_details(base_url(), token, enriched_rows)
    if positions:
        enriched_rows = attach_voucher_positions(base_url(), token, enriched_rows)
    if tags:
        enriched_rows = attach_voucher_tags(base_url(), token, enriched_rows)
    return enriched_rows


def _rows_missing_positions(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if str(row.get("id", "")).strip()
        and not isinstance(row.get("voucherPos") or row.get("voucherPosSave"), list)
    ]


def _replace_rows_by_id(existing_rows: list[dict], updated_rows: list[dict]) -> list[dict]:
    updated_by_id = {
        str(row.get("id", "")).strip(): row
        for row in updated_rows
        if isinstance(row, dict) and str(row.get("id", "")).strip()
    }
    if not updated_by_id:
        return existing_rows
    return [
        updated_by_id.get(str(row.get("id", "")).strip(), row)
        for row in existing_rows
    ]


def _enrich_latest_belege_session_rows(
    token: str,
    *,
    details: bool = False,
    positions: bool = False,
    tags: bool = False,
    rows_to_enrich: list[dict] | None = None,
) -> list[dict]:
    existing_rows = st.session_state.get(LATEST_BELEGE_ROWS_KEY) or []
    if not isinstance(existing_rows, list) or not existing_rows:
        return []

    target_rows = rows_to_enrich if rows_to_enrich is not None else existing_rows
    enriched_rows = _enrich_voucher_rows(
        target_rows,
        token,
        details=details,
        positions=positions,
        tags=tags,
    )
    st.session_state[LATEST_BELEGE_ROWS_KEY] = _replace_rows_by_id(existing_rows, enriched_rows)
    return enriched_rows


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


def _selected_voucher_positions(rows: list[dict]) -> list[dict]:
    collected_positions: list[dict] = []
    for row in rows:
        voucher_id = str(row.get("id", "")).strip()
        voucher_reference = {
            "id": voucher_id,
            "voucherNumber": row.get("voucherNumber"),
            "number": row.get("number"),
            "description": row.get("description"),
        }
        positions = row.get("voucherPos") or row.get("voucherPosSave")
        if not isinstance(positions, list):
            continue
        for position in positions:
            if not isinstance(position, dict):
                continue
            position_id = str(position.get("id", "")).strip()
            if not position_id:
                continue
            position_voucher = position.get("voucher")
            collected_positions.append(
                {
                    **position,
                    "voucher": position_voucher if isinstance(position_voucher, dict) else voucher_reference,
                }
            )
    return collected_positions


def _render_selectable_voucher_positions(
    rows: list[dict],
    *,
    selection_key: str,
    selected_position_ids: set[str] | None = None,
    accounting_type_lookup: dict[str, dict] | None = None,
) -> list[str]:
    if not rows:
        st.info("Keine Buchungspositionen für die ausgewählten Belege gefunden.")
        return []

    visible_position_ids = [
        str(row.get("id", "")).strip() for row in rows if str(row.get("id", "")).strip()
    ]
    selected_id_set = {
        str(value).strip() for value in (selected_position_ids or set()) if str(value).strip()
    }
    widget_version_key = f"{selection_key}_widget_version"
    widget_version = int(st.session_state.get(widget_version_key, 0))

    action_col1, action_col2 = st.columns(2)
    with action_col1:
        select_all_clicked = st.button(
            "Alle sichtbaren Positionen auswählen",
            width="stretch",
            key=f"{selection_key}_select_all",
        )
    with action_col2:
        deselect_all_clicked = st.button(
            "Alle sichtbaren Positionen abwählen",
            width="stretch",
            key=f"{selection_key}_deselect_all",
        )

    if select_all_clicked or deselect_all_clicked:
        widget_version += 1
        st.session_state[widget_version_key] = widget_version
        selected_id_set = set(visible_position_ids) if select_all_clicked else set()

    position_df = pd.DataFrame(
        [
            {
                "selected": str(row.get("id", "")).strip() in selected_id_set,
                **format_voucher_position_row(row, accounting_type_lookup=accounting_type_lookup),
            }
            for row in rows
        ]
    )
    edited_position_df = st.data_editor(
        position_df,
        width="stretch",
        hide_index=True,
        disabled=[column for column in position_df.columns if column != "selected"],
        column_config={
            "selected": st.column_config.CheckboxColumn("Select"),
            "buchungskonto": st.column_config.TextColumn("Buchungskonto"),
            "buchungskonto_beschreibung": st.column_config.TextColumn("Beschreibung"),
            "positionstext": st.column_config.TextColumn("Positionstext"),
        },
        key=f"{selection_key}_{widget_version}",
    )

    selected_rows = edited_position_df.loc[edited_position_df["selected"], "positions_id"].tolist()
    return [str(value).strip() for value in selected_rows if str(value).strip()]


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


def _render_latest_belege_accounting_results() -> None:
    results = st.session_state.get(LATEST_BELEGE_UMBUCHEN_ACCOUNTING_RESULTS_KEY)
    if not isinstance(results, list) or not results:
        return

    st.markdown("**Buchungskonto-Updates**")
    success_count = sum(1 for row in results if row.get("result") == "success")
    skipped_count = sum(1 for row in results if row.get("result") == "skipped")
    error_count = len(results) - success_count - skipped_count
    if error_count:
        st.warning(
            f"{success_count} Belege updated successfully, {skipped_count} skipped, {error_count} failed."
        )
    elif skipped_count:
        st.success(f"{success_count} Belege updated successfully, {skipped_count} skipped.")
    else:
        st.success(f"{success_count} Belege updated successfully.")
    st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)


def _render_latest_belege_field_update_results() -> None:
    results = st.session_state.get(LATEST_BELEGE_FIELD_UPDATE_RESULTS_KEY)
    if not isinstance(results, list) or not results:
        return

    st.markdown("**Belegfeld-Updates**")
    result = results[-1]
    if result.get("result") == "success":
        st.success("Belegfelder wurden aktualisiert.")
    elif result.get("result") == "skipped":
        st.info("Keine Änderung notwendig.")
    else:
        st.warning("Belegfeld-Update fehlgeschlagen.")
    st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)


def _render_latest_belege_field_update_section(selected_rows: list[dict]) -> None:
    st.divider()
    st.markdown("**Belegfelder ändern**")

    if len(selected_rows) != 1:
        st.caption("Wähle genau einen Beleg aus, um Belegdatum, Lieferdatum oder Belegname zu ändern.")
        return

    selected_row = selected_rows[0]
    voucher_id = str(selected_row.get("id", "")).strip()
    if not voucher_id:
        st.caption("Der ausgewählte Beleg hat keine nutzbare ID.")
        return

    current_voucher_date = _parse_voucher_date_for_input(
        selected_row.get("voucherDate") or selected_row.get("invoiceDate")
    )
    current_delivery_date = _parse_voucher_date_for_input(selected_row.get("deliveryDate"))
    current_description = str(selected_row.get("description") or selected_row.get("name") or "").strip()

    form_key = f"sevdesk_beleg_field_update_form_{voucher_id}"
    with st.form(form_key):
        edited_voucher_date = st.date_input(
            "Belegdatum",
            value=current_voucher_date or date.today(),
            key=f"{form_key}_voucher_date",
        )
        edited_delivery_date = st.date_input(
            "Lieferdatum",
            value=current_delivery_date or current_voucher_date or date.today(),
            key=f"{form_key}_delivery_date",
        )
        edited_description = st.text_input(
            "Belegname / Beschreibung",
            value=current_description,
            key=f"{form_key}_description",
        )
        submitted = st.form_submit_button("Belegfelder speichern", width="stretch", type="primary")

    if not submitted:
        return

    token = ensure_token()
    if not token:
        return

    try:
        with st.spinner("Belegfelder werden in sevDesk aktualisiert..."):
            update_result = update_voucher_fields(
                base_url(),
                token,
                voucher_id,
                voucher_date=_sevdesk_date_string(edited_voucher_date),
                delivery_date=_sevdesk_date_string(edited_delivery_date),
                description=edited_description,
            )
        updated_voucher = update_result.get("updated_voucher")
        if isinstance(updated_voucher, dict):
            _merge_updated_vouchers_into_session([updated_voucher])

        change_status = str(update_result.get("change_status", "success")).strip()
        st.session_state[LATEST_BELEGE_FIELD_UPDATE_RESULTS_KEY] = [
            {
                "result": change_status,
                "id": voucher_id,
                "fromBelegdatum": update_result.get("before_voucher_date") or "-",
                "toBelegdatum": update_result.get("after_voucher_date") or "-",
                "fromLieferdatum": update_result.get("before_delivery_date") or "-",
                "toLieferdatum": update_result.get("after_delivery_date") or "-",
                "fromBeschreibung": update_result.get("before_description") or "-",
                "toBeschreibung": update_result.get("after_description") or "-",
                "message": (
                    "Keine Änderung notwendig."
                    if change_status == "skipped"
                    else "Updated successfully."
                ),
            }
        ]
    except Exception as exc:
        st.session_state[LATEST_BELEGE_FIELD_UPDATE_RESULTS_KEY] = [
            {
                "result": "error",
                "id": voucher_id,
                "fromBelegdatum": current_voucher_date.isoformat() if current_voucher_date else "-",
                "toBelegdatum": _sevdesk_date_string(edited_voucher_date),
                "fromLieferdatum": current_delivery_date.isoformat() if current_delivery_date else "-",
                "toLieferdatum": _sevdesk_date_string(edited_delivery_date),
                "fromBeschreibung": current_description or "-",
                "toBeschreibung": edited_description or "-",
                "message": str(exc),
            }
        ]
        report_error(
            f"Failed to update Belegfelder: {exc}",
            log_message="Failed to update voucher fields",
            exc_info=True,
        )


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
    st.markdown("**Ausgewählte Belege**")

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

    accounting_types_for_selection = st.session_state.get("sevdesk_accounting_types_rows")
    if accounting_types_for_selection is None:
        accounting_types_for_selection = load_stored_accounting_types()
    accounting_type_lookup = {
        str(row.get("id", "")).strip(): row
        for row in (accounting_types_for_selection or [])
        if str(row.get("id", "")).strip()
    }

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

    _render_latest_belege_field_update_section(selected_rows)

    st.divider()
    st.markdown("**Umbuchen auf Check Account**")

    check_accounts_for_selection = st.session_state.get("sevdesk_check_accounts_rows")
    if check_accounts_for_selection is None:
        check_accounts_for_selection = load_stored_check_accounts()
    active_check_accounts = _active_check_account_rows(check_accounts_for_selection)
    if not active_check_accounts:
        st.info(
            "No stored check accounts found. Open Accounting MD in the accounting app first "
            "so you can fetch them."
        )
    else:
        check_account_options = {
            _check_account_label(row): str(row.get("id", "")).strip()
            for row in active_check_accounts
            if str(row.get("id", "")).strip()
        }
        if not check_account_options:
            st.info(
                "Stored check accounts are missing usable ids. Refresh them in Accounting MD first."
            )
        else:
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

    st.divider()
    st.markdown("**Umbuchen auf Buchungskonto**")

    current_position_source_ids = st.session_state.get(LATEST_BELEGE_POSITION_SOURCE_IDS_KEY, [])
    if current_position_source_ids != current_selection_ids:
        st.session_state[LATEST_BELEGE_SELECTED_POSITION_IDS_KEY] = []

    if st.button(
        "Buchungspositionen anzeigen",
        width="stretch",
        disabled=not selected_rows,
    ):
        token = ensure_token()
        if token:
            try:
                missing_position_rows = _rows_missing_positions(selected_rows)
                if missing_position_rows:
                    with st.spinner("Buchungspositionen werden aus sevDesk nachgeladen..."):
                        _enrich_latest_belege_session_rows(
                            token,
                            positions=True,
                            rows_to_enrich=missing_position_rows,
                        )
                    current_rows = st.session_state.get(LATEST_BELEGE_ROWS_KEY) or current_rows
                    selected_rows = [
                        row
                        for row in current_rows
                        if str(row.get("id", "")).strip() in selected_voucher_id_set
                    ]
                st.session_state[LATEST_BELEGE_POSITION_SOURCE_IDS_KEY] = current_selection_ids
                st.session_state[LATEST_BELEGE_SELECTED_POSITION_IDS_KEY] = []
            except Exception as exc:
                report_error(
                    f"Failed to load Buchungspositionen: {exc}",
                    log_message="Failed to load voucher positions",
                    exc_info=True,
                )

    show_positions = (
        bool(current_selection_ids)
        and st.session_state.get(LATEST_BELEGE_POSITION_SOURCE_IDS_KEY) == current_selection_ids
    )
    selected_positions: list[dict] = []
    if show_positions:
        selected_voucher_positions = _selected_voucher_positions(selected_rows)
        selected_position_ids = {
            str(value).strip()
            for value in st.session_state.get(LATEST_BELEGE_SELECTED_POSITION_IDS_KEY, [])
            if str(value).strip()
        }
        chosen_position_ids = _render_selectable_voucher_positions(
            selected_voucher_positions,
            selection_key=LATEST_BELEGE_POSITION_SELECTION_TABLE_KEY,
            selected_position_ids=selected_position_ids,
            accounting_type_lookup=accounting_type_lookup,
        )
        chosen_position_id_set = {
            str(value).strip() for value in chosen_position_ids if str(value).strip()
        }
        st.session_state[LATEST_BELEGE_SELECTED_POSITION_IDS_KEY] = sorted(chosen_position_id_set)
        selected_positions = [
            row
            for row in selected_voucher_positions
            if str(row.get("id", "")).strip() in chosen_position_id_set
        ]
        if selected_positions:
            st.caption(f"Selected Buchungspositionen: {len(selected_positions)}")
        else:
            st.caption("Select one or more Buchungspositionen in the table above.")

    active_accounting_types = _active_accounting_type_rows(accounting_types_for_selection or [])
    if not active_accounting_types:
        st.info(
            "No stored accounting types found. Open Accounting MD in the accounting app first "
            "so you can fetch them."
        )
    else:
        accounting_type_options = {
            _accounting_type_label(row): row
            for row in active_accounting_types
            if str(row.get("id", "")).strip()
        }
        if not accounting_type_options:
            st.info(
                "Stored accounting types are missing usable ids. Refresh them in Accounting MD first."
            )
        else:
            selected_accounting_type_label = st.selectbox(
                "Target accounting type",
                options=list(accounting_type_options.keys()),
                key=LATEST_BELEGE_UMBUCHEN_ACCOUNTING_TYPE_KEY,
            )
            selected_accounting_type = accounting_type_options[selected_accounting_type_label]

            if st.button(
                "Buchungskonto ausgewählter Buchungspositionen ändern",
                width="stretch",
                disabled=not selected_positions,
                type="primary",
            ):
                token = ensure_token()
                if token:
                    results: list[dict[str, str]] = []
                    processed_position_ids: set[str] = set()
                    updated_vouchers: list[dict] = []
                    positions_by_voucher_id: dict[str, list[dict]] = {}
                    for position in selected_positions:
                        voucher = position.get("voucher")
                        if not isinstance(voucher, dict):
                            continue
                        voucher_id = str(voucher.get("id", "")).strip()
                        position_id = str(position.get("id", "")).strip()
                        if not voucher_id or not position_id:
                            continue
                        positions_by_voucher_id.setdefault(voucher_id, []).append(position)

                    with st.spinner("Buchungskonto in sevDesk wird aktualisiert..."):
                        for voucher_id, voucher_positions in positions_by_voucher_id.items():
                            try:
                                update_result = update_voucher_accounting_type_for_positions(
                                    base_url(),
                                    token,
                                    voucher_id,
                                    selected_accounting_type,
                                    [str(position.get("id", "")).strip() for position in voucher_positions],
                                )
                                change_status = str(update_result.get("change_status", "success")).strip()
                                if change_status in {"success", "skipped"}:
                                    processed_position_ids.update(
                                        update_result.get("updated_position_ids", []) or []
                                    )
                                updated_voucher = update_result.get("updated_voucher")
                                if isinstance(updated_voucher, dict):
                                    updated_vouchers.append(updated_voucher)

                                before_map = update_result.get("before_position_accounting_type_ids", {}) or {}
                                after_map = update_result.get("after_position_accounting_type_ids", {}) or {}
                                for position in voucher_positions:
                                    position_id = str(position.get("id", "")).strip()
                                    formatted_position = format_voucher_position_row(
                                        position,
                                        accounting_type_lookup=accounting_type_lookup,
                                    )
                                    results.append(
                                        {
                                            "result": change_status,
                                            "belegId": voucher_id,
                                            "positionId": position_id,
                                            "belegnummer": str(formatted_position.get("belegnummer", "-")),
                                            "beschreibung": str(formatted_position.get("beschreibung", "-")),
                                            "positionstext": str(formatted_position.get("positionstext", "-")),
                                            "fromAccountingType": str(before_map.get(position_id, "-")),
                                            "toAccountingType": str(after_map.get(position_id, "-")),
                                            "targetAccountingType": selected_accounting_type_label,
                                            "message": (
                                                "Already on target accounting type."
                                                if change_status == "skipped"
                                                else "Updated successfully."
                                            ),
                                        }
                                    )
                            except Exception as exc:
                                for position in voucher_positions:
                                    position_id = str(position.get("id", "")).strip()
                                    formatted_position = format_voucher_position_row(
                                        position,
                                        accounting_type_lookup=accounting_type_lookup,
                                    )
                                    results.append(
                                        {
                                            "result": "error",
                                            "belegId": voucher_id,
                                            "positionId": position_id,
                                            "belegnummer": str(formatted_position.get("belegnummer", "-")),
                                            "beschreibung": str(formatted_position.get("beschreibung", "-")),
                                            "positionstext": str(formatted_position.get("positionstext", "-")),
                                            "fromAccountingType": "-",
                                            "toAccountingType": "-",
                                            "targetAccountingType": selected_accounting_type_label,
                                            "message": str(exc),
                                        }
                                    )

                    st.session_state[LATEST_BELEGE_UMBUCHEN_ACCOUNTING_RESULTS_KEY] = results
                    if processed_position_ids:
                        remaining_position_ids = [
                            position_id
                            for position_id in st.session_state.get(LATEST_BELEGE_SELECTED_POSITION_IDS_KEY, [])
                            if position_id not in processed_position_ids
                        ]
                        st.session_state[LATEST_BELEGE_SELECTED_POSITION_IDS_KEY] = remaining_position_ids
                        _merge_updated_vouchers_into_session(updated_vouchers)

    _render_latest_belege_umbuchen_results()
    _render_latest_belege_accounting_results()
    _render_latest_belege_field_update_results()


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
            st.selectbox(
                "Datenumfang",
                options=list(VOUCHER_LOAD_MODE_LABELS.keys()),
                format_func=lambda option: VOUCHER_LOAD_MODE_LABELS.get(option, option),
                key=LATEST_BELEGE_LOAD_MODE_KEY,
                help=(
                    "Schnell lädt nur die Voucher-Liste. Tags lädt zusätzlich Tag-Filterdaten. "
                    "Vollständig lädt Details, Buchungspositionen und Tags wie der bisherige Ablauf."
                ),
            )
        latest_submit = st.form_submit_button("Belege laden", width="stretch")

    if latest_submit:
        token = ensure_token()
        if token:
            try:
                start_date = st.session_state.get(LATEST_BELEGE_START_DATE_KEY)
                end_date = st.session_state.get(LATEST_BELEGE_END_DATE_KEY)
                if not validate_date_range(
                    start_date,
                    end_date,
                    start_label="Belegdatum ab",
                    end_label="Belegdatum bis",
                ):
                    return

                request_filters = _build_voucher_request_filters()
                contact_query = str(st.session_state.get(LATEST_BELEGE_CONTACT_QUERY_KEY, "")).strip()
                load_mode = str(st.session_state.get(LATEST_BELEGE_LOAD_MODE_KEY, "fast")).strip()
                logger.info(
                    "Triggered 'Belege laden' from Streamlit UI with limit=%s, filters=%s, contact_query=%r, load_mode=%s.",
                    int(latest_limit),
                    request_filters,
                    contact_query,
                    load_mode,
                )
                with st.spinner("Belege werden aus sevDesk geladen..."):
                    st.session_state[LATEST_BELEGE_ROWS_KEY] = _load_vouchers_for_current_filters(
                        token=token,
                        limit=int(latest_limit),
                        filters=request_filters,
                        contact_query=contact_query,
                        load_mode=load_mode,
                    )
                st.session_state[LATEST_BELEGE_SELECTED_IDS_KEY] = []
                st.session_state.pop(LATEST_BELEGE_SELECTION_TABLE_KEY, None)
                st.session_state.pop(LATEST_BELEGE_UMBUCHEN_RESULTS_KEY, None)
                st.session_state.pop(LATEST_BELEGE_UMBUCHEN_ACCOUNTING_RESULTS_KEY, None)
                st.session_state.pop(LATEST_BELEGE_FIELD_UPDATE_RESULTS_KEY, None)
                st.session_state.pop(LATEST_BELEGE_DOWNLOAD_PAYLOAD_KEY, None)
                st.session_state.pop(LATEST_BELEGE_POSITION_SELECTION_TABLE_KEY, None)
                st.session_state[LATEST_BELEGE_SELECTED_POSITION_IDS_KEY] = []
                st.session_state[LATEST_BELEGE_POSITION_SOURCE_IDS_KEY] = []
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

    _render_latest_belege_enrichment_controls(rows)

    status_options = build_status_filter_options(
        rows,
        status_getter=_voucher_status_value,
    )
    status_labels = list(status_options.keys())
    tag_labels = _build_tag_filter_options(rows)
    sync_multiselect_options(
        LATEST_BELEGE_STATUS_FILTER_KEY,
        LATEST_BELEGE_STATUS_FILTER_OPTIONS_KEY,
        status_labels,
    )
    sync_multiselect_options(
        LATEST_BELEGE_TAG_FILTER_KEY,
        LATEST_BELEGE_TAG_FILTER_OPTIONS_KEY,
        tag_labels,
    )

    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        st.text_input(
            "Suche in Beleg",
            key=LATEST_BELEGE_TEXT_QUERY_KEY,
            help="Matches number, description, supplier, date, status, tags, and id.",
        )
    with filter_col2:
        selected_status_labels = st.multiselect(
            "Status filter",
            options=status_labels,
            key=LATEST_BELEGE_STATUS_FILTER_KEY,
            disabled=not status_labels,
        )
    with filter_col3:
        selected_tag_labels = st.multiselect(
            "Tag filter",
            options=tag_labels,
            key=LATEST_BELEGE_TAG_FILTER_KEY,
            disabled=not tag_labels,
        )

    filtered_rows = st.session_state.get(LATEST_BELEGE_ROWS_KEY)
    if filtered_rows:
        selected_status_values = selected_option_values(selected_status_labels, status_options)
        selected_tag_values = {label for label in selected_tag_labels if label in tag_labels}
        text_query = str(st.session_state.get(LATEST_BELEGE_TEXT_QUERY_KEY, "")).strip()
        if status_labels and not selected_status_values:
            filtered_rows = []
        elif tag_labels and not selected_tag_values:
            filtered_rows = []
        else:
            filtered_rows = [
                row
                for row in filtered_rows
                if _voucher_status_value(row) in selected_status_values
                and (not tag_labels or _row_matches_tag_filter(row, selected_tag_values))
                and _voucher_text_matches(row, text_query)
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
