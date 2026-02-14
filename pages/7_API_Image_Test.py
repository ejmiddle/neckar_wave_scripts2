import json
import os
import uuid
from datetime import date, datetime

import pandas as pd
import streamlit as st

from src.logging_config import logger
from src.notion_access import (
    DEFAULT_ORDER_DB_TITLE,
    build_order_database_properties,
    create_order_database,
    insert_orders,
)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

def _resolve_image_extract_url() -> str:
    base_or_endpoint = os.getenv("API_BASE_URL", "http://localhost:8000").strip()
    if not base_or_endpoint:
        base_or_endpoint = "http://localhost:8000"

    if base_or_endpoint.endswith("/api/v1/images/extract"):
        return base_or_endpoint

    return f"{base_or_endpoint.rstrip('/')}/api/v1/images/extract"


API_URL = _resolve_image_extract_url()
REQUEST_TIMEOUT_SECONDS = 30
API_BEARER_TOKEN = os.getenv("API_BEARER_TOKEN", "").strip()
API_TLS_VERIFY_RAW = os.getenv("API_TLS_VERIFY", "true").strip()
DEFAULT_NOTION_PAGE_ID = "3014e28bdf9e802183d3efda2854f233"
# Fill this once the database exists, to skip re-creating it.
HARDCODED_NOTION_DATABASE_ID = "3014e28bdf9e812c93e7e970dd3146b1"

if API_TLS_VERIFY_RAW.lower() in {"false", "0", "no", "off"}:
    API_TLS_VERIFY: bool | str = False
else:
    API_TLS_VERIFY = API_TLS_VERIFY_RAW if API_TLS_VERIFY_RAW not in {"", "true", "1"} else True


st.title("🧪 API Image Test")
st.caption(
    "Bild hochladen, optional Eintragender setzen und Bestell-JSON pruefen."
)

eintragender = st.text_input(
    "Eintragender (optional)",
    help="Wird als Default fuer das Feld 'Eintragender' genutzt.",
)

uploaded_file = st.file_uploader(
    "Bild auswaehlen",
    type=["jpg", "jpeg", "png", "heic"],
    help="Das Bild wird an die konfigurierte API gesendet.",
)

if requests is None:
    logger.error("API Image Test: requests package is not available.")
    st.error("Das Paket 'requests' ist nicht verfuegbar. Bitte Umgebung pruefen.")

send_clicked = st.button("Bild an API senden", type="primary", disabled=uploaded_file is None)


def _validate_response_shape(payload: dict) -> list[str]:
    issues: list[str] = []
    expected_keys = ["request_id", "status", "columns", "rows"]
    for key in expected_keys:
        if key not in payload:
            issues.append(f"Feld fehlt: {key}")
    if "rows" in payload and not isinstance(payload["rows"], list):
        issues.append("Feld 'rows' sollte eine Liste sein.")
    if "columns" in payload and not isinstance(payload["columns"], list):
        issues.append("Feld 'columns' sollte eine Liste sein.")
    return issues


def _normalize_orders_for_json(orders: list[dict]) -> list[dict]:
    normalized = []
    for order in orders:
        entry = dict(order)
        datum_value = entry.get("Datum")
        if isinstance(datum_value, datetime):
            entry["Datum"] = datum_value.replace(microsecond=0).isoformat()
        elif isinstance(datum_value, date):
            entry["Datum"] = datetime.combine(datum_value, datetime.min.time()).isoformat()
        normalized.append(entry)
    return normalized


