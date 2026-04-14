import base64
import io
from pathlib import Path

import pandas as pd
import streamlit as st

from src.accounting.amazon_extraction import format_amazon_payment_row
from src.accounting.common import (
    base_url,
    cache_json_payload,
    ensure_token,
    flag_as_bool,
    load_json_payload,
    report_error,
)
from src.accounting.master_data import collect_used_accounting_type_ids_from_vouchers
from src.accounting.master_data import format_product_row
from src.accounting.sevdesk_browse import (
    format_latest_invoice_row,
    format_latest_voucher_row,
    format_transaction_row,
)
from src.accounting.state import (
    ACCOUNTING_TYPES_EXPORT_PATH,
    CHECK_ACCOUNTS_EXPORT_PATH,
    PRODUCTS_EXPORT_PATH,
    TAX_RULES_EXPORT_PATH,
    TAX_SETS_EXPORT_PATH,
)


def _cache_and_caption_payload(name: str, payload: object) -> None:
    try:
        cache_path = cache_json_payload(name, payload)
        st.caption(f"Raw payload cached at `{cache_path}`")
    except Exception as exc:
        report_error(
            f"Failed to cache raw payload: {exc}",
            log_message="Failed to cache raw payload",
            exc_info=True,
        )


def _active_accounting_type_rows(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if flag_as_bool(row.get("active", True)) and str(row.get("status", "100")) == "100"
    ]


def _format_accounting_type_row(row: dict) -> dict[str, object]:
    active = flag_as_bool(row.get("active", True))
    status = str(row.get("status", "")).strip()
    if active and status == "100":
        lifecycle = "Active"
    elif not active:
        lifecycle = "Inactive"
    elif status:
        lifecycle = f"Status {status}"
    else:
        lifecycle = "Unknown"

    return {
        "id": str(row.get("id", "")).strip(),
        "name": str(row.get("name", "")).strip(),
        "type": str(row.get("type", "")).strip(),
        "skr03": str(row.get("skr03", "")).strip() or "-",
        "skr04": str(row.get("skr04", "")).strip() or "-",
        "active": active,
        "status": status or "-",
        "state": lifecycle,
    }


def _load_used_accounting_type_ids() -> set[str] | None:
    cache_key = "sevdesk_accounting_types_used_ids_v2"
    cached_ids = st.session_state.get(cache_key)
    if isinstance(cached_ids, list):
        return {str(value).strip() for value in cached_ids if str(value).strip()}

    token = ensure_token()
    if not token:
        return None

    try:
        with st.spinner("Scanning sevDesk vouchers for used accounting types..."):
            used_ids = collect_used_accounting_type_ids_from_vouchers(base_url(), token)
    except Exception as exc:
        report_error(
            f"Failed to scan vouchers for used accounting types: {exc}",
            log_message="Failed to scan vouchers for used accounting types",
            exc_info=True,
        )
        return None

    st.session_state[cache_key] = sorted(used_ids)
    return used_ids


def _clear_used_accounting_type_ids_cache() -> None:
    st.session_state.pop("sevdesk_accounting_types_used_ids_v2", None)


def _dataframe_to_excel_bytes(dataframe: pd.DataFrame) -> io.BytesIO:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        dataframe.to_excel(writer, index=False)
    buffer.seek(0)
    return buffer


def show_vouchers(rows: list[dict] | None, *, total_count: int | None = None) -> None:
    if rows is None:
        st.caption("Load the latest sevDesk Belege to inspect them here.")
        return
    if not rows:
        if total_count:
            st.info("No Belege match the selected status/tag filters.")
            return
        st.info("No Belege found.")
        return
    if total_count is not None and total_count != len(rows):
        st.success(f"Showing {len(rows)} of {total_count} loaded Belege.")
    else:
        st.success(f"Loaded {len(rows)} Belege.")
    st.dataframe(pd.DataFrame([format_latest_voucher_row(row) for row in rows]), width="stretch")
    _cache_and_caption_payload("belege_raw_api_response", rows)


