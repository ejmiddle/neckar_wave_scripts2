from datetime import date, timedelta
from inspect import signature

import pandas as pd
import streamlit as st

from src.accounting.common import (
    base_url,
    ensure_token,
    format_currency_value,
    parse_amount_value,
    parse_transaction_date,
    report_error,
)
from src.accounting.master_data import load_stored_check_accounts
from src.accounting.payment_vouchers import (
    TRANSFER_VOUCHER_SUPPLIER_OPTIONS,
    build_transfer_voucher_payload,
)
from src.accounting.state import TRANSACTION_STATUS_LABELS
from src.accounting.ui.displays import show_selectable_transactions
from src.accounting.ui.filter_utils import (
    build_status_filter_options,
    is_within_date_range,
    matches_text_query,
    selected_option_values,
    sync_multiselect_options,
    validate_date_range,
)
from src.logging_config import logger
from src.sevdesk.api import create_voucher, fetch_all_transactions_for_check_account
from src.sevdesk.payments import (
    move_transaction_to_check_account,
    move_transaction_to_check_account_old_logic,
)
from src.sevdesk.voucher import first_object_from_response

PAYMENTS_ROWS_KEY = "sevdesk_payments_rows"
PAYMENTS_SOURCE_ACCOUNT_KEY = "sevdesk_payments_source_account"
PAYMENTS_START_DATE_KEY = "sevdesk_payments_start_date"
PAYMENTS_END_DATE_KEY = "sevdesk_payments_end_date"
PAYMENTS_STATUS_FILTER_KEY = "sevdesk_payments_status_filter"
PAYMENTS_STATUS_FILTER_OPTIONS_KEY = "sevdesk_payments_status_filter_options"
PAYMENTS_TEXT_QUERY_KEY = "sevdesk_payments_text_query"
PAYMENTS_DIRECTION_KEY = "sevdesk_payments_direction"
PAYMENTS_MIN_AMOUNT_KEY = "sevdesk_payments_min_amount"
PAYMENTS_MAX_AMOUNT_KEY = "sevdesk_payments_max_amount"
PAYMENTS_SELECTION_TABLE_KEY = "sevdesk_payments_selection_table"
PAYMENTS_SELECTED_IDS_KEY = "sevdesk_payments_selected_ids"
PAYMENTS_TARGET_ACCOUNT_KEY = "sevdesk_payments_target_account"
PAYMENTS_RESULTS_KEY = "sevdesk_payments_results"
PAYMENTS_ACTION_KEY = "sevdesk_payments_action"
PAYMENTS_TRANSFER_VOUCHER_SUPPLIER_KEY = "sevdesk_payments_transfer_voucher_supplier"
PAYMENTS_TRANSFER_VOUCHER_RESULTS_KEY = "sevdesk_payments_transfer_voucher_results"
PAYMENTS_TRANSFER_VOUCHER_CREATED_IDS_KEY = "sevdesk_payments_transfer_voucher_created_ids"
PAYMENTS_DEFAULT_LOAD_DAYS = 60

PAYMENT_DIRECTION_OPTIONS = {
    "all": "Alle Beträge",
    "incoming": "Nur Eingänge",
    "outgoing": "Nur Ausgänge",
}

PAYMENT_ACTION_LABELS = {
    "create_transfer_vouchers": "Geldtransfer-Belege erstellen",
    "transfer_to_clearing": "Auf Verrechnungskonto umbuchen",
    "move_bookings": "Buchungen vollständig verschieben",
}


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


def _status_option_label(status: str) -> str:
    meaning = TRANSACTION_STATUS_LABELS.get(status, "Unknown")
    return f"{status or '-'} - {meaning}"


def _load_payments_for_check_account(
    source_check_account_id: str,
    *,
    start_date: date | None,
    end_date: date | None,
) -> list[dict]:
    token = ensure_token()
    if not token:
        return []
    with st.spinner("Zahlungen werden aus sevDesk geladen..."):
        helper_signature = signature(fetch_all_transactions_for_check_account)
        if "start_date" in helper_signature.parameters:
            return fetch_all_transactions_for_check_account(
                base_url(),
                token,
                source_check_account_id,
                start_date=start_date,
                end_date=end_date,
            )
        rows = fetch_all_transactions_for_check_account(base_url(), token, source_check_account_id)
        return [
            row
            for row in rows
            if is_within_date_range(
                parse_transaction_date(row),
                start_date=start_date,
                end_date=end_date,
            )
        ]


