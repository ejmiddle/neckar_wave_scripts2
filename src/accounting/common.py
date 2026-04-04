import json
import os
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import streamlit as st

from src.accounting.state import SEVDESK_CACHE_DIR
from src.logging_config import logger
from src.sevdesk.api import read_token
from src.sevdesk.constants import DEFAULT_BASE_URL


def base_url() -> str:
    return os.getenv("SEVDESK_BASE_URL") or DEFAULT_BASE_URL


def report_error(
    user_message: str,
    *,
    log_message: str | None = None,
    exc_info: bool = False,
) -> None:
    message = log_message or user_message
    if exc_info:
        logger.exception(message)
    else:
        logger.error(message)
    st.error(user_message)


def ensure_token() -> str | None:
    token = read_token()
    if token:
        return token
    report_error("No sevDesk API token found. Set `SEVDESK_KEY` in `.env`.")
    return None


def flag_as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip() == "1"


def parse_transaction_date(row: dict[str, Any]) -> date | None:
    for key in ("valueDate", "entryDate"):
        raw_value = row.get(key)
        if not raw_value:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw_value))
            return parsed.date()
        except ValueError:
            continue
    return None


def filter_rows_by_date_range(
    rows: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        booking_date = parse_transaction_date(row)
        if booking_date is None:
            continue
        if start_date <= booking_date <= end_date:
            filtered_rows.append(row)
    return filtered_rows


def find_check_account_by_name(
    rows: list[dict[str, Any]],
    name_fragment: str,
) -> dict[str, Any] | None:
    wanted = name_fragment.strip().lower()
    for row in rows:
        name = str(row.get("name", "")).strip().lower()
        if wanted and wanted in name:
            return row
    return None


def parse_amount_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    cleaned = str(value).strip()
    if not cleaned:
        return None
    normalized = re.sub(r"[^\d,.\-]", "", cleaned)
    if not normalized:
        return None
    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif normalized.count(",") == 1:
        normalized = normalized.replace(",", ".")
    try:
        return round(float(normalized), 2)
    except ValueError:
        return None


def format_currency_value(value: Any) -> str:
    amount = parse_amount_value(value)
    if amount is None:
        return "-"
    return f"{amount:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


def format_sevdesk_date(value: Any) -> str:
    parsed_date = value if isinstance(value, date) else parse_iso_date(value)
    if parsed_date is None:
        parsed_date = date.today()
    return parsed_date.strftime("%d.%m.%Y")


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def normalize_compare_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def compare_document_values(left: Any, right: Any) -> bool | None:
    left_token = normalize_compare_token(left)
    right_token = normalize_compare_token(right)
    if not left_token or not right_token:
        return None
    return left_token in right_token or right_token in left_token


def compare_amounts(booking_amount: Any, extracted_amount: Any) -> bool | None:
    booking_value = parse_amount_value(booking_amount)
    extracted_value = parse_amount_value(extracted_amount)
    if booking_value is None or extracted_value is None:
        return None
    return abs(abs(booking_value) - abs(extracted_value)) <= 0.01


def compare_dates(booking_value: Any, extracted_value: Any) -> bool | None:
    booking_date = booking_value if isinstance(booking_value, date) else parse_iso_date(booking_value)
    extracted_date = parse_iso_date(extracted_value)
    if booking_date is None or extracted_date is None:
        return None
    return booking_date == extracted_date


def compare_booking_after_receipt_window(
    booking_value: Any,
    extracted_value: Any,
    max_delay_days: int,
) -> bool | None:
    booking_date = booking_value if isinstance(booking_value, date) else parse_iso_date(booking_value)
    receipt_date = parse_iso_date(extracted_value)
    if booking_date is None or receipt_date is None:
        return None
    delta_days = (booking_date - receipt_date).days
    return 0 <= delta_days <= max_delay_days


def format_match_value(value: bool | None) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "-"


def format_bool_value(value: Any) -> str:
    if value is True:
        return "Ja"
    if value is False:
        return "Nein"
    return "-"


def safe_filename_token(value: Any) -> str:
    token = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip())
    token = token.strip("._-")
    return token or "unknown"


def load_json_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def cache_json_payload(name: str, payload: Any) -> Path:
    SEVDESK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{timestamp}_{safe_filename_token(name)}.json"
    path = SEVDESK_CACHE_DIR / filename
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path