def show_invoices(rows: list[dict] | None, *, total_count: int | None = None) -> None:
    if rows is None:
        st.caption("Load the latest sevDesk Rechnungen to inspect them here.")
        return
    if not rows:
        if total_count:
            st.info("No Rechnungen match the current filters.")
            return
        st.info("No Rechnungen found.")
        return
    st.dataframe(pd.DataFrame([format_latest_invoice_row(row) for row in rows]), width="stretch")
    _cache_and_caption_payload("rechnungen_raw_api_response", rows)


def show_selectable_vouchers(
    rows: list[dict] | None,
    *,
    total_count: int | None = None,
    selection_key: str,
    selected_ids: set[str] | None = None,
) -> list[str]:
    if rows is None:
        st.caption("Load the latest sevDesk Belege to inspect them here.")
        return []
    if not rows:
        if total_count:
            st.info("No Belege match the selected status/tag filters.")
            return []
        st.info("No Belege found.")
        return []
    if total_count is not None and total_count != len(rows):
        st.success(f"Showing {len(rows)} of {total_count} loaded Belege.")
    else:
        st.success(f"Loaded {len(rows)} Belege.")

    visible_voucher_ids = [
        str(row.get("id", "")).strip() for row in rows if str(row.get("id", "")).strip()
    ]
    selected_id_set = {str(value).strip() for value in (selected_ids or set()) if str(value).strip()}
    widget_version_key = f"{selection_key}_widget_version"
    widget_version = int(st.session_state.get(widget_version_key, 0))

    action_col1, action_col2 = st.columns(2)
    with action_col1:
        select_all_clicked = st.button(
            "Alle sichtbaren Belege auswählen",
            width="stretch",
            key=f"{selection_key}_select_all",
        )
    with action_col2:
        deselect_all_clicked = st.button(
            "Alle sichtbaren Belege abwählen",
            width="stretch",
            key=f"{selection_key}_deselect_all",
        )

    if select_all_clicked or deselect_all_clicked:
        widget_version += 1
        st.session_state[widget_version_key] = widget_version
        selected_id_set = set(visible_voucher_ids) if select_all_clicked else set()

    voucher_df = pd.DataFrame(
        [
            {
                "selected": str(row.get("id", "")).strip() in selected_id_set,
                **format_latest_voucher_row(row),
            }
            for row in rows
        ]
    )
    edited_voucher_df = st.data_editor(
        voucher_df,
        width="stretch",
        hide_index=True,
        disabled=[column for column in voucher_df.columns if column != "selected"],
        column_config={
            "selected": st.column_config.CheckboxColumn("Select"),
        },
        key=f"{selection_key}_{widget_version}",
    )
    _cache_and_caption_payload("belege_selection_raw_api_response", rows)

    selected_rows = edited_voucher_df.loc[edited_voucher_df["selected"], "id"].tolist()
    return [str(value).strip() for value in selected_rows if str(value).strip()]