def _render_payment_results() -> None:
    results = st.session_state.get(PAYMENTS_RESULTS_KEY)
    if not isinstance(results, list) or not results:
        return
    st.markdown("**Umbuchung results**")
    success_count = sum(1 for row in results if row.get("result") == "success")
    error_count = len(results) - success_count
    if error_count:
        st.warning(f"{success_count} payments reassigned successfully, {error_count} failed.")
    else:
        st.success(f"{success_count} payments reassigned successfully.")
    st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)


def _render_transfer_voucher_results() -> None:
    results = st.session_state.get(PAYMENTS_TRANSFER_VOUCHER_RESULTS_KEY)
    if not isinstance(results, list) or not results:
        return
    st.markdown("**Geldtransfer-Belege**")
    success_count = sum(1 for row in results if row.get("result") == "success")
    error_count = len(results) - success_count
    if error_count:
        st.warning(f"{success_count} Belege erstellt, {error_count} fehlgeschlagen.")
    else:
        st.success(f"{success_count} Belege erstellt.")
    st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)


def _created_transfer_voucher_transaction_ids() -> set[str]:
    return {
        str(value).strip()
        for value in st.session_state.get(PAYMENTS_TRANSFER_VOUCHER_CREATED_IDS_KEY, [])
        if str(value).strip()
    }


def _run_create_transfer_vouchers(
    *,
    selected_rows: list[dict],
    supplier_name: str,
) -> None:
    token = ensure_token()
    if not token:
        return

    results: list[dict[str, str]] = []
    created_transaction_ids = _created_transfer_voucher_transaction_ids()
    with st.spinner("Geldtransfer-Belege werden in sevDesk erstellt..."):
        for row in selected_rows:
            transaction_id = str(row.get("id", "")).strip()
            if transaction_id in created_transaction_ids:
                continue
            try:
                payload = build_transfer_voucher_payload(row, supplier_name)
                response_payload = create_voucher(base_url(), token, payload)
                created_summary = first_object_from_response(response_payload) or {}
                created_id = str(created_summary.get("id", "")).strip()
                if transaction_id:
                    created_transaction_ids.add(transaction_id)
                results.append(
                    {
                        "result": "success",
                        "transactionId": transaction_id or "-",
                        "voucherId": created_id or "-",
                        "lieferant": supplier_name,
                        "betrag": str(row.get("amount", "")).strip() or "-",
                        "payeePayerName": str(row.get("payeePayerName", "")).strip() or "-",
                        "message": "Beleg erstellt.",
                    }
                )
            except Exception as exc:
                logger.exception(
                    "Failed to create transfer voucher for payment id=%s supplier=%s.",
                    transaction_id,
                    supplier_name,
                )
                results.append(
                    {
                        "result": "error",
                        "transactionId": transaction_id or "-",
                        "voucherId": "-",
                        "lieferant": supplier_name,
                        "betrag": str(row.get("amount", "")).strip() or "-",
                        "payeePayerName": str(row.get("payeePayerName", "")).strip() or "-",
                        "message": str(exc),
                    }
                )

    st.session_state[PAYMENTS_TRANSFER_VOUCHER_RESULTS_KEY] = results
    st.session_state[PAYMENTS_TRANSFER_VOUCHER_CREATED_IDS_KEY] = sorted(created_transaction_ids)
    if created_transaction_ids:
        st.session_state[PAYMENTS_SELECTED_IDS_KEY] = [
            transaction_id
            for transaction_id in st.session_state.get(PAYMENTS_SELECTED_IDS_KEY, [])
            if str(transaction_id).strip() not in created_transaction_ids
        ]


def _updated_transaction_from_result(result: dict) -> dict | None:
    updated_transaction = result.get("updated_source_transaction")
    if isinstance(updated_transaction, dict):
        return updated_transaction
    updated_transaction = result.get("updated_transaction")
    if isinstance(updated_transaction, dict):
        return updated_transaction
    return None


