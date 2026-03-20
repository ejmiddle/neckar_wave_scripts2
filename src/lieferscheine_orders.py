from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd


def _normalize_date_to_iso_day(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    normalized = cleaned.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError:
        pass

    datetime_formats = (
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d.%m.%y %H:%M",
    )
    for fmt in datetime_formats:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue

    date_formats = (
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%d.%m.%y",
    )
    for fmt in date_formats:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue

    return cleaned


def normalize_orders_for_json(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for order in orders:
        entry = dict(order)
        datum_value = entry.get("date", entry.get("Datum"))
        entry["date"] = _normalize_date_to_iso_day(datum_value)
        entry.pop("Datum", None)
        normalized.append(entry)
    return normalized


def orders_to_editor_df(orders: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(orders)
    if "folder" not in df.columns:
        df["folder"] = ""
    if "date" not in df.columns:
        df["date"] = pd.NaT
    if "customer" not in df.columns:
        df["customer"] = ""
    if "product" not in df.columns:
        df["product"] = ""
    if "no_items" not in df.columns:
        df["no_items"] = 1
    df = df[["folder", "customer", "product", "no_items", "date"]]
    df["date"] = df["date"].replace("", pd.NA)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


def to_orders_payload(raw_orders: list[dict[str, Any]], folder: str = "") -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for row in raw_orders:
        if not isinstance(row, dict):
            continue
        no_items = row.get("no_items", 1)
        try:
            no_items_value = int(str(no_items).strip())
        except (TypeError, ValueError):
            no_items_value = 1
        converted.append(
            {
                "customer": row.get("customer", ""),
                "product": row.get("product", ""),
                "no_items": max(1, no_items_value),
                "date": row.get("date"),
                "folder": folder or row.get("folder", ""),
            }
        )
    return converted
