from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .constants import FALLBACK_ACCOUNTING_TYPE_ID, FALLBACK_ACCOUNTING_TYPE_NAME


def format_amount(voucher: dict[str, Any]) -> str:
    for key in ("sumGross", "totalGross", "sumNet"):
        value = voucher.get(key)
        if value is not None:
            return str(value)
    return "-"


def format_date(voucher: dict[str, Any]) -> str:
    for key in ("voucherDate", "create", "update", "invoiceDate"):
        value = voucher.get(key)
        if value:
            return str(value)
    return "-"


def format_number(voucher: dict[str, Any]) -> str:
    for key in ("voucherNumber", "number", "id"):
        value = voucher.get(key)
        if value:
            return str(value)
    return "-"


def format_text(voucher: dict[str, Any]) -> str:
    for key in ("description", "name"):
        value = voucher.get(key)
        if value:
            return str(value).strip()
    return "-"


def print_rows(vouchers: list[dict[str, Any]]) -> None:
    if not vouchers:
        print("No Belege found.")
        return

    print(f"Found {len(vouchers)} Belege:")
    print("-" * 110)
    print(f"{'ID':<8} {'Nummer':<20} {'Datum':<22} {'Betrag':<12} Beschreibung")
    print("-" * 110)
    for voucher in vouchers:
        voucher_id = str(voucher.get("id", "-"))
        number = format_number(voucher)
        voucher_date = format_date(voucher)
        amount = format_amount(voucher)
        description = format_text(voucher)
        print(f"{voucher_id:<8} {number:<20} {voucher_date:<22} {amount:<12} {description}")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    if not isinstance(payload, dict):
        return []

    rows = payload.get("daten")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def load_buchunggskonten(path: Path) -> list[dict[str, Any]]:
    return load_rows(path)