def _run_transfer_action(
    *,
    action_label: str,
    all_rows: list[dict],
    selected_rows: list[dict],
    selected_source_account_id: str,
    selected_target_account_id: str,
    source_account_name: str,
    target_account_type: str,
    transfer_function,
) -> None:
    token = ensure_token()
    if not token:
        return

    results: list[dict[str, str]] = []
    successful_results: list[dict] = []
    successful_transaction_ids: set[str] = set()
    with st.spinner(f"{action_label} in sevDesk wird ausgefuehrt..."):
        for row in selected_rows:
            transaction_id = str(row.get("id", "")).strip()
            try:
                booking_result = transfer_function(
                    base_url(),
                    token,
                    transaction_id,
                    selected_target_account_id,
                    source_check_account_name=source_account_name,
                    target_check_account_type=target_account_type,
                )
                successful_transaction_ids.add(transaction_id)
                successful_results.append(booking_result)
                results.append(
                    {
                        "result": "success",
                        "id": transaction_id,
                        "logic": action_label,
                        "betrag": str(row.get("amount", "")).strip() or "-",
                        "payeePayerName": str(row.get("payeePayerName", "")).strip() or "-",
                        "fromCheckAccount": booking_result["before_check_account_id"] or "-",
                        "toCheckAccount": booking_result["after_check_account_id"] or "-",
                        "targetTransactionId": booking_result.get("target_transaction_id", "-")
                        or "-",
                        "message": "Reassigned successfully.",
                    }
                )
            except Exception as exc:
                logger.exception(
                    "Failed to transfer payment id=%s from check account id=%s to check account id=%s.",
                    transaction_id,
                    selected_source_account_id,
                    selected_target_account_id,
                )
                results.append(
                    {
                        "result": "error",
                        "id": transaction_id,
                        "logic": action_label,
                        "betrag": str(row.get("amount", "")).strip() or "-",
                        "payeePayerName": str(row.get("payeePayerName", "")).strip() or "-",
                        "fromCheckAccount": "-",
                        "toCheckAccount": "-",
                        "message": str(exc),
                    }
                )

    st.session_state[PAYMENTS_RESULTS_KEY] = results
    if successful_transaction_ids:
        updated_transactions = {
            str(result.get("transaction_id", "")).strip(): _updated_transaction_from_result(result)
            for result in successful_results
            if str(result.get("transaction_id", "")).strip()
            and _updated_transaction_from_result(result) is not None
        }
        st.session_state[PAYMENTS_ROWS_KEY] = [
            updated_transactions.get(str(row.get("id", "")).strip(), row) for row in all_rows
        ]
        st.session_state.pop(PAYMENTS_SELECTION_TABLE_KEY, None)
        st.session_state[PAYMENTS_SELECTED_IDS_KEY] = [
            transaction_id
            for transaction_id in st.session_state.get(PAYMENTS_SELECTED_IDS_KEY, [])
            if transaction_id not in successful_transaction_ids
        ]


def _matches_text_query(row: dict, query: str) -> bool:
    return matches_text_query(
        query,
        [
            row.get("payeePayerName"),
            row.get("paymtPurpose"),
            row.get("entryText"),
            row.get("id"),
        ],
    )


def _matches_amount_filters(row: dict, min_amount: float | None, max_amount: float | None) -> bool:
    amount = parse_amount_value(row.get("amount"))
    if amount is None:
        return False
    abs_amount = abs(amount)
    if min_amount is not None and abs_amount < min_amount:
        return False
    if max_amount is not None and abs_amount > max_amount:
        return False
    return True


def _matches_direction_filter(row: dict, direction: str) -> bool:
    amount = parse_amount_value(row.get("amount"))
    if amount is None:
        return False
    if direction == "incoming":
        return amount > 0
    if direction == "outgoing":
        return amount < 0
    return True


