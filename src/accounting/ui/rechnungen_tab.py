import difflib
import re
import unicodedata
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

import streamlit as st

from src.accounting.common import base_url, ensure_token, parse_iso_date, report_error
from src.accounting.master_data import load_stored_products
from src.accounting.ui.displays import show_invoices
from src.accounting.ui.filter_utils import (
    build_status_filter_options,
    is_within_date_range,
    matches_text_query,
    selected_option_values,
    sync_multiselect_options,
    validate_date_range,
)
from src.lieferscheine_orders import extract_lieferscheine_folder_jsons
from src.logging_config import logger
from src.sevdesk.api import (
    request_invoice_by_id,
    request_invoice_positions,
    request_invoices,
    save_invoice,
)
from src.sevdesk.customer_list import (
    RECHNUNGEN_CUSTOMERS_PATH,
    add_rechnungen_customer_name,
    load_rechnungen_customer_names,
)

RECHNUNGEN_ROWS_KEY = "sevdesk_rechnungen_rows"
RECHNUNGEN_LOAD_LIMIT_KEY = "sevdesk_rechnungen_load_limit"
RECHNUNGEN_STATUS_FILTER_KEY = "sevdesk_rechnungen_status_filter"
RECHNUNGEN_STATUS_FILTER_OPTIONS_KEY = "sevdesk_rechnungen_status_filter_options"
RECHNUNGEN_TEXT_QUERY_KEY = "sevdesk_rechnungen_text_query"
RECHNUNGEN_START_DATE_KEY = "sevdesk_rechnungen_start_date"
RECHNUNGEN_END_DATE_KEY = "sevdesk_rechnungen_end_date"
RECHNUNGEN_CUSTOMER_KEY = "sevdesk_rechnungen_customer"
RECHNUNGEN_DRAFT_INVOICE_ID_KEY = "sevdesk_rechnungen_draft_invoice_id"
RECHNUNGEN_EDITOR_INVOICE_ID_KEY = "sevdesk_rechnungen_editor_invoice_id"
RECHNUNGEN_EDITOR_INVOICE_KEY = "sevdesk_rechnungen_editor_invoice"
RECHNUNGEN_EDITOR_POSITIONS_KEY = "sevdesk_rechnungen_editor_positions"
RECHNUNGEN_EDITOR_DELETED_POSITION_IDS_KEY = "sevdesk_rechnungen_editor_deleted_position_ids"
RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY = "sevdesk_rechnungen_editor_new_positions"
RECHNUNGEN_EDITOR_NEW_POSITION_COUNTER_KEY = "sevdesk_rechnungen_editor_new_position_counter"
RECHNUNGEN_LIEFERSCHEINE_JSONS_KEY = "sevdesk_rechnungen_lieferscheine_folder_jsons"
RECHNUNGEN_EDITOR_POPULATION_STATE_KEY = "sevdesk_rechnungen_editor_population_state"
RECHNUNGEN_CUSTOMER_NEW_NAME_KEY = "sevdesk_rechnungen_customer_new_name"
RECHNUNGEN_DEFAULT_LOAD_LIMIT = 100
RECHNUNGEN_FOLDER_MATCH_MIN_SCORE = 0.45
RECHNUNGEN_PRODUCT_MATCH_MIN_SCORE = 0.7
RECHNUNGEN_LARGE_SIZE_TOKENS = {"l", "large", "gross", "groß"}
RECHNUNGEN_SMALL_SIZE_TOKENS = {"s", "small", "klein"}


def _invoice_status_value(row: dict) -> str:
    return str(row.get("status", "")).strip()


def _contact_display_name(value: object) -> str:
    if not isinstance(value, dict):
        return ""

    organization_name = str(value.get("name", "")).strip()
    if organization_name:
        return organization_name

    person_name = " ".join(
        part
        for part in (
            str(value.get("surename", "")).strip(),
            str(value.get("familyname", "")).strip(),
        )
        if part
    ).strip()
    if person_name:
        return person_name

    return str(value.get("customerNumber", "")).strip()


def _invoice_text_matches(row: dict, query: str) -> bool:
    return matches_text_query(
        query,
        [
            row.get("id"),
            row.get("invoiceNumber"),
            row.get("number"),
            row.get("description"),
            row.get("header"),
            row.get("headText"),
            row.get("customerInternalNote"),
            row.get("name"),
            row.get("customerName"),
            row.get("contactName"),
            row.get("supplierName"),
            row.get("invoiceType"),
            row.get("invoiceDate"),
            row.get("status"),
            _contact_display_name(row.get("contact")),
            _contact_display_name(row.get("customer")),
            _contact_display_name(row.get("supplier")),
        ],
    )


def _invoice_date(row: dict) -> date | None:
    for key in ("invoiceDate", "voucherDate", "create", "update"):
        parsed = parse_iso_date(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _invoice_customer_name(row: dict[str, Any]) -> str:
    customer_name = str(
        row.get("addressName")
        or row.get("customerName")
        or row.get("contactName")
        or _contact_display_name(row.get("contact"))
        or _contact_display_name(row.get("customer"))
        or ""
    ).strip()
    return customer_name


def _invoice_sort_key(row: dict[str, Any]) -> tuple[str, int]:
    row_date = _invoice_date(row)
    item_id = str(row.get("id", "")).strip()
    try:
        numeric_id = int(item_id)
    except ValueError:
        numeric_id = 0
    return ((row_date.isoformat() if row_date else ""), numeric_id)


def _draft_invoice_label(row: dict[str, Any]) -> str:
    invoice_number = str(row.get("invoiceNumber") or row.get("id") or "-").strip()
    invoice_date = str(row.get("invoiceDate") or row.get("create") or "-").strip()
    amount = str(row.get("sumGross") or row.get("totalGross") or "-").strip()
    return f"{invoice_number} | {invoice_date} | {amount} EUR | {row.get('id')}"


def _product_label(row: dict[str, Any]) -> str:
    article_number = str(row.get("articleNumber", "")).strip()
    name = str(row.get("name", "")).strip() or str(row.get("description", "")).strip()
    if article_number and name:
        return f"{article_number} | {name}"
    return article_number or name or str(row.get("id", "")).strip()


def _normalize_match_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).strip()