def load_zahlungskonten(path: Path) -> list[dict[str, Any]]:
    return load_rows(path)


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def known_buchunggskonto_ids(rows: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for row in rows:
        value = row.get("id")
        if value is None:
            continue
        ids.add(str(value))
    return ids


def select_default_buchunggskonto(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    candidates = [
        row
        for row in rows
        if is_truthy(row.get("active", "1"))
        and str(row.get("status", "100")) == "100"
        and not is_truthy(row.get("hidden", "0"))
    ]
    if not candidates:
        candidates = rows

    by_name = [row for row in candidates if "sonstiges" in str(row.get("name", "")).strip().lower()]
    if by_name:
        return by_name[0]

    by_type = [row for row in candidates if str(row.get("type", "")).upper() in {"IC", "DC"}]
    if by_type:
        return by_type[0]

    return candidates[0]


def select_default_zahlungskonto(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    candidates = [row for row in rows if str(row.get("status", "")) == "100"]
    if not candidates:
        candidates = rows

    defaults = [row for row in candidates if is_truthy(row.get("defaultAccount", "0"))]
    if defaults:
        return defaults[0]

    return candidates[0]


def select_buchunggskonto(
    rows: list[dict[str, Any]],
    *,
    accounting_type_id: str,
    accounting_type_name_contains: str,
) -> dict[str, Any] | None:
    wanted_id = accounting_type_id.strip()
    if wanted_id:
        for row in rows:
            if str(row.get("id", "")).strip() == wanted_id:
                return row
        return None

    name_contains = accounting_type_name_contains.strip().lower()
    if name_contains:
        matches = [row for row in rows if name_contains in str(row.get("name", "")).strip().lower()]
        if not matches:
            return None

        active_matches = [
            row
            for row in matches
            if is_truthy(row.get("active", "1"))
            and str(row.get("status", "100")) == "100"
            and not is_truthy(row.get("hidden", "0"))
        ]
        return active_matches[0] if active_matches else matches[0]

    return select_default_buchunggskonto(rows)


def select_zahlungskonto(
    rows: list[dict[str, Any]],
    *,
    check_account_id: str,
    check_account_name: str,
) -> dict[str, Any] | None:
    wanted_id = check_account_id.strip()
    if wanted_id:
        for row in rows:
            if str(row.get("id", "")).strip() == wanted_id:
                return row
        return None

    wanted_name = check_account_name.strip().lower()
    if wanted_name:
        matches = [row for row in rows if wanted_name in str(row.get("name", "")).strip().lower()]
        if not matches:
            return None
        active_matches = [row for row in matches if str(row.get("status", "")) == "100"]
        return active_matches[0] if active_matches else matches[0]

    return select_default_zahlungskonto(rows)


def accounting_type_ref_from_buchunggskonto(row: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(row, dict) and row.get("id") is not None:
        return {
            "id": str(row.get("id")),
            "objectName": "AccountingType",
        }
    return {
        "id": FALLBACK_ACCOUNTING_TYPE_ID,
        "objectName": "AccountingType",
    }


def check_account_ref_from_zahlungskonto(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(row, dict) and row.get("id") is not None:
        return {
            "id": str(row.get("id")),
            "objectName": "CheckAccount",
        }
    return None


def today_str() -> str:
    return date.today().strftime("%d.%m.%Y")


def default_create_template(
    default_buchunggskonto: dict[str, Any] | None = None,
    default_zahlungskonto: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = date.today()
    deadline = today + timedelta(days=14)
    accounting_type = accounting_type_ref_from_buchunggskonto(default_buchunggskonto)
    accounting_type_name = (
        str(default_buchunggskonto.get("name", "")).strip()
        if isinstance(default_buchunggskonto, dict)
        else FALLBACK_ACCOUNTING_TYPE_NAME
    )
    check_account = check_account_ref_from_zahlungskonto(default_zahlungskonto)
    check_account_name = (
        str(default_zahlungskonto.get("name", "")).strip()
        if isinstance(default_zahlungskonto, dict)
        else ""
    )
    if not accounting_type_name:
        accounting_type_name = FALLBACK_ACCOUNTING_TYPE_NAME

    payload = {
        "voucher": {
            "objectName": "Voucher",
            "mapAll": True,
            "voucherType": "VOU",
            "creditDebit": "C",
            "status": 100,
            "voucherDate": today.strftime("%d.%m.%Y"),
            "deliveryDate": today.strftime("%d.%m.%Y"),
            "paymentDeadline": deadline.strftime("%d.%m.%Y"),
            "currency": "EUR",
            "description": "RG-EXAMPLE-2026-0001",
            "supplierName": "Beispiel Lieferant GmbH",
            "taxRule": {"id": 9, "objectName": "TaxRule"},
            "taxType": "default",
            "costCentre": None,
            "document": None,
        },
        "voucherPosSave": [
            {
                "objectName": "VoucherPos",
                "mapAll": True,
                "net": False,
                "taxRate": 19.0,
                "sumGross": 119.0,
                "sumNet": 100.0,
                "comment": f"Bueromaterial ({accounting_type_name})",
                "accountingType": accounting_type,
            }
        ],
        "voucherPosDelete": None,
        "filename": None,
        "notes": {
            "how_to_choose_account_ids": (
                "Use GET /ReceiptGuidance/forExpense or /ReceiptGuidance/forRevenue and "
                "use accountDatevId from the response."
            ),
            "tax_rules_examples": {
                "expense_regelbesteuerung_typical": 9,
                "revenue_regelbesteuerung_typical": 1,
                "kleinunternehmer_revenue": 11,
            },
            "ausgewaehltes_buchunggskonto": {
                "id": accounting_type.get("id"),
                "name": accounting_type_name,
            },
            "ausgewaehltes_zahlungskonto": (
                {
                    "id": check_account.get("id"),
                    "name": check_account_name,
                }
                if check_account is not None
                else None
            ),
            "status_values_for_create": [50, 100],
            "date_format": "dd.mm.yyyy",
        },
    }

    if check_account is not None:
        payload["voucher"]["checkAccount"] = check_account

    return payload


def apply_account_assignment_to_payload(
    base_payload: dict[str, Any],
    default_buchunggskonto: dict[str, Any] | None = None,
    default_zahlungskonto: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = deepcopy(base_payload)
    default_payload = default_create_template(default_buchunggskonto, default_zahlungskonto)

    voucher = payload.get("voucher")
    if not isinstance(voucher, dict):
        voucher = {}
        payload["voucher"] = voucher

    default_voucher = default_payload["voucher"]
    for key in ("objectName", "mapAll", "voucherType", "creditDebit", "status", "currency"):
        voucher.setdefault(key, default_voucher.get(key))
    voucher.setdefault("voucherDate", today_str())
    voucher.setdefault("deliveryDate", voucher.get("voucherDate"))

    if default_zahlungskonto is not None:
        default_check_account = default_voucher.get("checkAccount")
        if isinstance(default_check_account, dict):
            voucher["checkAccount"] = default_check_account

    positions = payload.get("voucherPosSave")
    if not isinstance(positions, list) or not positions:
        positions = deepcopy(default_payload["voucherPosSave"])
        payload["voucherPosSave"] = positions

    if default_buchunggskonto is not None:
        default_accounting_type = default_payload["voucherPosSave"][0].get("accountingType")
        if isinstance(default_accounting_type, dict):
            for pos in positions:
                if isinstance(pos, dict):
                    pos["accountingType"] = deepcopy(default_accounting_type)
                    pos.setdefault("objectName", "VoucherPos")
                    pos.setdefault("mapAll", True)
    else:
        for pos in positions:
            if isinstance(pos, dict):
                pos.setdefault("objectName", "VoucherPos")
                pos.setdefault("mapAll", True)

    payload.setdefault("voucherPosDelete", None)
    payload.setdefault("filename", None)
    notes = payload.get("notes")
    if not isinstance(notes, dict):
        notes = {}
        payload["notes"] = notes
    notes.setdefault(
        "how_to_choose_account_ids",
        "Use GET /ReceiptGuidance/forExpense or /ReceiptGuidance/forRevenue and use accountDatevId from the response.",
    )
    notes.setdefault(
        "tax_rules_examples",
        {
            "expense_regelbesteuerung_typical": 9,
            "revenue_regelbesteuerung_typical": 1,
            "kleinunternehmer_revenue": 11,
        },
    )
    if default_buchunggskonto is not None:
        default_notes = default_payload.get("notes", {})
        if isinstance(default_notes, dict):
            notes["ausgewaehltes_buchunggskonto"] = default_notes.get("ausgewaehltes_buchunggskonto")
    if default_zahlungskonto is not None:
        default_notes = default_payload.get("notes", {})
        if isinstance(default_notes, dict):
            notes["ausgewaehltes_zahlungskonto"] = default_notes.get("ausgewaehltes_zahlungskonto")
    notes.setdefault("status_values_for_create", [50, 100])
    notes.setdefault("date_format", "dd.mm.yyyy")
    return payload


def write_template(
    output_path: Path,
    default_buchunggskonto: dict[str, Any] | None = None,
    default_zahlungskonto: dict[str, Any] | None = None,
    base_payload: dict[str, Any] | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(base_payload, dict):
        payload = apply_account_assignment_to_payload(
            base_payload,
            default_buchunggskonto,
            default_zahlungskonto,
        )
    else:
        payload = default_create_template(default_buchunggskonto, default_zahlungskonto)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def ensure_nested_ref(value: Any, object_name: str) -> bool:
    if not isinstance(value, dict):
        return False
    if "id" not in value:
        return False
    if not is_non_empty_string(value.get("objectName")):
        return False
    return value.get("objectName") == object_name


def validate_create_payload(
    payload: dict[str, Any],
    known_accounting_type_ids: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []

    voucher = payload.get("voucher")
    if not isinstance(voucher, dict):
        errors.append("voucher must be an object")
        return errors

    if voucher.get("objectName") != "Voucher":
        errors.append("voucher.objectName must be 'Voucher'")
    if voucher.get("mapAll") is not True:
        errors.append("voucher.mapAll must be true")

    status = voucher.get("status")
    if status not in (50, 100):
        errors.append("voucher.status must be 50 (draft) or 100 (open)")

    if voucher.get("creditDebit") not in ("C", "D"):
        errors.append("voucher.creditDebit must be 'C' (expense) or 'D' (revenue)")

    if voucher.get("voucherType") not in ("VOU", "RV"):
        errors.append("voucher.voucherType must be 'VOU' or 'RV'")

    has_supplier = ensure_nested_ref(voucher.get("supplier"), "Contact")
    has_supplier_name = is_non_empty_string(voucher.get("supplierName"))
    if not has_supplier and not has_supplier_name:
        errors.append("provide voucher.supplier (Contact) or voucher.supplierName")

    if not ensure_nested_ref(voucher.get("taxRule"), "TaxRule") and not is_non_empty_string(
        voucher.get("taxType")
    ):
        errors.append("provide voucher.taxRule (preferred) or voucher.taxType")

    if voucher.get("checkAccount") is not None and not ensure_nested_ref(
        voucher.get("checkAccount"),
        "CheckAccount",
    ):
        errors.append("voucher.checkAccount must be a CheckAccount reference if provided")

    positions = payload.get("voucherPosSave")
    if not isinstance(positions, list) or not positions:
        errors.append("voucherPosSave must be a non-empty array")
        return errors

    for idx, pos in enumerate(positions):
        prefix = f"voucherPosSave[{idx}]"
        if not isinstance(pos, dict):
            errors.append(f"{prefix} must be an object")
            continue

        if pos.get("objectName") != "VoucherPos":
            errors.append(f"{prefix}.objectName must be 'VoucherPos'")
        if pos.get("mapAll") is not True:
            errors.append(f"{prefix}.mapAll must be true")

        if not isinstance(pos.get("net"), bool):
            errors.append(f"{prefix}.net must be boolean")

        if not isinstance(pos.get("taxRate"), (int, float)):
            errors.append(f"{prefix}.taxRate must be numeric")

        has_account_datev = ensure_nested_ref(pos.get("accountDatev"), "AccountDatev")
        has_accounting_type = ensure_nested_ref(pos.get("accountingType"), "AccountingType")
        if not has_account_datev and not has_accounting_type:
            errors.append(
                f"{prefix} requires accountDatev (2.0) or accountingType (1.0); "
                "include both for compatibility"
            )
        elif has_accounting_type and known_accounting_type_ids:
            accounting_type_id = str(pos["accountingType"].get("id"))
            if accounting_type_id not in known_accounting_type_ids:
                errors.append(
                    f"{prefix}.accountingType.id={accounting_type_id} "
                    "not found in exported buchunggskonten"
                )

        has_sum_net = isinstance(pos.get("sumNet"), (int, float))
        has_sum_gross = isinstance(pos.get("sumGross"), (int, float))
        if not has_sum_net and not has_sum_gross:
            errors.append(f"{prefix} requires at least one of sumNet/sumGross")

    return errors


def normalize_create_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(raw_payload)

    payload.pop("notes", None)

    voucher = payload.setdefault("voucher", {})
    if isinstance(voucher, dict):
        voucher.setdefault("objectName", "Voucher")
        voucher.setdefault("mapAll", True)
        voucher.setdefault("voucherType", "VOU")
        voucher.setdefault("creditDebit", "C")
        voucher.setdefault("status", 100)
        voucher.setdefault("voucherDate", today_str())
        voucher.setdefault("deliveryDate", voucher.get("voucherDate"))
        voucher.setdefault("currency", "EUR")

    positions = payload.setdefault("voucherPosSave", [])
    if isinstance(positions, list):
        for pos in positions:
            if isinstance(pos, dict):
                pos.setdefault("objectName", "VoucherPos")
                pos.setdefault("mapAll", True)
                if isinstance(pos.get("net"), bool) and isinstance(pos.get("taxRate"), (int, float)):
                    tax_multiplier = 1 + (float(pos["taxRate"]) / 100.0)
                    if pos["net"] and isinstance(pos.get("sumNet"), (int, float)) and "sumGross" not in pos:
                        pos["sumGross"] = round(float(pos["sumNet"]) * tax_multiplier, 2)
                    if (not pos["net"]) and isinstance(pos.get("sumGross"), (int, float)) and "sumNet" not in pos:
                        pos["sumNet"] = round(float(pos["sumGross"]) / tax_multiplier, 2)

    return payload


def load_create_input(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(
            f"Input file not found: {path}. Create one with: python -m src.sevdesk template"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Create input JSON must be an object")
    return payload


def first_object_from_response(response_payload: dict[str, Any]) -> dict[str, Any] | None:
    objects = response_payload.get("objects")
    if isinstance(objects, list) and objects:
        first = objects[0] if isinstance(objects[0], dict) else None
    elif isinstance(objects, dict):
        first = objects
    else:
        return None
    return first


def print_create_result(response_payload: dict[str, Any], *, operation: str = "created") -> None:
    first = first_object_from_response(response_payload)
    print(f"Beleg {operation} successfully.")
    if isinstance(first, dict):
        print(f"id: {first.get('id', '-')}")
        print(f"status: {first.get('status', '-')}")
        print(f"description: {first.get('description', '-')}")
        print(f"voucherDate: {first.get('voucherDate', '-')}")
        print(f"sumGross: {first.get('sumGross', '-')}")