def _filtered_payment_rows(rows: list[dict]) -> list[dict]:
    selected_status_labels = st.session_state.get(PAYMENTS_STATUS_FILTER_KEY, [])
    status_options = build_status_filter_options(
        rows,
        status_getter=lambda row: str(row.get("status", "")).strip(),
        label_formatter=_status_option_label,
    )
    selected_status_values = selected_option_values(selected_status_labels, status_options)
    if status_options and not selected_status_values:
        return []
    text_query = str(st.session_state.get(PAYMENTS_TEXT_QUERY_KEY, "")).strip()
    direction = str(st.session_state.get(PAYMENTS_DIRECTION_KEY, "all")).strip() or "all"
    min_amount = parse_amount_value(st.session_state.get(PAYMENTS_MIN_AMOUNT_KEY))
    max_amount = parse_amount_value(st.session_state.get(PAYMENTS_MAX_AMOUNT_KEY))
    start_date = st.session_state.get(PAYMENTS_START_DATE_KEY)
    end_date = st.session_state.get(PAYMENTS_END_DATE_KEY)

    filtered_rows: list[dict] = []
    for row in rows:
        row_status = str(row.get("status", "")).strip()
        if selected_status_values and row_status not in selected_status_values:
            continue
        if not _matches_text_query(row, text_query):
            continue
        if not _matches_direction_filter(row, direction):
            continue
        if not _matches_amount_filters(row, min_amount, max_amount):
            continue
        row_date = parse_transaction_date(row)
        if not is_within_date_range(row_date, start_date=start_date, end_date=end_date):
            continue
        filtered_rows.append(row)
    return filtered_rows