def _match_score(query: Any, candidate: Any) -> float:
    normalized_query = _normalize_match_text(query)
    normalized_candidate = _normalize_match_text(candidate)
    if not normalized_query or not normalized_candidate:
        return 0.0
    if normalized_query == normalized_candidate:
        return 1.0

    score = difflib.SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
    if normalized_query in normalized_candidate or normalized_candidate in normalized_query:
        score += 0.2

    query_tokens = set(normalized_query.split())
    candidate_tokens = set(normalized_candidate.split())
    if query_tokens and candidate_tokens:
        overlap = len(query_tokens & candidate_tokens) / max(len(query_tokens), len(candidate_tokens))
        score = max(score, 0.35 + (0.65 * overlap))
    return min(score, 1.0)


def _match_tokens(value: Any) -> set[str]:
    normalized = _normalize_match_text(value)
    if not normalized:
        return set()
    return set(normalized.split())


def _summary_product_family(value: Any) -> str | None:
    normalized = _normalize_match_text(value)
    if "classico" in normalized:
        return "classico"
    if "saaten" in normalized or "vollkorn" in normalized:
        return "saaten"
    return None


def _summary_product_size(value: Any) -> str | None:
    tokens = _match_tokens(value)
    if tokens & RECHNUNGEN_LARGE_SIZE_TOKENS:
        return "large"
    if tokens & RECHNUNGEN_SMALL_SIZE_TOKENS:
        return "small"
    return None


def _candidate_matches_family(candidate: Any, family: str | None) -> bool:
    if family is None:
        return True
    normalized = _normalize_match_text(candidate)
    if family == "classico":
        return "classico" in normalized
    if family == "saaten":
        return "saaten" in normalized or "vollkorn" in normalized
    return True


def _candidate_size(value: Any) -> str | None:
    tokens = _match_tokens(value)
    if "klein" in tokens or "small" in tokens:
        return "small"
    if "gross" in tokens or "large" in tokens:
        return "large"
    return None


def _product_match_candidates(row: dict[str, Any]) -> list[str]:
    return [
        _normalize_match_text(row.get("name")),
        _normalize_match_text(row.get("articleNumber")),
        _normalize_match_text(row.get("description")),
        _normalize_match_text(_product_label(row)),
    ]


def _suggest_product_label(
    position: dict[str, Any],
    products: list[dict[str, Any]],
) -> tuple[str | None, float]:
    query = _normalize_match_text(
        " ".join(
            [
                str(position.get("name", "")),
                str(position.get("text", "")),
                str(position.get("description", "")),
            ]
        )
    )
    if not query:
        return None, 0.0

    best_label: str | None = None
    best_score = 0.0
    for product in products:
        label = _product_label(product)
        candidate_score = 0.0
        for candidate in _product_match_candidates(product):
            if not candidate:
                continue
            candidate_score = max(candidate_score, _match_score(query, candidate))
        if candidate_score > best_score:
            best_label = label
            best_score = candidate_score
    return best_label, best_score


def _best_matching_folder_payload(
    customer_name: str,
    folder_jsons: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, float]:
    if not customer_name or not isinstance(folder_jsons, list):
        return None, 0.0

    best_payload: dict[str, Any] | None = None
    best_score = 0.0
    for folder_payload in folder_jsons:
        if not isinstance(folder_payload, dict):
            continue
        folder_name = str(folder_payload.get("folder", "")).strip()
        if not folder_name:
            continue
        score = _match_score(customer_name, folder_name)
        if score > best_score:
            best_payload = folder_payload
            best_score = score
    if best_score < RECHNUNGEN_FOLDER_MATCH_MIN_SCORE:
        return None, best_score
    return best_payload, best_score


def _match_summary_product_to_product(
    summary_row: dict[str, Any],
    products: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None, float]:
    product_name = str(summary_row.get("product", "")).strip()
    if not product_name:
        return None, None, 0.0

    product_family = _summary_product_family(product_name)
    requested_size = _summary_product_size(product_name)
    if product_family in {"classico", "saaten"} and requested_size is None:
        requested_size = "small"

    best_product: dict[str, Any] | None = None
    best_label: str | None = None
    best_score = 0.0
    for product in products:
        label = _product_label(product)
        candidates = [candidate for candidate in _product_match_candidates(product) if candidate]
        if product_family and not any(_candidate_matches_family(candidate, product_family) for candidate in candidates):
            continue

        candidate_score = 0.0
        for candidate in candidates:
            score = _match_score(product_name, candidate)
            if product_family and _candidate_matches_family(candidate, product_family):
                score = min(score + 0.2, 1.0)
            candidate_size = _candidate_size(candidate)
            if requested_size is not None:
                if candidate_size == requested_size:
                    score = min(score + 0.25, 1.0)
                elif candidate_size is not None:
                    score = max(score - 0.25, 0.0)
            candidate_score = max(candidate_score, score)
        if candidate_score > best_score:
            best_product = product
            best_label = label
            best_score = candidate_score

    if best_score < RECHNUNGEN_PRODUCT_MATCH_MIN_SCORE:
        return None, None, best_score
    return best_product, best_label, best_score


