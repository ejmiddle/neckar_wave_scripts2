from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

import streamlit as st


EMPTY_STATUS_FILTER_LABEL = "(No status)"


def sync_multiselect_options(selection_key: str, options_key: str, options: list[str]) -> None:
    previous_options = st.session_state.get(options_key)
    current_selection = st.session_state.get(selection_key, [])

    if not options:
        st.session_state[options_key] = []
        st.session_state[selection_key] = []
        return

    if selection_key not in st.session_state:
        st.session_state[options_key] = options
        st.session_state[selection_key] = options
        return

    filtered_selection = [option for option in current_selection if option in options]
    if previous_options != options:
        st.session_state[options_key] = options
        st.session_state[selection_key] = filtered_selection or options
        return

    if filtered_selection != current_selection:
        st.session_state[selection_key] = filtered_selection


def default_status_filter_label(status: str, *, empty_label: str = EMPTY_STATUS_FILTER_LABEL) -> str:
    return status or empty_label


def build_status_filter_options(
    rows: list[dict[str, Any]],
    *,
    status_getter: Callable[[dict[str, Any]], str],
    label_formatter: Callable[[str], str] | None = None,
) -> dict[str, str]:
    format_label = label_formatter or default_status_filter_label
    status_values = sorted({status_getter(row) for row in rows})
    return {format_label(status): status for status in status_values}


def selected_option_values(
    selection_labels: list[str],
    option_mapping: dict[str, str],
) -> set[str]:
    return {option_mapping[label] for label in selection_labels if label in option_mapping}


def matches_text_query(query: str, values: Iterable[Any]) -> bool:
    normalized_query = str(query).strip().casefold()
    if not normalized_query:
        return True

    for value in values:
        normalized_value = str(value or "").strip()
        if normalized_value and normalized_query in normalized_value.casefold():
            return True
    return False


def is_within_date_range(
    row_date: date | None,
    *,
    start_date: Any,
    end_date: Any,
) -> bool:
    if isinstance(start_date, date) and (row_date is None or row_date < start_date):
        return False
    if isinstance(end_date, date) and (row_date is None or row_date > end_date):
        return False
    return True


def validate_date_range(
    start_date: Any,
    end_date: Any,
    *,
    start_label: str,
    end_label: str,
) -> bool:
    if isinstance(start_date, date) and isinstance(end_date, date) and start_date > end_date:
        st.error(f"`{start_label}` darf nicht nach `{end_label}` liegen.")
        return False
    return True
