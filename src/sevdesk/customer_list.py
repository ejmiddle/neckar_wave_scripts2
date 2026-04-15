from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.sevdesk.constants import RECHNUNGEN_CUSTOMERS_PATH

DEFAULT_RECHNUNGEN_CUSTOMERS = (
    "Fair & Quer Naturkost/Naturwaren GmbH",
    "abc",
)


def _normalize_customer_name(name: Any) -> str:
    return str(name or "").strip()


def _casefold_name(name: str) -> str:
    return name.strip().casefold()


def _load_customer_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def _extract_customer_names(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []

    candidate_keys = ("customers", "kunden", "daten")
    for key in candidate_keys:
        raw_rows = payload.get(key)
        if not isinstance(raw_rows, list):
            continue

        names: list[str] = []
        seen: set[str] = set()
        for item in raw_rows:
            if isinstance(item, str):
                customer_name = _normalize_customer_name(item)
            elif isinstance(item, dict):
                customer_name = _normalize_customer_name(
                    item.get("name")
                    or item.get("customerName")
                    or item.get("customer")
                    or item.get("label")
                )
            else:
                customer_name = ""

            if not customer_name:
                continue

            key_value = _casefold_name(customer_name)
            if key_value in seen:
                continue

            seen.add(key_value)
            names.append(customer_name)

        if names:
            return names

    return []


def _dedupe_customer_names(names: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        normalized = _normalize_customer_name(name)
        if not normalized:
            continue
        key = _casefold_name(normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def save_rechnungen_customer_names(
    names: list[str],
    *,
    path: Path = RECHNUNGEN_CUSTOMERS_PATH,
) -> list[str]:
    customer_names = _dedupe_customer_names(names)
    payload = {
        "informationsart": "rechnungen_customers",
        "quelle": "local",
        "quelle_datei": str(path),
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "anzahl": len(customer_names),
        "customers": customer_names,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return customer_names


def load_rechnungen_customer_names(
    *,
    path: Path = RECHNUNGEN_CUSTOMERS_PATH,
) -> list[str]:
    payload = _load_customer_payload(path)
    customer_names = _extract_customer_names(payload)
    if customer_names:
        return customer_names
    return save_rechnungen_customer_names(list(DEFAULT_RECHNUNGEN_CUSTOMERS), path=path)


def add_rechnungen_customer_name(
    customer_name: str,
    *,
    path: Path = RECHNUNGEN_CUSTOMERS_PATH,
) -> list[str]:
    normalized_customer_name = _normalize_customer_name(customer_name)
    if not normalized_customer_name:
        return load_rechnungen_customer_names(path=path)

    customer_names = load_rechnungen_customer_names(path=path)
    if _casefold_name(normalized_customer_name) in {_casefold_name(name) for name in customer_names}:
        return customer_names

    customer_names.append(normalized_customer_name)
    return save_rechnungen_customer_names(customer_names, path=path)


def remove_rechnungen_customer_name(
    customer_name: str,
    *,
    path: Path = RECHNUNGEN_CUSTOMERS_PATH,
) -> list[str]:
    normalized_customer_name = _normalize_customer_name(customer_name)
    if not normalized_customer_name:
        return load_rechnungen_customer_names(path=path)

    customer_names = load_rechnungen_customer_names(path=path)
    filtered_customer_names = [
        name
        for name in customer_names
        if _casefold_name(name) != _casefold_name(normalized_customer_name)
    ]
    if len(filtered_customer_names) == len(customer_names):
        return customer_names

    return save_rechnungen_customer_names(filtered_customer_names, path=path)