def _build_folder_population_rows(
    folder_payload: dict[str, Any],
    products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matched_rows: list[dict[str, Any]] = []
    unmatched_rows: list[dict[str, Any]] = []
    summary_rows = folder_payload.get("summary_folder_product", [])
    if not isinstance(summary_rows, list):
        return matched_rows, unmatched_rows

    for summary_row in summary_rows:
        if not isinstance(summary_row, dict):
            continue
        matched_product, matched_label, score = _match_summary_product_to_product(summary_row, products)
        quantity = max(float(summary_row.get("total_no_items", 0) or 0), 0.0)
        row_payload = {
            "summary_row": summary_row,
            "quantity": quantity,
            "matched_product": matched_product,
            "matched_label": matched_label,
            "match_score": score,
        }
        if matched_product is None or matched_label is None:
            unmatched_rows.append(row_payload)
        else:
            matched_rows.append(row_payload)
    return matched_rows, unmatched_rows


def _clear_widget_state_for_invoice(invoice_id: str, base_positions: list[dict[str, Any]], *, new_position_count: int = 0) -> None:
    for index, position in enumerate(base_positions, start=1):
        position_id = str(position.get("id", "")).strip() or f"row-{index}"
        st.session_state.pop(f"sevdesk_rechnung_product_{invoice_id}_{position_id}", None)
        st.session_state.pop(f"sevdesk_rechnung_quantity_{invoice_id}_{position_id}", None)

    for row_id in range(1, new_position_count + 1):
        draft_key = f"{invoice_id}_{row_id}"
        st.session_state.pop(f"sevdesk_rechnung_new_product_{draft_key}", None)
        st.session_state.pop(f"sevdesk_rechnung_new_quantity_{draft_key}", None)


def _apply_folder_population_to_editor(
    *,
    invoice_id: str,
    base_positions: list[dict[str, Any]],
    matched_rows: list[dict[str, Any]],
) -> None:
    _clear_widget_state_for_invoice(
        invoice_id,
        base_positions,
        new_position_count=int(
            st.session_state.get(RECHNUNGEN_EDITOR_NEW_POSITION_COUNTER_KEY, 0)
        ),
    )
    population_state = {
        "invoice_id": invoice_id,
        "rows": [],
    }

    for index, row_payload in enumerate(matched_rows):
        matched_label = row_payload.get("matched_label")
        quantity = float(row_payload.get("quantity", 0.0) or 0.0)
        if not isinstance(matched_label, str) or not matched_label:
            continue

        population_state["rows"].append(
            {
                "row_id": str(index + 1),
                "selected_label": matched_label,
                "quantity": quantity,
            }
        )

    deleted_position_ids = sorted(
        {
            str(position.get("id", "")).strip()
            for position in base_positions
            if isinstance(position, dict) and str(position.get("id", "")).strip()
        }
    )
    new_row_count = len(population_state["rows"])
    st.session_state[RECHNUNGEN_EDITOR_DELETED_POSITION_IDS_KEY] = deleted_position_ids
    st.session_state[RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY] = [
        {"row_id": row_id} for row_id in range(1, new_row_count + 1)
    ]
    st.session_state[RECHNUNGEN_EDITOR_NEW_POSITION_COUNTER_KEY] = new_row_count
    st.session_state[RECHNUNGEN_EDITOR_POPULATION_STATE_KEY] = population_state


def _product_by_id(products: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(product.get("id", "")).strip(): product for product in products}


def _product_by_label(products: list[dict[str, Any]]) -> dict[str, Any]:
    return {_product_label(product): product for product in products}


def _read_selected_product(
    selected_label: str,
    products_by_label: dict[str, dict[str, Any]],
    existing_product: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if selected_label == "(Keep current)":
        return None

    selected_product = products_by_label.get(selected_label)
    if selected_product is not None:
        return selected_product

    return existing_product


def _invoice_ref(invoice_id: str) -> dict[str, Any]:
    return {
        "id": invoice_id,
        "objectName": "Invoice",
    }


def _invoice_show_net(invoice: dict[str, Any]) -> bool:
    value = invoice.get("showNet")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _position_quantity_value(position: dict[str, Any]) -> float:
    quantity = position.get("quantity", 1)
    try:
        return max(float(quantity), 0.0)
    except (TypeError, ValueError):
        return 1.0


def _position_tax_rate_value(position: dict[str, Any], invoice: dict[str, Any]) -> float:
    for key in ("taxRate",):
        value = position.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue

    for key in ("taxRate",):
        value = invoice.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue

    return 0.0


def _product_price_value(product: dict[str, Any], show_net: bool) -> float | None:
    price_keys = ("priceNet", "netPrice") if show_net else ("priceGross", "grossPrice")
    for key in price_keys:
        value = product.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _fallback_unity(position: dict[str, Any] | None, positions: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(position, dict):
        unity = position.get("unity")
        if isinstance(unity, dict):
            return deepcopy(unity)
    for row in positions:
        unity = row.get("unity")
        if isinstance(unity, dict):
            return deepcopy(unity)
    return {"id": 1, "objectName": "Unity"}


def _product_payload_from_row(
    product: dict[str, Any],
    *,
    fallback_name: str = "",
    fallback_description: str = "",
    show_net: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    product_id = str(product.get("id", "")).strip()
    if product_id:
        payload["part"] = {"id": product_id, "objectName": "Part"}

    product_name = str(product.get("name", "")).strip() or fallback_name
    if product_name:
        payload["name"] = product_name

    description = str(product.get("description", "")).strip() or fallback_description
    if description:
        payload["description"] = description
        payload["text"] = description

    price = _product_price_value(product, show_net)
    if price is not None:
        payload["price"] = price

    return payload


def _clean_invoice_for_save(invoice: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(invoice)
    for key in (
        "create",
        "update",
        "sevClient",
        "createUser",
        "paidAmount",
        "sumNet",
        "sumTax",
        "sumGross",
        "sumDiscounts",
        "sumNetForeignCurrency",
        "sumTaxForeignCurrency",
        "sumGrossForeignCurrency",
        "sumDiscountsForeignCurrency",
        "sumNetAccounting",
        "sumTaxAccounting",
        "sumGrossAccounting",
        "sumDiscountNet",
        "sumDiscountGross",
        "sumDiscountNetForeignCurrency",
        "sumDiscountGrossForeignCurrency",
        "taxRate",
        "enshrined",
        "additionalInformation",
        "openReminderCharge",
        "openInvoiceReminderDebit",
        "delinquent",
        "debit",
        "total",
        "checkAccountTransactions",
        "checkAccountTransactionLogs",
    ):
        cleaned.pop(key, None)
    cleaned["id"] = str(cleaned.get("id", "")).strip()
    cleaned["objectName"] = "Invoice"
    cleaned["mapAll"] = True
    return cleaned


def _clean_invoice_position_for_save(
    invoice_id: str,
    position: dict[str, Any],
    selected_product: dict[str, Any] | None,
    *,
    quantity: float,
    fallback_unity: dict[str, Any],
    show_net: bool,
) -> dict[str, Any]:
    cleaned = deepcopy(position)
    for key in (
        "create",
        "update",
        "sevClient",
        "invoice",
        "sumNet",
        "sumTax",
        "sumGross",
        "sumDiscount",
        "sumNetAccounting",
        "sumTaxAccounting",
        "sumGrossAccounting",
        "priceNet",
        "priceGross",
        "priceTax",
    ):
        cleaned.pop(key, None)

    position_id = str(cleaned.get("id", "")).strip()
    if position_id:
        cleaned["id"] = position_id
    else:
        cleaned.pop("id", None)
    cleaned["objectName"] = "InvoicePos"
    cleaned["mapAll"] = True
    cleaned["invoice"] = _invoice_ref(invoice_id)
    cleaned["quantity"] = float(quantity)
    cleaned["unity"] = deepcopy(fallback_unity)

    if selected_product is not None:
        cleaned.update(
            _product_payload_from_row(
                selected_product,
                fallback_name=str(position.get("name", "")).strip(),
                fallback_description=str(position.get("description", "")).strip()
                or str(position.get("text", "")).strip(),
                show_net=show_net,
            )
        )
    else:
        existing_name = str(position.get("name", "")).strip()
        if existing_name:
            cleaned["name"] = existing_name
        existing_description = str(position.get("description", "")).strip() or str(
            position.get("text", "")
        ).strip()
        if existing_description:
            cleaned["description"] = existing_description
            cleaned["text"] = existing_description
        current_price = position.get("price")
        if current_price not in (None, ""):
            try:
                cleaned["price"] = float(current_price)
            except (TypeError, ValueError):
                pass

    tax_rate = _position_tax_rate_value(position, {"taxRate": position.get("taxRate")})
    cleaned["taxRate"] = tax_rate

    return cleaned


def _build_new_position_template(
    invoice: dict[str, Any],
    base_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    first_position = base_positions[0] if base_positions else {}
    return {
        "quantity": 1.0,
        "taxRate": _position_tax_rate_value(first_position, invoice),
        "unity": _fallback_unity(first_position, base_positions),
    }


def _build_invoice_position_update_payload(
    invoice: dict[str, Any],
    position_specs: list[dict[str, Any]],
    deleted_position_ids: list[str] | None = None,
) -> dict[str, Any]:
    invoice_id = str(invoice.get("id", "")).strip()
    if not invoice_id:
        raise RuntimeError("Selected invoice is missing an id.")

    cleaned_positions: list[dict[str, Any]] = []
    base_positions = [
        spec["position"] for spec in position_specs if isinstance(spec.get("position"), dict)
    ]
    fallback_unity = _fallback_unity(base_positions[0] if base_positions else None, base_positions)
    show_net = _invoice_show_net(invoice)

    for spec in position_specs:
        position = spec.get("position")
        if not isinstance(position, dict):
            continue
        selected_product = spec.get("selected_product")
        quantity = spec.get("quantity", 1.0)
        is_new = bool(spec.get("is_new", False))
        if is_new and not isinstance(selected_product, dict):
            continue
        position_fallback_unity = (
            fallback_unity if is_new else _fallback_unity(position, base_positions)
        )
        cleaned_positions.append(
            _clean_invoice_position_for_save(
                invoice_id,
                position,
                selected_product if isinstance(selected_product, dict) else None,
                quantity=float(quantity or 1.0),
                fallback_unity=position_fallback_unity,
                show_net=show_net,
            )
        )

    cleaned_deleted_position_ids = [
        position_id
        for position_id in (deleted_position_ids or [])
        if isinstance(position_id, str) and position_id.strip()
    ]

    return {
        "invoice": _clean_invoice_for_save(invoice),
        "invoicePosSave": cleaned_positions,
        "invoicePosDelete": [
            {"id": position_id, "objectName": "InvoicePos"}
            for position_id in cleaned_deleted_position_ids
        ]
        or None,
        "filename": None,
    }


def _filtered_invoice_rows(rows: list[dict]) -> list[dict]:
    selected_status_labels = st.session_state.get(RECHNUNGEN_STATUS_FILTER_KEY, [])
    status_options = build_status_filter_options(
        rows,
        status_getter=_invoice_status_value,
    )
    selected_status_values = selected_option_values(selected_status_labels, status_options)
    if status_options and not selected_status_values:
        return []
    text_query = str(st.session_state.get(RECHNUNGEN_TEXT_QUERY_KEY, "")).strip()
    start_date = st.session_state.get(RECHNUNGEN_START_DATE_KEY)
    end_date = st.session_state.get(RECHNUNGEN_END_DATE_KEY)

    filtered_rows: list[dict] = []
    for row in rows:
        row_status = _invoice_status_value(row)
        if selected_status_values and row_status not in selected_status_values:
            continue
        if not _invoice_text_matches(row, text_query):
            continue

        row_date = _invoice_date(row)
        if not is_within_date_range(row_date, start_date=start_date, end_date=end_date):
            continue

        filtered_rows.append(row)
    return filtered_rows


def render_rechnungen_section() -> None:
    st.subheader("Rechnungsverwaltung")
    with st.form("sevdesk_rechnungen_form"):
        current_load_limit = int(
            st.session_state.get(RECHNUNGEN_LOAD_LIMIT_KEY) or RECHNUNGEN_DEFAULT_LOAD_LIMIT
        )
        load_limit = st.number_input(
            "Anzahl Rechnungen",
            min_value=1,
            max_value=1000,
            value=current_load_limit,
            step=10,
            key=RECHNUNGEN_LOAD_LIMIT_KEY,
            help="Default: 100. Load only the newest Rechnungen unless you increase this value.",
        )
        latest_submit = st.form_submit_button("Rechnungen laden", width="stretch")

    if latest_submit:
        token = ensure_token()
        if token:
            try:
                logger.info(
                    "Triggered 'Rechnungen laden' from Streamlit UI with limit=%s.",
                    int(load_limit),
                )
                with st.spinner("Rechnungen werden aus sevDesk geladen..."):
                    st.session_state[RECHNUNGEN_ROWS_KEY] = request_invoices(
                        base_url(),
                        token,
                        int(load_limit),
                    )
            except Exception as exc:
                report_error(
                    f"Failed to load Rechnungen: {exc}",
                    log_message="Failed to load Rechnungen",
                    exc_info=True,
                )

    rows = st.session_state.get(RECHNUNGEN_ROWS_KEY)
    if rows is None:
        st.caption("Load the latest sevDesk Rechnungen to inspect them here.")
        return
    if not rows:
        st.info("No Rechnungen found.")
        return

    current_start_date = st.session_state.get(RECHNUNGEN_START_DATE_KEY) or (
        date.today() - timedelta(days=30)
    )
    current_end_date = st.session_state.get(RECHNUNGEN_END_DATE_KEY) or date.today()
    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        st.date_input("Rechnungsdatum ab", value=current_start_date, key=RECHNUNGEN_START_DATE_KEY)
        st.date_input("Rechnungsdatum bis", value=current_end_date, key=RECHNUNGEN_END_DATE_KEY)
    with filter_col2:
        st.text_input(
            "Suche in Rechnung",
            key=RECHNUNGEN_TEXT_QUERY_KEY,
            help="Matches invoice number, customer name, description, status, and id.",
        )
    with filter_col3:
        status_options = build_status_filter_options(
            rows,
            status_getter=_invoice_status_value,
        )
        status_labels = list(status_options.keys())
        sync_multiselect_options(
            RECHNUNGEN_STATUS_FILTER_KEY,
            RECHNUNGEN_STATUS_FILTER_OPTIONS_KEY,
            status_labels,
        )
        st.multiselect(
            "Status filter",
            options=status_labels,
            key=RECHNUNGEN_STATUS_FILTER_KEY,
            disabled=not status_labels,
        )

    start_date = st.session_state.get(RECHNUNGEN_START_DATE_KEY)
    end_date = st.session_state.get(RECHNUNGEN_END_DATE_KEY)
    if not validate_date_range(
        start_date,
        end_date,
        start_label="Rechnungsdatum ab",
        end_label="Rechnungsdatum bis",
    ):
        return

    filtered_rows = _filtered_invoice_rows(rows)
    total_count = len(rows)
    show_invoices(filtered_rows, total_count=total_count)

    st.divider()
    st.subheader("Lieferscheine Orders Import")
    st.caption(
        "Upload an Excel workbook with a `summary_folder_product` sheet. "
        "The parsed folder JSONs are shown below for now."
    )
    uploaded_file = st.file_uploader(
        "Lieferscheine Orders Excel",
        type=["xlsx"],
        key="sevdesk_rechnungen_lieferscheine_orders_upload",
    )

    folder_jsons: list[dict[str, Any]] | None = None
    if uploaded_file is not None:
        try:
            folder_jsons = extract_lieferscheine_folder_jsons(
                uploaded_file.getvalue(),
                source_name=getattr(uploaded_file, "name", None),
            )
            st.session_state[RECHNUNGEN_LIEFERSCHEINE_JSONS_KEY] = folder_jsons
        except Exception as exc:
            st.error(f"Failed to parse Lieferscheine Orders workbook: {exc}")
            st.session_state.pop(RECHNUNGEN_LIEFERSCHEINE_JSONS_KEY, None)
    else:
        folder_jsons = st.session_state.get(RECHNUNGEN_LIEFERSCHEINE_JSONS_KEY)

    if isinstance(folder_jsons, list) and folder_jsons:
        with st.expander("Show parsed folder JSONs", expanded=False):
            st.json(folder_jsons)

    st.divider()
    st.subheader("Rechnungspositionen bearbeiten")
    st.caption(
        "Wähle einen Kunden aus der lokalen sevDesk-Kundenliste, lade die neueste Rechnung im Draft-Status `100`, prüfe die fuzzy Produktvorschläge und aktualisiere danach die Rechnungspositionen in sevDesk."
    )

    customer_options = load_rechnungen_customer_names()
    if not customer_options:
        st.info("Keine Kunden in der sevDesk-Kundenliste gefunden.")
        return
    current_customer = st.session_state.get(RECHNUNGEN_CUSTOMER_KEY)
    if current_customer not in customer_options:
        st.session_state[RECHNUNGEN_CUSTOMER_KEY] = customer_options[0]
    selected_customer = str(st.session_state.get(RECHNUNGEN_CUSTOMER_KEY, customer_options[0]))
    with st.expander("Kundenliste verwalten", expanded=False):
        st.caption(f"Gespeichert in `{RECHNUNGEN_CUSTOMERS_PATH}`")
        with st.form("sevdesk_rechnungen_customer_list_form", clear_on_submit=True):
            new_customer_name = st.text_input(
                "Neuer Kunde",
                key=RECHNUNGEN_CUSTOMER_NEW_NAME_KEY,
                placeholder="Kundenname eingeben",
            )
            add_customer_clicked = st.form_submit_button("Zur Kundenliste hinzufügen", width="stretch")

        if add_customer_clicked:
            cleaned_customer_name = str(new_customer_name).strip()
            if not cleaned_customer_name:
                st.warning("Bitte einen Kundennamen eingeben.")
            else:
                updated_customer_options = add_rechnungen_customer_name(cleaned_customer_name)
                canonical_customer_name = next(
                    (
                        name
                        for name in updated_customer_options
                        if name.casefold() == cleaned_customer_name.casefold()
                    ),
                    cleaned_customer_name,
                )
                st.session_state[RECHNUNGEN_CUSTOMER_KEY] = canonical_customer_name
                st.rerun()

    draft_rows = [
        row
        for row in rows
        if _invoice_status_value(row) == "100" and _invoice_customer_name(row) == selected_customer
    ]
    draft_rows = sorted(draft_rows, key=_invoice_sort_key, reverse=True)

    if not draft_rows:
        st.info("Keine Rechnungen mit Status `100` für den gewählten Kunden gefunden.")
    else:
        draft_invoice_ids = [str(row.get("id", "")).strip() for row in draft_rows]
        current_invoice_id = st.session_state.get(RECHNUNGEN_DRAFT_INVOICE_ID_KEY)
        if current_invoice_id not in draft_invoice_ids:
            st.session_state[RECHNUNGEN_DRAFT_INVOICE_ID_KEY] = draft_invoice_ids[0]

        draft_by_id = {str(row.get("id", "")).strip(): row for row in draft_rows}
        selector_col1, selector_col2 = st.columns([1, 1.4])
        with selector_col1:
            selected_customer = st.selectbox(
                "Kunde",
                options=customer_options,
                key=RECHNUNGEN_CUSTOMER_KEY,
                width="stretch",
            )
        with selector_col2:
            selected_invoice_id = st.selectbox(
                "Neueste Draft-Rechnung",
                options=draft_invoice_ids,
                format_func=lambda invoice_id: _draft_invoice_label(draft_by_id[invoice_id]),
                key=RECHNUNGEN_DRAFT_INVOICE_ID_KEY,
                width="stretch",
            )

        loaded_invoice_id = st.session_state.get(RECHNUNGEN_EDITOR_INVOICE_ID_KEY)
        if selected_invoice_id and loaded_invoice_id != selected_invoice_id:
            token = ensure_token()
            if token:
                try:
                    with st.spinner("Rechnung und Positionen werden geladen..."):
                        st.session_state[RECHNUNGEN_EDITOR_INVOICE_KEY] = request_invoice_by_id(
                            base_url(),
                            token,
                            selected_invoice_id,
                        )
                        st.session_state[RECHNUNGEN_EDITOR_POSITIONS_KEY] = request_invoice_positions(
                            base_url(),
                            token,
                            selected_invoice_id,
                        )
                        st.session_state[RECHNUNGEN_EDITOR_INVOICE_ID_KEY] = selected_invoice_id
                        st.session_state[RECHNUNGEN_EDITOR_DELETED_POSITION_IDS_KEY] = []
                        st.session_state[RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY] = []
                        st.session_state[RECHNUNGEN_EDITOR_NEW_POSITION_COUNTER_KEY] = 0
                except Exception as exc:
                    report_error(
                        f"Failed to load Rechnung details: {exc}",
                        log_message="Failed to load Rechnung details",
                        exc_info=True,
                    )

        selected_invoice = st.session_state.get(RECHNUNGEN_EDITOR_INVOICE_KEY)
        selected_positions = st.session_state.get(RECHNUNGEN_EDITOR_POSITIONS_KEY)
        if (
            isinstance(selected_invoice, dict)
            and isinstance(selected_positions, list)
            and st.session_state.get(RECHNUNGEN_EDITOR_INVOICE_ID_KEY) == selected_invoice_id
        ):
            deleted_position_ids = {
                str(position_id).strip()
                for position_id in st.session_state.get(
                    RECHNUNGEN_EDITOR_DELETED_POSITION_IDS_KEY,
                    [],
                )
                if str(position_id).strip()
            }
            base_positions = [
                row
                for row in selected_positions
                if isinstance(row, dict)
                and str(row.get("id", "")).strip() not in deleted_position_ids
            ]
            products = load_stored_products()
            if RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY not in st.session_state:
                st.session_state[RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY] = []
            if RECHNUNGEN_EDITOR_NEW_POSITION_COUNTER_KEY not in st.session_state:
                st.session_state[RECHNUNGEN_EDITOR_NEW_POSITION_COUNTER_KEY] = 0

            population_state = st.session_state.pop(RECHNUNGEN_EDITOR_POPULATION_STATE_KEY, None)
            populated_rows: list[dict[str, Any]] = []
            if (
                isinstance(population_state, dict)
                and population_state.get("invoice_id") == selected_invoice_id
            ):
                _clear_widget_state_for_invoice(
                    selected_invoice_id,
                    base_positions,
                    new_position_count=int(
                        st.session_state.get(RECHNUNGEN_EDITOR_NEW_POSITION_COUNTER_KEY, 0)
                    ),
                )
                for item in population_state.get("rows", []):
                    if not isinstance(item, dict):
                        continue
                    populated_rows.append(item)

            pending_new_positions = list(
                st.session_state.get(RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY, [])
            )
            visible_position_count = len(base_positions) + len(pending_new_positions)
            info_col1, info_col2 = st.columns([1.6, 1])
            with info_col1:
                st.caption(
                    f"Bearbeite Rechnung `{selected_invoice.get('invoiceNumber', selected_invoice_id)}` mit {visible_position_count} Positionen."
                )
            with info_col2:
                st.caption(f"{len(products)} Produkte geladen.")
            if not products:
                st.info(
                    "Keine sevDesk-Produkte gefunden. Lade zuerst die Produktliste in Accounting MD."
                )
            else:
                if visible_position_count == 0:
                    st.info("Die ausgewählte Rechnung hat keine Positionen.")
                products_by_id = _product_by_id(products)
                products_by_label = _product_by_label(products)
                product_labels = sorted(products_by_label.keys())

                folder_jsons = st.session_state.get(RECHNUNGEN_LIEFERSCHEINE_JSONS_KEY)
                invoice_customer_name = _invoice_customer_name(selected_invoice) or selected_customer
                matched_folder_payload, folder_match_score = _best_matching_folder_payload(
                    invoice_customer_name,
                    folder_jsons if isinstance(folder_jsons, list) else None,
                )
                if matched_folder_payload is not None:
                    matched_folder_name = str(matched_folder_payload.get("folder", "")).strip() or "-"
                    matched_rows, unmatched_rows = _build_folder_population_rows(
                        matched_folder_payload,
                        products,
                    )
                    folder_col1, folder_col2 = st.columns([1.8, 1])
                    with folder_col1:
                        st.info(
                            f"Ordner-Match fuer `{invoice_customer_name}`: `{matched_folder_name}` ({folder_match_score:.0%})."
                        )
                        if matched_rows:
                            st.caption(
                                f"{len(matched_rows)} Produktpositionen koennen automatisch befuellt werden."
                            )
                    with folder_col2:
                        if matched_rows and st.button(
                            "Aus Ordner-JSON befuellen",
                            key=f"sevdesk_rechnung_populate_from_folder_{selected_invoice_id}",
                            width="stretch",
                        ):
                            _apply_folder_population_to_editor(
                                invoice_id=selected_invoice_id,
                                base_positions=base_positions,
                                matched_rows=matched_rows,
                            )
                            st.rerun()
                    if unmatched_rows:
                        unmatched_product_names = ", ".join(
                            sorted(
                                {
                                    str(item.get("summary_row", {}).get("product", "")).strip()
                                    for item in unmatched_rows
                                    if isinstance(item.get("summary_row"), dict)
                                    and str(item.get("summary_row", {}).get("product", "")).strip()
                                }
                            )
                        )
                        st.warning(
                            "Diese Summary-Produkte wurden nicht sicher auf sevDesk-Produkte gemappt: "
                            f"{unmatched_product_names or '-'}."
                        )
                elif isinstance(folder_jsons, list) and folder_jsons:
                    st.caption(
                        f"Kein ausreichend sicherer Ordner-Match fuer `{invoice_customer_name}` gefunden."
                    )

                position_specs: list[dict[str, Any]] = []

                st.markdown("**Positionen**")
                header_col1, header_col2, header_col3 = st.columns([5, 1, 1])
                with header_col1:
                    st.caption("Produkt")
                with header_col2:
                    st.caption("Menge")
                with header_col3:
                    st.caption("Aktion")
                for index, position in enumerate(base_positions, start=1):
                    position_id = str(position.get("id", "")).strip() or f"row-{index}"
                    existing_part = position.get("part")
                    existing_part_id = (
                        str(existing_part.get("id", "")).strip()
                        if isinstance(existing_part, dict)
                        else ""
                    )
                    existing_product = products_by_id.get(existing_part_id)
                    suggested_label, _ = _suggest_product_label(position, products)
                    populated_row = (
                        populated_rows[index - 1] if index - 1 < len(populated_rows) else {}
                    )
                    default_label = (
                        str(populated_row.get("selected_label", "")).strip()
                        if str(populated_row.get("selected_label", "")).strip()
                        else (
                            _product_label(existing_product)
                            if existing_product is not None
                            else (suggested_label or "(Keep current)")
                        )
                    )
                    default_quantity = (
                        float(populated_row.get("quantity", 0.0))
                        if populated_row
                        else _position_quantity_value(position)
                    )
                    options = ["(Keep current)"] + product_labels
                    default_index = options.index(default_label) if default_label in options else 0

                    row_label_col, input_col1, input_col2, input_col3 = st.columns([0.8, 5, 1, 1])
                    with row_label_col:
                        st.caption(f"#{index}")
                    with input_col1:
                        selected_label = st.selectbox(
                            "Produkt",
                            options=options,
                            index=default_index,
                            key=f"sevdesk_rechnung_product_{selected_invoice_id}_{position_id}",
                            width="stretch",
                            label_visibility="collapsed",
                        )
                    with input_col2:
                        quantity_value = st.number_input(
                            "Menge",
                            min_value=0.0,
                            value=default_quantity,
                            step=1.0,
                            key=f"sevdesk_rechnung_quantity_{selected_invoice_id}_{position_id}",
                            label_visibility="collapsed",
                        )
                    with input_col3:
                        remove_clicked = st.button(
                            "🗑️",
                            key=f"sevdesk_rechnung_remove_existing_position_{selected_invoice_id}_{position_id}",
                            width="stretch",
                            help="Position löschen",
                        )
                    if remove_clicked:
                        updated_deleted_position_ids = list(
                            st.session_state.get(
                                RECHNUNGEN_EDITOR_DELETED_POSITION_IDS_KEY,
                                [],
                            )
                        )
                        if position_id:
                            updated_deleted_position_ids.append(position_id)
                            st.session_state[RECHNUNGEN_EDITOR_DELETED_POSITION_IDS_KEY] = (
                                sorted(set(updated_deleted_position_ids))
                            )
                        st.rerun()
                    position_specs.append(
                        {
                            "position": position,
                            "selected_product": _read_selected_product(
                                selected_label,
                                products_by_label,
                                existing_product,
                            ),
                            "quantity": quantity_value,
                            "is_new": False,
                        }
                    )

                new_positions = list(
                    st.session_state.get(RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY, [])
                )
                if new_positions:
                    retained_new_positions: list[dict[str, Any]] = []
                    for draft_index, draft in enumerate(new_positions, start=1):
                        row_number = len(base_positions) + draft_index
                        row_id = str(draft.get("row_id", draft_index))
                        draft_key = f"{selected_invoice_id}_{row_id}"
                        draft_template = _build_new_position_template(selected_invoice, base_positions)
                        suggested_label, _ = _suggest_product_label(
                            draft_template,
                            products,
                        )
                        select_options = ["(Bitte wählen)"] + product_labels
                        populated_row = (
                            populated_rows[row_number - 1]
                            if row_number - 1 < len(populated_rows)
                            else {}
                        )
                        default_label = str(populated_row.get("selected_label", "")).strip()
                        if not default_label:
                            default_label = (
                                suggested_label if suggested_label in product_labels else "(Bitte wählen)"
                            )
                        default_index = (
                            select_options.index(default_label)
                            if default_label in select_options
                            else 0
                        )

                        row_label_col, input_col1, input_col2, input_col3 = st.columns([0.8, 5, 1, 1])
                        with row_label_col:
                            st.caption(f"#{row_number}")
                        with input_col1:
                            selected_label = st.selectbox(
                                "Produkt",
                                options=select_options,
                                index=default_index,
                                key=f"sevdesk_rechnung_new_product_{draft_key}",
                                width="stretch",
                                label_visibility="collapsed",
                            )
                        with input_col2:
                            quantity_value = st.number_input(
                                "Menge",
                                min_value=0.0,
                                value=float(populated_row.get("quantity", 1.0) or 1.0),
                                step=1.0,
                                key=f"sevdesk_rechnung_new_quantity_{draft_key}",
                                label_visibility="collapsed",
                            )
                        with input_col3:
                            remove_clicked = st.button(
                                "🗑️",
                                key=f"sevdesk_rechnung_remove_new_position_{draft_key}",
                                width="stretch",
                                help="Position löschen",
                            )
                        if remove_clicked:
                            retained_new_positions = [
                                row for row in new_positions if str(row.get("row_id")) != row_id
                            ]
                            st.session_state[RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY] = retained_new_positions
                            st.rerun()

                        selected_product = (
                            products_by_label.get(selected_label)
                            if selected_label in products_by_label
                            else None
                        )
                        position_specs.append(
                            {
                                "position": draft_template,
                                "selected_product": selected_product,
                                "quantity": quantity_value,
                                "is_new": True,
                            }
                        )
                        retained_new_positions.append(draft)

                    st.session_state[RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY] = retained_new_positions

                action_col1, action_col2 = st.columns([1, 1.4])
                with action_col1:
                    add_new_row_clicked = st.button(
                        "Neue Position hinzufügen",
                        key=f"sevdesk_rechnung_add_new_position_{selected_invoice_id}",
                        width="stretch",
                    )
                if add_new_row_clicked:
                    counter = int(st.session_state.get(RECHNUNGEN_EDITOR_NEW_POSITION_COUNTER_KEY, 0))
                    counter += 1
                    st.session_state[RECHNUNGEN_EDITOR_NEW_POSITION_COUNTER_KEY] = counter
                    new_positions = list(
                        st.session_state.get(RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY, [])
                    )
                    new_positions.append({"row_id": counter})
                    st.session_state[RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY] = new_positions

                try:
                    update_payload = _build_invoice_position_update_payload(
                        selected_invoice,
                        position_specs,
                        deleted_position_ids=list(deleted_position_ids),
                    )
                except Exception as exc:
                    st.error(f"Update payload could not be built: {exc}")
                else:
                    with st.expander("Update payload preview", expanded=False):
                        st.json(update_payload)

                    with action_col2:
                        update_clicked = st.button(
                            "Rechnungspositionen in sevDesk aktualisieren",
                            key=f"sevdesk_rechnung_update_{selected_invoice_id}",
                            width="stretch",
                        )

                    if update_clicked:
                        token = ensure_token()
                        if token:
                            try:
                                with st.spinner("Rechnungspositionen werden aktualisiert..."):
                                    save_invoice(base_url(), token, update_payload)
                                    st.session_state[RECHNUNGEN_EDITOR_INVOICE_KEY] = (
                                        request_invoice_by_id(
                                            base_url(),
                                            token,
                                            selected_invoice_id,
                                        )
                                    )
                                    st.session_state[RECHNUNGEN_EDITOR_POSITIONS_KEY] = (
                                        request_invoice_positions(
                                            base_url(),
                                            token,
                                            selected_invoice_id,
                                        )
                                    )
                                    st.session_state[RECHNUNGEN_EDITOR_DELETED_POSITION_IDS_KEY] = []
                                    st.session_state[RECHNUNGEN_EDITOR_NEW_POSITIONS_KEY] = []
                                st.success("Rechnungspositionen wurden in sevDesk aktualisiert.")
                            except Exception as exc:
                                report_error(
                                    f"Failed to update Rechnungspositionen: {exc}",
                                    log_message="Failed to update Rechnungspositionen",
                                    exc_info=True,
                                )