def render_payments_section() -> None:
    st.subheader("Zahlungsverwaltung")
    check_accounts_for_selection = st.session_state.get("sevdesk_check_accounts_rows")
    if check_accounts_for_selection is None:
        check_accounts_for_selection = load_stored_check_accounts()
    active_check_accounts = _active_check_account_rows(check_accounts_for_selection)
    if not active_check_accounts:
        st.info(
            "No stored check accounts found. Open Accounting MD in the accounting app first "
            "so you can fetch them."
        )
        return

    account_options = {
        _check_account_label(row): str(row.get("id", "")).strip()
        for row in active_check_accounts
        if str(row.get("id", "")).strip()
    }
    account_rows_by_id = {
        str(row.get("id", "")).strip(): row
        for row in active_check_accounts
        if str(row.get("id", "")).strip()
    }
    if not account_options:
        st.info("Stored check accounts are missing usable ids. Refresh them in Accounting MD first.")
        return

    default_end_date = st.session_state.get(PAYMENTS_END_DATE_KEY) or date.today()
    default_start_date = st.session_state.get(PAYMENTS_START_DATE_KEY) or (
        default_end_date - timedelta(days=PAYMENTS_DEFAULT_LOAD_DAYS)
    )

    with st.form("sevdesk_payments_form"):
        st.selectbox(
            "Quellkonto",
            options=list(account_options.keys()),
            key=PAYMENTS_SOURCE_ACCOUNT_KEY,
        )
        load_date_col1, load_date_col2 = st.columns(2)
        with load_date_col1:
            st.date_input("Wertstellung ab", value=default_start_date, key=PAYMENTS_START_DATE_KEY)
        with load_date_col2:
            st.date_input("Wertstellung bis", value=default_end_date, key=PAYMENTS_END_DATE_KEY)
        submit = st.form_submit_button("Zahlungen laden", width="stretch")

    if submit:
        try:
            selected_source_label = str(st.session_state.get(PAYMENTS_SOURCE_ACCOUNT_KEY, "")).strip()
            source_account_id = account_options[selected_source_label]
            start_date = st.session_state.get(PAYMENTS_START_DATE_KEY)
            end_date = st.session_state.get(PAYMENTS_END_DATE_KEY)
            if not validate_date_range(
                start_date,
                end_date,
                start_label="Wertstellung ab",
                end_label="Wertstellung bis",
            ):
                return
            st.session_state[PAYMENTS_ROWS_KEY] = _load_payments_for_check_account(
                source_account_id,
                start_date=start_date,
                end_date=end_date,
            )
            st.session_state[PAYMENTS_SELECTED_IDS_KEY] = []
            st.session_state.pop(PAYMENTS_SELECTION_TABLE_KEY, None)
            st.session_state.pop(PAYMENTS_RESULTS_KEY, None)
            st.session_state.pop(PAYMENTS_TRANSFER_VOUCHER_RESULTS_KEY, None)
            st.session_state.pop(PAYMENTS_TRANSFER_VOUCHER_CREATED_IDS_KEY, None)
            st.session_state.pop(PAYMENTS_ACTION_KEY, None)
        except Exception as exc:
            report_error(
                f"Failed to load payments: {exc}",
                log_message="Failed to load payments",
                exc_info=True,
            )

    rows = st.session_state.get(PAYMENTS_ROWS_KEY)
    if rows is None:
        st.caption("Select a source check account and load payments.")
        return
    st.markdown("**Transaktionen filtern**")
    st.caption(
        "Geladener Zeitraum: "
        f"{st.session_state.get(PAYMENTS_START_DATE_KEY)} bis "
        f"{st.session_state.get(PAYMENTS_END_DATE_KEY)}"
    )
    status_options = build_status_filter_options(
        rows,
        status_getter=lambda row: str(row.get("status", "")).strip(),
        label_formatter=_status_option_label,
    )
    status_labels = list(status_options.keys())
    sync_multiselect_options(
        PAYMENTS_STATUS_FILTER_KEY,
        PAYMENTS_STATUS_FILTER_OPTIONS_KEY,
        status_labels,
    )
    search_col, status_col = st.columns([2, 1])
    with search_col:
        st.text_input(
            "Suche",
            key=PAYMENTS_TEXT_QUERY_KEY,
            help="Matches payee/payer, payment purpose, entry text, and payment id.",
        )
    with status_col:
        st.multiselect(
            "Status",
            options=status_labels,
            key=PAYMENTS_STATUS_FILTER_KEY,
            disabled=not status_labels,
        )
    amount_col1, amount_col2, amount_col3 = st.columns(3)
    with amount_col1:
        st.selectbox(
            "Betragsrichtung",
            options=list(PAYMENT_DIRECTION_OPTIONS.keys()),
            format_func=lambda option: PAYMENT_DIRECTION_OPTIONS[option],
            key=PAYMENTS_DIRECTION_KEY,
        )
    with amount_col2:
        st.text_input("Betrag min", key=PAYMENTS_MIN_AMOUNT_KEY)
    with amount_col3:
        st.text_input("Betrag max", key=PAYMENTS_MAX_AMOUNT_KEY)

    start_date = st.session_state.get(PAYMENTS_START_DATE_KEY)
    end_date = st.session_state.get(PAYMENTS_END_DATE_KEY)
    if not validate_date_range(
        start_date,
        end_date,
        start_label="Wertstellung ab",
        end_label="Wertstellung bis",
    ):
        return

    filtered_rows = _filtered_payment_rows(rows)
    visible_row_ids = {
        str(row.get("id", "")).strip() for row in filtered_rows if str(row.get("id", "")).strip()
    }
    selected_ids = {
        str(value).strip()
        for value in st.session_state.get(PAYMENTS_SELECTED_IDS_KEY, [])
        if str(value).strip() in visible_row_ids
    }
    st.session_state[PAYMENTS_SELECTED_IDS_KEY] = sorted(selected_ids)

    selected_payment_ids = show_selectable_transactions(
        filtered_rows,
        total_count=len(rows),
        selection_key=PAYMENTS_SELECTION_TABLE_KEY,
        selected_ids=selected_ids,
    )
    selected_payment_id_set = {
        str(value).strip() for value in selected_payment_ids if str(value).strip() in visible_row_ids
    }
    st.session_state[PAYMENTS_SELECTED_IDS_KEY] = sorted(selected_payment_id_set)
    selected_rows = [
        row for row in filtered_rows if str(row.get("id", "")).strip() in selected_payment_id_set
    ]

    st.markdown("**Aktion für Auswahl**")
    if selected_rows:
        selected_sum = sum(parse_amount_value(row.get("amount")) or 0.0 for row in selected_rows)
        st.caption(
            f"Ausgewählte Zahlungen: {len(selected_rows)} | Summe: "
            f"{format_currency_value(selected_sum)}"
        )
    else:
        st.caption("Wähle eine oder mehrere Zahlungen in der Tabelle aus.")

    selected_source_label = str(st.session_state.get(PAYMENTS_SOURCE_ACCOUNT_KEY, "")).strip()
    target_options = {
        label: account_id
        for label, account_id in account_options.items()
        if account_id != account_options[selected_source_label]
    }
    created_transfer_voucher_ids = _created_transfer_voucher_transaction_ids()
    voucher_candidate_rows = [
        row
        for row in selected_rows
        if str(row.get("id", "")).strip() not in created_transfer_voucher_ids
    ]
    already_created_count = len(selected_rows) - len(voucher_candidate_rows)

    action_options = ["create_transfer_vouchers"]
    if target_options:
        action_options.extend(["transfer_to_clearing", "move_bookings"])

    if already_created_count:
        st.success(
            f"Für {already_created_count} der ausgewählten Zahlungen wurden bereits "
            "Geldtransfer-Belege erstellt."
        )

    if not action_options:
        st.info("Für die aktuelle Auswahl ist keine weitere Aktion verfügbar.")
        _render_payment_results()
        _render_transfer_voucher_results()
        return
    if st.session_state.get(PAYMENTS_ACTION_KEY) not in action_options:
        st.session_state[PAYMENTS_ACTION_KEY] = action_options[0]

    selected_action = st.selectbox(
        "Aktion",
        options=action_options,
        format_func=lambda option: PAYMENT_ACTION_LABELS[option],
        key=PAYMENTS_ACTION_KEY,
    )

    if selected_action == "create_transfer_vouchers":
        supplier_name = st.selectbox(
            "Lieferant",
            options=list(TRANSFER_VOUCHER_SUPPLIER_OPTIONS.keys()),
            key=PAYMENTS_TRANSFER_VOUCHER_SUPPLIER_KEY,
        )
        preview_rows = []
        for row in voucher_candidate_rows:
            amount = parse_amount_value(row.get("amount")) or 0.0
            preview_rows.append(
                {
                    "transactionId": str(row.get("id", "")).strip(),
                    "Lieferant": supplier_name,
                    "Betrag": format_currency_value(abs(amount)),
                    "Richtung": "Einnahme" if amount > 0 else "Ausgabe",
                    "Belegbeschreibung": "Umbuchung",
                }
            )
        if preview_rows:
            st.dataframe(pd.DataFrame(preview_rows), width="stretch", hide_index=True)
        elif selected_rows:
            st.caption("Für die aktuelle Auswahl wurden bereits Geldtransfer-Belege erstellt.")
        else:
            st.caption("Wähle Zahlungen aus, um die Belegvorschau zu sehen.")
        if st.button(
            "Geldtransfer-Belege erstellen",
            width="stretch",
            type="primary",
            disabled=not voucher_candidate_rows,
        ):
            _run_create_transfer_vouchers(
                selected_rows=voucher_candidate_rows,
                supplier_name=supplier_name,
            )

    if selected_action in {"transfer_to_clearing", "move_bookings"}:
        selected_target_label = st.selectbox(
            "Zielkonto",
            options=list(target_options.keys()),
            key=PAYMENTS_TARGET_ACCOUNT_KEY,
            help="The currently selected source account is excluded from the target list.",
        )
        if selected_source_label:
            st.caption(f"`{selected_source_label}` ist hier ausgeblendet, weil es das Quellkonto ist.")

        selected_source_account_id = account_options[selected_source_label]
        selected_target_account_id = target_options[selected_target_label]
        source_account_name = str(
            account_rows_by_id.get(selected_source_account_id, {}).get("name", "")
        ).strip()
        target_account_type = str(
            account_rows_by_id.get(selected_target_account_id, {}).get("type", "")
        ).strip()
        action_label = PAYMENT_ACTION_LABELS[selected_action]
        transfer_function = (
            move_transaction_to_check_account
            if selected_action == "transfer_to_clearing"
            else move_transaction_to_check_account_old_logic
        )
        if st.button(action_label, width="stretch", type="primary", disabled=not selected_rows):
            _run_transfer_action(
                action_label=action_label,
                all_rows=rows,
                selected_rows=selected_rows,
                selected_source_account_id=selected_source_account_id,
                selected_target_account_id=selected_target_account_id,
                source_account_name=source_account_name,
                target_account_type=target_account_type,
                transfer_function=transfer_function,
            )

    _render_payment_results()
    _render_transfer_voucher_results()
