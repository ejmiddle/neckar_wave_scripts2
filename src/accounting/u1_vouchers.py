from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import re
from typing import Any

from src.accounting.common import format_sevdesk_date, parse_amount_value
from src.accounting.master_data import (
    format_accounting_type_row,
    format_tax_rule_row,
    load_stored_tax_rules,
)
from src.sevdesk.constants import (
    KRANKENKASSE_TEMPLATE_PATH,
    KRANKENKASSE_U1_TEMPLATE_PATH,
    LOHN_TEMPLATE_PATH,
    STEUER_LOHN_TEMPLATE_PATH,
)
from src.sevdesk.voucher import normalize_create_payload

TEMPLATE_PATH = KRANKENKASSE_U1_TEMPLATE_PATH
LOHNVOUCHER_TEMPLATE_PATH = LOHN_TEMPLATE_PATH
DEFAULT_ACCOUNTING_TYPE_NAME = "Krankenkasse"
DEFAULT_LOHN_ACCOUNTING_TYPE_NAME = "Lohn / Gehalt"
DEFAULT_STEUER_ACCOUNTING_TYPE_NAME = "Pauschale Steuer für Aushilfen"
FALLBACK_STEUER_ACCOUNTING_TYPE_NAMES = (
    "Pauschale Steuern für Minijobber",
    "Verbindlichkeiten aus Lohn- und Kirchensteuer",
)
DEFAULT_NON_TAXABLE_STEUER_RULE_NAME = "Nicht Steuerbar (Steuer)"
PLACEHOLDER_RE = re.compile(r"^\{\{\s*([a-zA-Z0-9_]+)\s*\}\}$")