def _orders_to_editor_df(orders: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(orders)
    if "Datum" not in df.columns:
        df["Datum"] = pd.NaT
    df["Datum"] = df["Datum"].replace("", pd.NA)
    df["Datum"] = pd.to_datetime(df["Datum"], errors="coerce")
    return df


def _orders_to_rows(orders: list[dict], columns: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for order in orders:
        row: dict[str, str] = {}
        for column in columns:
            value = order.get(column, "")
            row[column] = "" if value is None else str(value)
        rows.append(row)
    return rows


if send_clicked and uploaded_file is not None and requests is not None:
    request_id = str(uuid.uuid4())
    metadata = {
        "source": "streamlit-test",
        "default_eintragender": eintragender.strip(),
    }

    files = {
        "image": (
            uploaded_file.name,
            uploaded_file.getvalue(),
            uploaded_file.type or "application/octet-stream",
        )
    }
    data = {"metadata": json.dumps(metadata)}

    st.info(f"Sende Request an: {API_URL}")
    logger.info(
        "API Image Test: sending image request request_id=%s filename=%s endpoint=%s",
        request_id,
        uploaded_file.name,
        API_URL,
    )
    headers = {"X-Request-Id": request_id}
    if API_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {API_BEARER_TOKEN}"

    with st.spinner("API-Aufruf laeuft..."):
        try:
            response = requests.post(
                API_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=REQUEST_TIMEOUT_SECONDS,
                verify=API_TLS_VERIFY,
            )
        except requests.RequestException as exc:
            logger.exception("API Image Test: request failed request_id=%s", request_id)
            st.error(f"Request fehlgeschlagen: {exc}")
            st.stop()

    st.write(f"HTTP Status: `{response.status_code}`")
    if response.status_code >= 400:
        logger.error(
            "API Image Test: backend returned error status request_id=%s status=%s body=%s",
            request_id,
            response.status_code,
            response.text[:1500],
        )

    try:
        response_json = response.json()
    except ValueError:
        logger.error(
            "API Image Test: response is not JSON request_id=%s status=%s body=%s",
            request_id,
            response.status_code,
            response.text[:1500],
        )
        st.warning("Antwort ist kein JSON. Zeige Rohtext.")
        st.code(response.text[:5000])
        st.stop()

    if isinstance(response_json, dict) and response.status_code < 400:
        st.session_state["api_image_response"] = response_json
        validation_issues = _validate_response_shape(response_json)
        if validation_issues:
            logger.warning(
                "API Image Test: schema validation issues request_id=%s issues=%s",
                request_id,
                validation_issues,
            )
            st.warning("Schema-Check (MVP) hat Hinweise:")
            for issue in validation_issues:
                st.write(f"- {issue}")
        else:
            st.success("Schema-Check (MVP) erfolgreich.")

        rows = response_json.get("rows")
        if isinstance(rows, list) and rows:
            st.subheader("Rows Vorschau")
            st.dataframe(rows, width="stretch")
    elif isinstance(response_json, dict):
        st.warning("API hat eine Fehlerantwort zurueckgegeben.")

current_response = st.session_state.get("api_image_response")

if isinstance(current_response, dict):
    if st.button("Aktuelle API-Antwort loeschen"):
        st.session_state.pop("api_image_response", None)
        st.session_state.pop("api_orders_editor", None)
        st.rerun()

    with st.expander("JSON Antwort", expanded=False):
        st.json(current_response)

    current_orders = None
    current_orders_payload = current_response.get("orders")
    if isinstance(current_orders_payload, list) and current_orders_payload:
        st.subheader("✏️ Orders bearbeiten")
        edited_orders = st.data_editor(
            _orders_to_editor_df(current_orders_payload),
            num_rows="dynamic",
            width="stretch",
            key="api_orders_editor",
        )
        current_orders = edited_orders.to_dict(orient="records")

        if st.button("Änderungen übernehmen"):
            normalized_orders = _normalize_orders_for_json(current_orders)
            st.session_state["api_image_response"]["orders"] = normalized_orders

            columns = st.session_state["api_image_response"].get("columns", [])
            if isinstance(columns, list) and columns:
                st.session_state["api_image_response"]["rows"] = _orders_to_rows(
                    normalized_orders, columns
                )
            st.success("Änderungen gespeichert.")

    if current_orders is None:
        current_orders = (
            current_orders_payload if isinstance(current_orders_payload, list) else []
        )
    current_json = dict(st.session_state["api_image_response"])
    current_json["orders"] = _normalize_orders_for_json(current_orders)

    dev_mode = st.checkbox(
        "Dev mode",
        value=st.session_state.get("api_test_dev_mode", False),
        help="Aktiviert das Erstellen einer neuen Notion-Datenbank.",
    )
    st.session_state["api_test_dev_mode"] = dev_mode

    if dev_mode:
        st.caption("Development: neue Notion-Datenbank erstellen oder in bestehende schreiben.")
        notion_page_id = st.text_input(
            "Notion Page ID (fuer neue Datenbank)",
            value=st.session_state.get("api_test_notion_page_id", DEFAULT_NOTION_PAGE_ID),
            help="Die Seite, auf der die neue Datenbank erstellt werden soll.",
        )
        st.session_state["api_test_notion_page_id"] = notion_page_id
    else:
        st.caption("Operations: Bestellungen direkt in die konfigurierte Notion-Datenbank speichern.")

    notion_db_id = (
        st.session_state.get("api_test_notion_db_id", "").strip()
        or HARDCODED_NOTION_DATABASE_ID
    )
    st.session_state["api_test_notion_db_id"] = notion_db_id
    if notion_db_id:
        st.caption(f"Aktive Notion Database ID: `{notion_db_id}`")

    if dev_mode:
        default_title = f"{DEFAULT_ORDER_DB_TITLE} {date.today().strftime('%d.%m.%Y')}"
        db_title = st.text_input(
            "Neuer Datenbank-Titel",
            value=st.session_state.get("api_test_notion_db_title", default_title),
        )
        st.session_state["api_test_notion_db_title"] = db_title

        if st.button("Datenbank erstellen"):
            if not notion_page_id.strip():
                st.error("Bitte eine Notion Page ID angeben.")
            else:
                with st.spinner("Erstelle Notion-Datenbank..."):
                    try:
                        created = create_order_database(
                            page_id=notion_page_id.strip(),
                            title=db_title.strip() or DEFAULT_ORDER_DB_TITLE,
                            properties=build_order_database_properties(),
                        )
                        created_id = created.get("id")
                        st.session_state["api_test_notion_db_id"] = created_id or ""
                        st.success(f"Datenbank erstellt: {created_id}")
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Notion-Datenbank-Erstellung fehlgeschlagen.")
                        st.error(f"Datenbank-Erstellung fehlgeschlagen: {exc}")

    if st.button("Bestellungen in Notion speichern"):
        orders = current_json.get("orders", [])
        if not orders:
            st.warning("Keine Bestellungen gefunden.")
        elif not notion_db_id:
            st.error("Keine Notion-Datenbank-ID konfiguriert. Bitte Dev mode nutzen oder HARDCODED_NOTION_DATABASE_ID setzen.")
        else:
            with st.spinner("Schreibe Bestellungen nach Notion..."):
                try:
                    count = insert_orders(notion_db_id, orders)
                    st.success(f"{count} Bestellungen gespeichert.")
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Notion-Export fehlgeschlagen.")
                    st.error(f"Notion-Export fehlgeschlagen: {exc}")
