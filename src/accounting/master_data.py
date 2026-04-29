from datetime import datetime, timezone
from typing import Any

from src.accounting.common import flag_as_bool
from src.accounting.state import (
    ACCOUNTING_TYPES_EXPORT_PATH,
    CHECK_ACCOUNTS_EXPORT_PATH,
    PRODUCTS_EXPORT_PATH,
    TAX_RULES_EXPORT_PATH,
    TAX_SETS_EXPORT_PATH,
)
from src.sevdesk.api import (
    fetch_all_accounting_types,
    fetch_all_check_accounts,
    fetch_all_products,
    fetch_all_tax_rules,
    fetch_all_tax_sets,
    request_voucher_positions,
)
from src.sevdesk.voucher import load_rows, write_json


def format_accounting_type_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "name": str(row.get("name", "")).strip(),
        "description": str(row.get("description") or row.get("text") or "").strip(),
        "type": row.get("type", ""),
        "skr03": row.get("skr03"),
        "skr04": row.get("skr04"),
        "active": str(row.get("active", "0")) == "1",
        "status": row.get("status", ""),
    }


def format_check_account_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "name": str(row.get("name", "")).strip(),
        "type": row.get("type", ""),
        "currency": row.get("currency"),
        "defaultAccount": flag_as_bool(row.get("defaultAccount", False)),
        "status": row.get("status", ""),
        "accountingNumber": row.get("accountingNumber"),
        "iban": row.get("iban"),
        "bic": row.get("bic"),
        "bankServer": row.get("bankServer"),
        "lastSync": row.get("lastSync"),
    }


def format_tax_rule_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "name": str(row.get("name", "")).strip(),
        "code": row.get("code"),
        "taxType": row.get("taxType"),
        "taxRate": row.get("taxRate", row.get("rate")),
        "status": row.get("status"),
        "isDefault": flag_as_bool(row.get("isDefault", False)),
    }


def format_tax_set_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "name": str(row.get("name", "")).strip(),
        "taxRate": row.get("taxRate", row.get("rate")),
        "taxText": row.get("taxText"),
        "taxType": row.get("taxType"),
        "status": row.get("status"),
        "isDefault": flag_as_bool(row.get("isDefault", False)),
    }


def _format_related_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "title", "label", "number", "id"):
            nested_value = str(value.get(key, "")).strip()
            if nested_value:
                return nested_value
        return ""
    return str(value or "").strip()


def format_product_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")).strip(),
        "name": str(row.get("name", row.get("partName", ""))).strip(),
        "articleNumber": str(row.get("articleNumber", row.get("partNumber", ""))).strip(),
        "description": str(row.get("description", "")).strip(),
        "unity": str(row.get("unity", row.get("unit", ""))).strip(),
        "priceNet": row.get("priceNet", row.get("netPrice")),
        "priceGross": row.get("priceGross", row.get("grossPrice")),
        "stock": row.get("stock"),
        "taxRule": _format_related_value(row.get("taxRule")),
        "active": flag_as_bool(row.get("active", True)),
        "status": row.get("status", ""),
    }


def export_check_accounts(base_url: str, token: str) -> list[dict[str, Any]]:
    rows = fetch_all_check_accounts(base_url, token, 1000, "id")
    essential_rows = [format_check_account_row(row) for row in rows]
    payload = {
        "informationsart": "checkaccounts",
        "quelle": "sevdesk",
        "quelle_endpoint": "/CheckAccount",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "anzahl": len(essential_rows),
        "feldschema": list(essential_rows[0].keys()) if essential_rows else [],
        "daten": essential_rows,
    }
    write_json(CHECK_ACCOUNTS_EXPORT_PATH, payload)
    return essential_rows