def _load_template(template_path: Path) -> dict[str, Any]:
    if not template_path.exists():
        raise RuntimeError(f"Voucher template not found: {template_path}")

    payload = json.loads(template_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Voucher template must be a JSON object")
    return payload


def _render_template_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = PLACEHOLDER_RE.match(value.strip())
        if match:
            return context.get(match.group(1))
        return value
    if isinstance(value, list):
        return [_render_template_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_template_value(item, context) for key, item in value.items()}
    return value


def _render_template_payload(template_path: Path, context: dict[str, Any]) -> dict[str, Any]:
    template = _load_template(template_path)
    rendered = _render_template_value(template, context)
    if not isinstance(rendered, dict):
        raise RuntimeError(f"Rendered voucher template is not an object: {template_path}")
    return rendered


def _active_accounting_types(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not rows:
        return []
    active_rows = [
        row
        for row in rows
        if bool(row.get("active", True)) and str(row.get("status", "100")) == "100"
    ]
    return active_rows or rows


def _select_u1_accounting_type(rows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    candidates = _active_accounting_types(rows)

    for row in candidates:
        formatted = format_accounting_type_row(row)
        if formatted["name"].strip().lower() == DEFAULT_ACCOUNTING_TYPE_NAME.lower():
            return {"id": formatted["id"], "objectName": "AccountingType"}

    return None


def _select_accounting_type_by_names(
    rows: list[dict[str, Any]] | None,
    *,
    exact_names: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    candidates = _active_accounting_types(rows)

    lowered_exact_names = {name.strip().lower() for name in exact_names if name.strip()}
    for row in candidates:
        formatted = format_accounting_type_row(row)
        name = formatted["name"].strip().lower()
        if name in lowered_exact_names:
            return {"id": formatted["id"], "objectName": "AccountingType"}

    return None


def _select_tax_rule_by_names(
    rows: list[dict[str, Any]] | None,
    *,
    exact_names: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    if not rows:
        return None

    lowered_exact_names = {name.strip().lower() for name in exact_names if name.strip()}
    for row in rows:
        formatted = format_tax_rule_row(row)
        name = formatted["name"].strip().lower()
        if name in lowered_exact_names:
            return {"id": formatted["id"], "objectName": "TaxRule"}

    return None


def _krankenkasse_prefix(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return "U1"
    return cleaned[:5]


def build_u1_voucher_description(belegdatum: date, krankenkasse: str) -> str:
    return f"Erstattung U1 {belegdatum.strftime('%m-%y')} {_krankenkasse_prefix(krankenkasse)}".strip()


def build_lohnkosten_voucher_description(kind: str, belegdatum: date) -> str:
    return f"{kind} {belegdatum.strftime('%m-%y')}".strip()


def _empty_result_entry(
    *,
    file_name: str,
    page_number: int,
    page_count: int,
    extracted: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    return {
        "source_type": "U1",
        "file_name": file_name,
        "page_number": page_number,
        "page_count": page_count,
        "extracted": extracted,
        "error": error,
    }


def _missing_master_data_error(*, entry_label: str, expected_names: tuple[str, ...]) -> str:
    joined_names = ", ".join(name for name in expected_names if str(name).strip())
    return (
        f"Missing exact master-data match for {entry_label}. "
        f"Expected one of: {joined_names}."
    )


def _clean_voucher_for_create(voucher: dict[str, Any], *, has_document: bool = False) -> dict[str, Any]:
    cleaned = deepcopy(voucher)
    for field_name in ("id", "create", "update", "sevClient", "createUser"):
        cleaned.pop(field_name, None)
    if not has_document:
        cleaned.pop("document", None)
    return cleaned


def _build_voucher_payload(
    *,
    template_path: Path,
    template_context: dict[str, Any],
    belegdatum: date,
    description: str,
    amount: float,
    accounting_type: dict[str, Any],
    supplier_source: str,
    voucher_source: str,
    result_disdar: dict[str, Any] | None = None,
    has_document: bool = False,
    tax_rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    voucher_date = format_sevdesk_date(belegdatum)
    payment_deadline = format_sevdesk_date(belegdatum + timedelta(days=14))
    payload = _render_template_payload(template_path, template_context)
    voucher = payload.get("voucher")
    if isinstance(voucher, dict):
        voucher_object = voucher
    elif isinstance(payload, dict):
        # Some template files are stored as flat voucher objects instead of a nested
        # {"voucher": {...}} wrapper. Treat the root object as the voucher in that case.
        voucher_object = payload
        payload = {"voucher": voucher_object}
    else:
        raise RuntimeError(f"Voucher template at {template_path} is not a valid voucher object")

    voucher_object.update(
        {
            "objectName": "Voucher",
            "mapAll": True,
            "voucherType": "VOU",
            "creditDebit": "C",
            "voucherDate": voucher_date,
            "deliveryDate": voucher_date,
            "paymentDeadline": payment_deadline,
            "currency": "EUR",
            "payDate": voucher_date,
            "description": description,
            "status": 50,
            "sumNet": amount,
            "sumTax": 0.0,
            "sumGross": amount,
            "sumNetAccounting": amount,
            "sumTaxAccounting": 0.0,
            "sumGrossAccounting": amount,
            "paidAmount": amount,
        }
    )
    if result_disdar is not None:
        voucher_object["resultDisdar"] = json.dumps(result_disdar, ensure_ascii=False)
    if tax_rule is not None:
        voucher_object["taxRule"] = deepcopy(tax_rule)
    voucher = _clean_voucher_for_create(voucher_object, has_document=has_document)

    voucher_position = {
        "objectName": "VoucherPos",
        "mapAll": True,
        "net": False,
        "taxRate": 0.0,
        "sumGross": amount,
        "sumNet": amount,
        "comment": description,
        "accountingType": deepcopy(accounting_type),
    }
    if tax_rule is not None:
        voucher_position["taxRule"] = deepcopy(tax_rule)

    voucher_payload = {
        "voucher": voucher,
        "voucherPosSave": [voucher_position],
        "voucherPosDelete": None,
        "filename": None,
        "notes": {
            "source": voucher_source,
            "template_path": str(template_path),
            "supplier_source": supplier_source,
            "belegdatum": voucher_date,
            "payment_deadline": payment_deadline,
            "description": description,
            "amount": amount,
            "result_disdar": result_disdar,
        },
    }
    return normalize_create_payload(voucher_payload)


def build_u1_voucher_payloads(
    extraction_results: list[dict[str, Any]],
    belegdatum: date,
    *,
    accounting_type_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    accounting_type = _select_u1_accounting_type(accounting_type_rows)

    payloads: list[dict[str, Any]] = []
    for file_result in extraction_results:
        file_name = str(file_result.get("file_name", "")).strip() or "unknown.pdf"
        pages = file_result.get("pages")
        if not isinstance(pages, list):
            continue

        for page_result in pages:
            page_number = int(page_result.get("page_number", 0) or 0)
            page_count = int(page_result.get("page_count", 0) or 0)
            page_pdf_name = str(page_result.get("page_pdf_name", "")).strip() or (
                f"{file_name}_seite_{page_number}.pdf"
            )
            page_pdf_bytes = page_result.get("page_pdf_bytes")
            extracted = page_result.get("extracted")
            if not isinstance(extracted, dict):
                payloads.append(
                    _empty_result_entry(
                        file_name=file_name,
                        page_number=page_number,
                        page_count=page_count,
                        extracted={},
                        error="Missing extracted page payload.",
                    )
                )
                continue

            amount = parse_amount_value(extracted.get("erstattungsbeitrag"))
            krankenkasse = str(extracted.get("krankenkasse") or "").strip()
            if amount is None or not krankenkasse:
                payloads.append(
                    _empty_result_entry(
                        file_name=file_name,
                        page_number=page_number,
                        page_count=page_count,
                        extracted=extracted,
                        error="Missing Erstattungsbeitrag or Krankenkasse in extracted page data.",
                    )
                )
                continue
            if accounting_type is None:
                payloads.append(
                    _empty_result_entry(
                        file_name=file_name,
                        page_number=page_number,
                        page_count=page_count,
                        extracted=extracted,
                        error=_missing_master_data_error(
                            entry_label="U1 accounting type",
                            expected_names=(DEFAULT_ACCOUNTING_TYPE_NAME,),
                        ),
                    )
                )
                continue

            description = build_u1_voucher_description(belegdatum, krankenkasse)
            payloads.append(
                {
                    "source_type": "U1",
                    "file_name": file_name,
                    "page_number": page_number,
                    "page_count": page_count,
                    "page_pdf_name": page_pdf_name,
                    "page_pdf_bytes": page_pdf_bytes if isinstance(page_pdf_bytes, bytes) else None,
                    "extracted": extracted,
                    "description": description,
                    "voucher_payload": _build_voucher_payload(
                        template_path=TEMPLATE_PATH,
                        template_context={
                            "voucher_id": None,
                            "created_at": None,
                            "updated_at": None,
                            "belegdatum": format_sevdesk_date(belegdatum),
                            "voucher_name": description,
                            "document_id": None,
                            "result_disdar_json": json.dumps(
                                {
                                    "source_type": "U1",
                                    "file_name": file_name,
                                    "page_number": page_number,
                                    "page_count": page_count,
                                    "extracted": extracted,
                                },
                                ensure_ascii=False,
                            ),
                            "pay_date": format_sevdesk_date(belegdatum),
                            "sum_net": amount,
                            "sum_tax": 0.0,
                            "sum_gross": amount,
                            "paid_amount": amount,
                            "payment_deadline": format_sevdesk_date(belegdatum + timedelta(days=14)),
                        },
                        belegdatum=belegdatum,
                        description=description,
                        amount=amount,
                        accounting_type=deepcopy(accounting_type),
                        supplier_source="U1",
                        voucher_source="u1_pdf",
                        result_disdar={
                            "source_type": "U1",
                            "file_name": file_name,
                            "page_number": page_number,
                            "page_count": page_count,
                            "extracted": extracted,
                        },
                        has_document=False,
                    ),
                }
            )

    return payloads


def build_lohnkosten_voucher_payloads(
    extraction_results: list[dict[str, Any]],
    belegdatum: date,
    *,
    accounting_type_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    tax_rule_rows = load_stored_tax_rules()

    payroll_type = _select_accounting_type_by_names(
        accounting_type_rows,
        exact_names=("Lohn / Gehalt",),
    )
    krankenkasse_type = _select_accounting_type_by_names(
        accounting_type_rows,
        exact_names=("Krankenkasse",),
    )
    steuer_type = _select_accounting_type_by_names(
        accounting_type_rows,
        exact_names=(DEFAULT_STEUER_ACCOUNTING_TYPE_NAME, *FALLBACK_STEUER_ACCOUNTING_TYPE_NAMES),
    )
    steuer_tax_rule = _select_tax_rule_by_names(
        tax_rule_rows,
        exact_names=(DEFAULT_NON_TAXABLE_STEUER_RULE_NAME,),
    )

    for file_result in extraction_results:
        file_name = str(file_result.get("file_name", "")).strip() or "unknown.pdf"
        extracted = file_result.get("extracted")
        if not isinstance(extracted, dict):
            payloads.append(
                {
                    "source_type": "Lohnkosten",
                    "file_name": file_name,
                    "error": "Missing extracted payroll payload.",
                }
            )
            continue

        value_map = [
            {
                "kind": "Lohnüberweisungen",
                "description": build_lohnkosten_voucher_description("Lohn", belegdatum),
                "amount": parse_amount_value(extracted.get("gesamtsumme_lohnueberweisungen")),
                "template_path": LOHNVOUCHER_TEMPLATE_PATH,
                "accounting_type": payroll_type,
                "expected_accounting_type_names": ("Lohn / Gehalt",),
                "supplier_source": "Lohnkosten",
                "voucher_source": "lohnkosten_pdf",
            },
            {
                "kind": "Krankenkasse",
                "description": build_lohnkosten_voucher_description("Krankenkasse", belegdatum),
                "amount": parse_amount_value(extracted.get("zwischensumme_krankenkasse")),
                "template_path": KRANKENKASSE_TEMPLATE_PATH,
                "accounting_type": krankenkasse_type,
                "expected_accounting_type_names": ("Krankenkasse",),
                "supplier_source": "Lohnkosten",
                "voucher_source": "lohnkosten_pdf",
            },
            {
                "kind": "Steuer Lohn",
                "description": build_lohnkosten_voucher_description("Steuer Lohn", belegdatum),
                "amount": parse_amount_value(extracted.get("zwischensumme_finanzamt")),
                "template_path": STEUER_LOHN_TEMPLATE_PATH,
                "accounting_type": steuer_type,
                "tax_rule": steuer_tax_rule,
                "expected_accounting_type_names": (
                    DEFAULT_STEUER_ACCOUNTING_TYPE_NAME,
                    *FALLBACK_STEUER_ACCOUNTING_TYPE_NAMES,
                ),
                "supplier_source": "Lohnkosten",
                "voucher_source": "lohnkosten_pdf",
            },
        ]

        for item in value_map:
            amount = item["amount"]
            if amount is None:
                payloads.append(
                    {
                        "source_type": "Lohnkosten",
                        "file_name": file_name,
                        "kind": item["kind"],
                        "description": item["description"],
                        "error": f"Missing amount for {item['kind']}.",
                        "extracted": extracted,
                    }
                )
                continue
            if item["accounting_type"] is None:
                payloads.append(
                    {
                        "source_type": "Lohnkosten",
                        "file_name": file_name,
                        "kind": item["kind"],
                        "description": item["description"],
                        "error": _missing_master_data_error(
                            entry_label=f"{item['kind']} accounting type",
                            expected_names=item["expected_accounting_type_names"],
                        ),
                        "extracted": extracted,
                    }
                )
                continue
            if item["kind"] == "Steuer Lohn" and item.get("tax_rule") is None:
                payloads.append(
                    {
                        "source_type": "Lohnkosten",
                        "file_name": file_name,
                        "kind": item["kind"],
                        "description": item["description"],
                        "error": _missing_master_data_error(
                            entry_label="Steuer Lohn tax rule",
                            expected_names=(DEFAULT_NON_TAXABLE_STEUER_RULE_NAME,),
                        ),
                        "extracted": extracted,
                    }
                )
                continue

            result_disdar = {
                "source_type": "Lohnkosten",
                "kind": item["kind"],
                "file_name": file_name,
                "extracted": extracted,
            }
            voucher_payload = _build_voucher_payload(
                template_path=item["template_path"],
                template_context={
                    "voucher_id": None,
                    "created_at": None,
                    "updated_at": None,
                    "belegdatum": format_sevdesk_date(belegdatum),
                    "voucher_name": item["description"],
                    "document_id": None,
                    "result_disdar_json": json.dumps(result_disdar, ensure_ascii=False),
                    "pay_date": format_sevdesk_date(belegdatum),
                    "sum_net": amount,
                    "sum_tax": 0.0,
                    "sum_gross": amount,
                    "paid_amount": amount,
                    "payment_deadline": format_sevdesk_date(belegdatum + timedelta(days=14)),
                },
                belegdatum=belegdatum,
                description=item["description"],
                amount=amount,
                accounting_type=item["accounting_type"],
                supplier_source=item["supplier_source"],
                voucher_source=item["voucher_source"],
                result_disdar=result_disdar,
                has_document=False,
                tax_rule=item.get("tax_rule"),
            )
            payloads.append(
                {
                    "source_type": "Lohnkosten",
                    "file_name": file_name,
                    "kind": item["kind"],
                    "description": item["description"],
                    "amount": amount,
                    "voucher_payload": voucher_payload,
                }
            )

    return payloads