def show_accounting_types(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk accounting types to inspect them here.")
        return
    if not rows:
        st.info("No accounting types stored yet.")
        return

    active_rows = _active_accounting_type_rows(rows)
    inactive_count = len(rows) - len(active_rows)
    missing_skr03 = sum(not str(row.get("skr03", "")).strip() for row in rows)
    missing_skr04 = sum(not str(row.get("skr04", "")).strip() for row in rows)
    type_values = sorted(
        {
            str(row.get("type", "")).strip()
            for row in rows
            if str(row.get("type", "")).strip()
        }
    )

    st.success(f"Stored {len(rows)} accounting types in `{ACCOUNTING_TYPES_EXPORT_PATH}`.")
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    with metric_col1:
        st.metric("Total", len(rows))
    with metric_col2:
        st.metric("Active", len(active_rows))
    with metric_col3:
        st.metric("Inactive", inactive_count)
    with metric_col4:
        st.metric("Distinct types", len(type_values))

    info_col1, info_col2 = st.columns(2)
    with info_col1:
        st.caption(f"Missing SKR03 entries: {missing_skr03}")
    with info_col2:
        st.caption(f"Missing SKR04 entries: {missing_skr04}")

    search_query = st.text_input(
        "Filter accounting types by name",
        placeholder="Type a name fragment, e.g. Material or Umsatzsteuer",
        key="sevdesk_accounting_types_search",
    ).strip().lower()
    show_only_active = st.checkbox(
        "Show active accounting types only",
        value=True,
        key="sevdesk_accounting_types_active_only",
    )
    show_only_used = st.checkbox(
        "Show accounting types used in this sevDesk instance only",
        value=False,
        key="sevdesk_accounting_types_used_only",
        help=(
            "Filters to accounting type IDs referenced by vouchers currently loaded from sevDesk."
        ),
    )

    overview_rows = [_format_accounting_type_row(row) for row in rows]
    filtered_rows = overview_rows
    if show_only_active:
        filtered_rows = [row for row in filtered_rows if row["state"] == "Active"]
    if show_only_used:
        refresh_clicked = st.button(
            "Refresh voucher usage scan",
            width="stretch",
            key="sevdesk_accounting_types_used_refresh",
        )
        if refresh_clicked:
            _clear_used_accounting_type_ids_cache()
        used_accounting_type_ids = _load_used_accounting_type_ids()
        if used_accounting_type_ids is None:
            st.info("Set a sevDesk API token to scan vouchers for used accounting types.")
            return
        st.caption(
            f"Detected {len(used_accounting_type_ids)} accounting type IDs from vouchers in sevDesk."
        )
        filtered_rows = [
            row for row in filtered_rows if row["id"] and row["id"] in used_accounting_type_ids
        ]
    if search_query:
        filtered_rows = [
            row
            for row in filtered_rows
            if search_query in row["name"].casefold() or search_query in row["id"].casefold()
        ]

    if not filtered_rows:
        st.info("No accounting types match the current filters.")
        return

    st.caption(
        "Overview of the stored sevDesk accounting types with the key bookkeeping fields."
    )
    overview_df = pd.DataFrame(filtered_rows)
    export_df = overview_df[
        [
            "id",
            "name",
            "type",
            "skr03",
            "skr04",
            "active",
            "status",
            "state",
        ]
    ]
    st.download_button(
        "Download accounting types as Excel",
        data=_dataframe_to_excel_bytes(export_df),
        file_name="accounting_types_filtered.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    st.dataframe(
        export_df,
        width="stretch",
        hide_index=True,
        column_config={
            "active": st.column_config.CheckboxColumn("Active"),
            "state": st.column_config.TextColumn("Lifecycle"),
        },
    )


def show_products(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk products to inspect them here.")
        return
    if not rows:
        st.info("No products stored yet.")
        return

    st.success(f"Stored {len(rows)} products in `{PRODUCTS_EXPORT_PATH}`.")
    product_df = pd.DataFrame([format_product_row(row) for row in rows])
    export_df = product_df[
        [
            "id",
            "name",
            "articleNumber",
            "description",
            "unity",
            "priceNet",
            "priceGross",
            "stock",
            "taxRule",
            "active",
            "status",
        ]
    ]
    st.download_button(
        "Download products as Excel",
        data=_dataframe_to_excel_bytes(export_df),
        file_name="products_filtered.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    st.dataframe(
        export_df,
        width="stretch",
        hide_index=True,
        column_config={
            "active": st.column_config.CheckboxColumn("Active"),
        },
    )


def show_check_accounts(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk check accounts to inspect them here.")
        return
    if not rows:
        st.info("No check accounts stored yet.")
        return
    st.success(f"Stored {len(rows)} check accounts in `{CHECK_ACCOUNTS_EXPORT_PATH}`.")


def show_tax_rules(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk tax rules to inspect them here.")
        return
    if not rows:
        st.info("No tax rules stored yet.")
        return
    st.success(f"Stored {len(rows)} tax rules in `{TAX_RULES_EXPORT_PATH}`.")


def show_tax_sets(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Fetch and store sevDesk tax sets to inspect them here.")
        return
    if not rows:
        st.info("No tax sets stored yet.")
        return
    st.success(f"Stored {len(rows)} tax sets in `{TAX_SETS_EXPORT_PATH}`.")


def show_amazon_customers(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Amazon customers are loaded live from sevDesk when the page starts.")
        return
    if not rows:
        st.info("No sevDesk customers with `Amazon` in the name were found.")
        return
    st.success(f"Loaded {len(rows)} live sevDesk customer entries with `Amazon` in the name.")
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def show_transactions(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Select a stored check account and load its latest bookings.")
        return
    if not rows:
        st.info("No bookings found for the selected check account.")
        return
    st.success(f"Loaded {len(rows)} bookings.")
    st.dataframe(pd.DataFrame([format_transaction_row(row) for row in rows]), width="stretch")
    _cache_and_caption_payload("check_account_transactions_raw_api_response", rows)


def show_selectable_transactions(
    rows: list[dict] | None,
    *,
    total_count: int | None = None,
    selection_key: str,
    selected_ids: set[str] | None = None,
) -> list[str]:
    if rows is None:
        st.caption("Load payments to inspect them here.")
        return []
    if not rows:
        if total_count:
            st.info("No payments match the selected filters.")
            return []
        st.info("No payments found.")
        return []
    if total_count is not None and total_count != len(rows):
        st.success(f"Showing {len(rows)} of {total_count} loaded payments.")
    else:
        st.success(f"Loaded {len(rows)} payments.")

    visible_transaction_ids = [
        str(row.get("id", "")).strip() for row in rows if str(row.get("id", "")).strip()
    ]
    selected_id_set = {str(value).strip() for value in (selected_ids or set()) if str(value).strip()}
    widget_version_key = f"{selection_key}_widget_version"
    widget_version = int(st.session_state.get(widget_version_key, 0))

    action_col1, action_col2 = st.columns(2)
    with action_col1:
        select_all_clicked = st.button(
            "Alle sichtbaren Zahlungen auswählen",
            width="stretch",
            key=f"{selection_key}_select_all",
        )
    with action_col2:
        deselect_all_clicked = st.button(
            "Alle sichtbaren Zahlungen abwählen",
            width="stretch",
            key=f"{selection_key}_deselect_all",
        )

    if select_all_clicked or deselect_all_clicked:
        widget_version += 1
        st.session_state[widget_version_key] = widget_version
        selected_id_set = set(visible_transaction_ids) if select_all_clicked else set()

    transaction_df = pd.DataFrame(
        [
            {
                "selected": str(row.get("id", "")).strip() in selected_id_set,
                **format_transaction_row(row),
            }
            for row in rows
        ]
    )
    edited_transaction_df = st.data_editor(
        transaction_df,
        width="stretch",
        hide_index=True,
        disabled=[column for column in transaction_df.columns if column != "selected"],
        column_config={
            "selected": st.column_config.CheckboxColumn("Select"),
        },
        key=f"{selection_key}_{widget_version}",
    )
    _cache_and_caption_payload("zahlungen_selection_raw_api_response", rows)

    selected_rows = edited_transaction_df.loc[edited_transaction_df["selected"], "id"].tolist()
    return [str(value).strip() for value in selected_rows if str(value).strip()]


def show_amazon_payments(rows: list[dict] | None) -> None:
    if rows is None:
        st.caption("Load Sparkasse bookings filtered for Amazon Payments Europe here.")
        return
    if not rows:
        st.info("No matching Amazon Payments Europe bookings found in the Sparkasse account.")
        return
    st.success(f"Loaded {len(rows)} matching Sparkasse bookings.")
    st.dataframe(pd.DataFrame([format_amazon_payment_row(row) for row in rows]), width="stretch")
    _cache_and_caption_payload("amazon_payments_raw_api_response", rows)


def render_pdf_inline(path_str: str, *, height: int = 420) -> None:
    path = Path(path_str)
    if not path.exists():
        report_error(f"PDF not found: {path}")
        return
    encoded_pdf = base64.b64encode(path.read_bytes()).decode("ascii")
    st.markdown(
        (
            '<iframe src="data:application/pdf;base64,'
            f"{encoded_pdf}"
            f'" width="100%" height="{height}" type="application/pdf"></iframe>'
        ),
        unsafe_allow_html=True,
    )


def show_downloaded_payload(title: str, path: Path) -> None:
    st.markdown(f"**{title}**")
    st.caption(f"`{path}`")
    payload = load_json_payload(path)
    if payload is None:
        st.info("No downloaded data file found.")
        return
    st.caption("Payload available on disk.")