def export_accounting_types(base_url: str, token: str) -> list[dict[str, Any]]:
    rows = fetch_all_accounting_types(base_url, token, 1000, "id")
    essential_rows = [format_accounting_type_row(row) for row in rows]
    payload = {
        "informationsart": "accounting_types",
        "quelle": "sevdesk",
        "quelle_endpoint": "/AccountingType",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "anzahl": len(essential_rows),
        "feldschema": list(essential_rows[0].keys()) if essential_rows else [],
        "daten": essential_rows,
    }
    write_json(ACCOUNTING_TYPES_EXPORT_PATH, payload)
    return essential_rows


def export_tax_rules(base_url: str, token: str) -> list[dict[str, Any]]:
    rows = fetch_all_tax_rules(base_url, token, 1000, "id")
    essential_rows = [format_tax_rule_row(row) for row in rows]
    payload = {
        "informationsart": "tax_rules",
        "quelle": "sevdesk",
        "quelle_endpoint": "/TaxRule",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "anzahl": len(essential_rows),
        "feldschema": list(essential_rows[0].keys()) if essential_rows else [],
        "daten": essential_rows,
    }
    write_json(TAX_RULES_EXPORT_PATH, payload)
    return essential_rows


def export_tax_sets(base_url: str, token: str) -> list[dict[str, Any]]:
    rows = fetch_all_tax_sets(base_url, token, 1000, "id")
    essential_rows = [format_tax_set_row(row) for row in rows]
    payload = {
        "informationsart": "tax_sets",
        "quelle": "sevdesk",
        "quelle_endpoint": "/TaxSet",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "anzahl": len(essential_rows),
        "feldschema": list(essential_rows[0].keys()) if essential_rows else [],
        "daten": essential_rows,
    }
    write_json(TAX_SETS_EXPORT_PATH, payload)
    return essential_rows


def export_products(base_url: str, token: str) -> list[dict[str, Any]]:
    # sevDesk exposes products as `Part` resources in the API.
    rows = fetch_all_products(base_url, token, 1000, "id")
    essential_rows = [format_product_row(row) for row in rows]
    payload = {
        "informationsart": "products",
        "quelle": "sevdesk",
        "quelle_endpoint": "/Part",
        "exportiert_am_utc": datetime.now(timezone.utc).isoformat(),
        "anzahl": len(essential_rows),
        "feldschema": list(essential_rows[0].keys()) if essential_rows else [],
        "daten": essential_rows,
    }
    write_json(PRODUCTS_EXPORT_PATH, payload)
    return essential_rows


def load_stored_check_accounts() -> list[dict[str, Any]]:
    return load_rows(CHECK_ACCOUNTS_EXPORT_PATH)


def load_stored_accounting_types() -> list[dict[str, Any]]:
    return load_rows(ACCOUNTING_TYPES_EXPORT_PATH)


def load_stored_products() -> list[dict[str, Any]]:
    return load_rows(PRODUCTS_EXPORT_PATH)


def _collect_accounting_type_ids_from_value(value: Any, collected: set[str]) -> None:
    if isinstance(value, dict):
        accounting_type = value.get("accountingType")
        if isinstance(accounting_type, dict):
            accounting_type_id = str(accounting_type.get("id", "")).strip()
            if accounting_type_id:
                collected.add(accounting_type_id)

        for nested_value in value.values():
            _collect_accounting_type_ids_from_value(nested_value, collected)
        return

    if isinstance(value, list):
        for nested_value in value:
            _collect_accounting_type_ids_from_value(nested_value, collected)


def collect_used_accounting_type_ids_from_vouchers(base_url: str, token: str) -> set[str]:
    collected: set[str] = set()
    rows = request_voucher_positions(base_url, token)
    for row in rows:
        _collect_accounting_type_ids_from_value(row, collected)
    return collected


def load_stored_tax_rules() -> list[dict[str, Any]]:
    return load_rows(TAX_RULES_EXPORT_PATH)


def load_stored_tax_sets() -> list[dict[str, Any]]:
    return load_rows(TAX_SETS_EXPORT_PATH)
