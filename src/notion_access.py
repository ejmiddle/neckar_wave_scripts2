import os
from datetime import UTC, date, datetime
from typing import Any, Dict, Iterable, List

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DATABASE_ID = "1ea4e28bdf9e8074ba94e2c410731c50"
DEFAULT_DATE_PROPERTY = os.getenv("NOTION_DATE_PROPERTY", "Date")
DEFAULT_ORDER_DB_TITLE = os.getenv("NOTION_ORDER_DB_TITLE", "Bestellungen")


class NotionRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int, body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def notion_request(
    method: str,
    path: str,
    token: str,
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    base_url = "https://api.notion.com/v1"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    url = f"{base_url}{path}"
    resp = requests.request(method, url, headers=headers, json=payload, timeout=30)
    if not resp.ok:
        raise NotionRequestError(
            f"{method} {path} failed: {resp.status_code} {resp.text}",
            resp.status_code,
            resp.text,
        )
    return resp.json()


def iter_database_rows(
    token: str,
    database_id: str,
    date_property: str,
    start_date: date,
) -> Iterable[Dict[str, Any]]:
    payload: Dict[str, Any] = {
        "page_size": 100,
        "filter": {
            "property": date_property,
            "date": {"on_or_after": start_date.isoformat()},
        },
    }
    while True:
        data = notion_request("POST", f"/databases/{database_id}/query", token, payload)
        for row in data.get("results", []):
            yield row
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")


def extract_plain_text(value: Any) -> str:
    if isinstance(value, list):
        parts = []
        for item in value:
            text = item.get("plain_text")
            if text:
                parts.append(text)
        return "".join(parts)
    if isinstance(value, dict):
        return value.get("plain_text") or value.get("name") or ""
    if value is None:
        return ""
    return str(value)


def flatten_properties(props: Dict[str, Any]) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for key, prop in props.items():
        ptype = prop.get("type")
        value = prop.get(ptype)
        if ptype in {"title", "rich_text"}:
            flat[key] = extract_plain_text(value)
        elif ptype == "select":
            flat[key] = value.get("name") if isinstance(value, dict) else None
        elif ptype == "multi_select":
            flat[key] = [v.get("name") for v in value or []]
        elif ptype == "people":
            flat[key] = [v.get("name") or v.get("id") for v in value or []]
        elif ptype in {"email", "phone_number", "url"}:
            flat[key] = value
        elif ptype == "number":
            flat[key] = value
        elif ptype == "checkbox":
            flat[key] = value
        elif ptype == "date":
            flat[key] = format_notion_date(value)
        elif ptype == "status":
            flat[key] = value.get("name") if isinstance(value, dict) else None
        elif ptype == "relation":
            flat[key] = [v.get("id") for v in value or []]
        elif ptype == "files":
            flat[key] = [v.get("name") for v in value or []]
        elif ptype == "formula":
            flat[key] = value
        elif ptype == "rollup":
            flat[key] = value
        else:
            flat[key] = value
    return flat


def format_notion_date(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    start = value.get("start")
    if not start:
        return None
    parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return parsed.strftime("%d.%m.%Y %H:%M")


def get_notion_orders_from_today(
    database_id: str | None = None,
    date_property: str | None = None,
    start_date: date | None = None,
) -> pd.DataFrame:
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise RuntimeError("Missing NOTION_TOKEN in environment or .env")

    database_id = database_id or os.getenv("NOTION_DATABASE_ID") or DEFAULT_DATABASE_ID
    date_property = date_property or DEFAULT_DATE_PROPERTY
    if not date_property:
        raise RuntimeError(
            "Missing Notion date property name. Provide date_property or set NOTION_DATE_PROPERTY."
        )

    start_date = start_date or datetime.now(UTC).date()

    rows: List[Dict[str, Any]] = []
    for row in iter_database_rows(token, database_id, date_property, start_date):
        props = row.get("properties", {})
        rows.append(flatten_properties(props))

    return pd.DataFrame(rows)


def build_order_database_properties() -> Dict[str, Any]:
    return {
        "Produkt": {"title": {}},
        "Menge": {"number": {"format": "number"}},
        "Datum": {"date": {}},
        "Notiz/Kunde": {"rich_text": {}},
        "Abgeholt": {
            "select": {
                "options": [
                    {"name": "Nein"},
                    {"name": "Ja"},
                ]
            }
        },
        "Eintragender": {"rich_text": {}},
        "Wohin": {
            "select": {
                "options": [
                    {"name": "Wieblingen"},
                    {"name": "Roest"},
                ]
            }
        },
        "Zahlung": {
            "select": {
                "options": [
                    {"name": "Vor Ort"},
                    {"name": "Online"},
                    {"name": "Per Rechnung"},
                    {"name": "Schon bezahlt"},
                    {"name": "Unklar"},
                ]
            }
        },
    }


def create_order_database(
    page_id: str,
    title: str | None = None,
    properties: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise RuntimeError("Missing NOTION_TOKEN in environment or .env")
    payload = {
        "parent": {"page_id": page_id},
        "title": [{"type": "text", "text": {"content": title or DEFAULT_ORDER_DB_TITLE}}],
        "properties": properties or build_order_database_properties(),
    }
    return notion_request("POST", "/databases", token, payload)


def _normalize_order_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time()).isoformat()
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        normalized = cleaned.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.replace(microsecond=0).isoformat()
        except ValueError:
            pass
        try:
            parsed = datetime.strptime(cleaned, "%d.%m.%Y %H:%M")
            return parsed.replace(microsecond=0).isoformat()
        except ValueError:
            pass
        try:
            parsed = datetime.strptime(cleaned, "%d.%m.%Y")
            return parsed.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        except ValueError:
            return None
    return None


def _text_prop(value: Any) -> Dict[str, Any] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


def _title_prop(value: Any) -> Dict[str, Any]:
    text = str(value or "").strip() or "Unbenannt"
    return {"title": [{"type": "text", "text": {"content": text}}]}


def _select_prop(value: Any) -> Dict[str, Any] | None:
    if value is None:
        return None
    name = str(value).strip()
    if not name:
        return None
    return {"select": {"name": name}}


def insert_orders(
    database_id: str,
    orders: Iterable[Dict[str, Any]],
) -> int:
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise RuntimeError("Missing NOTION_TOKEN in environment or .env")
    count = 0
    for order in orders:
        properties: Dict[str, Any] = {
            "Produkt": _title_prop(order.get("Produkt")),
        }
        if order.get("Menge") is not None:
            properties["Menge"] = {"number": order.get("Menge")}

        date_value = _normalize_order_date(order.get("Datum"))
        if date_value:
            properties["Datum"] = {"date": {"start": date_value}}

        text_prop = _text_prop(order.get("Notiz/Kunde"))
        if text_prop:
            properties["Notiz/Kunde"] = text_prop

        select_prop = _select_prop(order.get("Abgeholt"))
        if select_prop:
            properties["Abgeholt"] = select_prop

        text_prop = _text_prop(order.get("Eintragender"))
        if text_prop:
            properties["Eintragender"] = text_prop

        select_prop = _select_prop(order.get("Wohin"))
        if select_prop:
            properties["Wohin"] = select_prop

        select_prop = _select_prop(order.get("Zahlung"))
        if select_prop:
            properties["Zahlung"] = select_prop

        payload = {"parent": {"database_id": database_id}, "properties": properties}
        notion_request("POST", "/pages", token, payload)
        count += 1
    return count
