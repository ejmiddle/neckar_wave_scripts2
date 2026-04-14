from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from io import BytesIO
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


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _normalize_int(value: Any, *, default: int = 0) -> int:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _normalize_summary_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "folder": _normalize_text(row.get("folder")),
        "product": _normalize_text(row.get("product")),
        "total_no_items": _normalize_int(row.get("total_no_items")),
        "positions": _normalize_int(row.get("positions")),
    }


def extract_lieferscheine_folder_jsons(
    workbook_bytes: bytes,
    *,
    source_name: str | None = None,
) -> list[dict[str, Any]]:
    try:
        summary_sheet = pd.read_excel(
            BytesIO(workbook_bytes),
            sheet_name="summary_folder_product",
            engine="openpyxl",
        )
    except ValueError as exc:
        raise ValueError("Workbook must contain a 'summary_folder_product' sheet.") from exc

    summary_by_folder: dict[str, list[dict[str, Any]]] = defaultdict(list)
    folder_order: list[str] = []
    seen_folders: set[str] = set()

    def register_folder(folder: str) -> None:
        folder_name = _normalize_text(folder)
        if not folder_name or folder_name in seen_folders:
            return
        seen_folders.add(folder_name)
        folder_order.append(folder_name)

    normalized_summary = [
        _normalize_summary_row(row)
        for row in summary_sheet.to_dict(orient="records")
        if isinstance(row, dict)
    ]

    for summary_row in normalized_summary:
        folder_name = _normalize_text(summary_row.get("folder"))
        register_folder(folder_name)
        if folder_name:
            summary_by_folder[folder_name].append(summary_row)

    folder_jsons: list[dict[str, Any]] = []
    for folder_name in folder_order:
        folder_summary = summary_by_folder.get(folder_name, [])
        total_no_items = sum(
            _normalize_int(row.get("total_no_items"), default=0) for row in folder_summary
        )
        total_positions = sum(
            _normalize_int(row.get("positions"), default=0) for row in folder_summary
        )
        products = sorted(
            {
                _normalize_text(row.get("product"))
                for row in folder_summary
                if _normalize_text(row.get("product"))
            }
        )
        folder_jsons.append(
            {
                "folder": folder_name,
                "summary_folder_product": folder_summary,
                "metadata": {
                    "source_name": _normalize_text(source_name) or None,
                    "summary_rows_count": len(folder_summary),
                    "products": products,
                    "total_no_items": total_no_items,
                    "positions": total_positions,
                },
            }
        )

    return folder_jsons
