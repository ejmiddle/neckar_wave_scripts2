from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from io import StringIO
from typing import BinaryIO

PAYMENT_PART_RE = re.compile(r"([^:,;]+):\s*(-?\d+(?:,\d+)?)")
CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


@dataclass(frozen=True)
class InvoicePaymentAnalysis:
    row_count: int
    payment_totals: dict[str, Decimal]
    corrected_payment_totals: dict[str, Decimal]
    all_payment_total: Decimal
    cash_total: Decimal
    corrected_cash_total: Decimal
    sumup_total: Decimal
    corrected_sumup_total: Decimal
    sumup_storno_correction_total: Decimal
    sumup_storno_correction_rows: list[dict[str, object]]
    sumup_storno_rows: list[dict[str, object]]


def _parse_decimal(value: object) -> Decimal:
    text = str(value or "").strip().replace(".", "").replace(",", ".")
    return Decimal(text) if text else Decimal("0")


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _is_date(value: object) -> bool:
    text = str(value or "").strip()
    return len(text) >= 10 and text[:4].isdigit()


def _parse_payment_parts(value: object) -> list[tuple[str, Decimal]]:
    return [
        (match.group(1).strip(), _parse_decimal(match.group(2)))
        for match in PAYMENT_PART_RE.finditer(str(value or ""))
    ]


def _decode_csv_bytes(csv_bytes: bytes) -> str:
    for encoding in CSV_ENCODINGS:
        try:
            return csv_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return csv_bytes.decode(CSV_ENCODINGS[-1], errors="replace")


def analyze_invoice_payment_csv(csv_source: bytes | str | BinaryIO) -> InvoicePaymentAnalysis:
    if isinstance(csv_source, bytes):
        csv_text = _decode_csv_bytes(csv_source)
    elif isinstance(csv_source, str):
        csv_text = csv_source
    else:
        csv_text = _decode_csv_bytes(csv_source.read())

    rows = list(csv.DictReader(StringIO(csv_text), delimiter=";"))
    required_columns = {"Rechnungsnummer", "Zahlungsarten", "Bezahlt am", "Storniert am"}
    missing_columns = required_columns.difference(rows[0].keys() if rows else set())
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing required invoice CSV column(s): {missing}")

    payment_totals: dict[str, Decimal] = {}
    row_sumup_amounts: dict[str, Decimal] = {}
    sumup_storno_rows: list[dict[str, object]] = []

    for row in rows:
        paid_value = str(row.get("Bezahlt am") or "").strip()
        cancelled_at = str(row.get("Storniert am") or "").strip()
        is_clean_paid = _is_date(paid_value) and not cancelled_at
        is_cancelled_or_correction = bool(cancelled_at) or paid_value == "storniert"
        sumup_amount = Decimal("0")

        for payment_name, amount in _parse_payment_parts(row.get("Zahlungsarten")):
            payment_totals[payment_name] = payment_totals.get(payment_name, Decimal("0")) + amount
            if payment_name.lower() == "sumup":
                sumup_amount += amount

        row_sumup_amounts[str(row.get("Rechnungsnummer") or "").strip()] = _round_money(sumup_amount)
        if sumup_amount and (is_cancelled_or_correction or not is_clean_paid):
            sumup_storno_rows.append(
                {
                    "Rechnungsnummer": row.get("Rechnungsnummer", ""),
                    "Rechnungsdatum": row.get("Rechnungsdatum", ""),
                    "Zahlungsarten": row.get("Zahlungsarten", ""),
                    "SumUp Betrag": _round_money(sumup_amount),
                    "Bezahlt am": paid_value,
                    "Storniert am": cancelled_at,
                    "Retourgebucht wegen": row.get("Retourgebucht wegen", ""),
                }
            )

    rounded_payment_totals = {
        payment_name: _round_money(amount)
        for payment_name, amount in sorted(payment_totals.items(), key=lambda item: item[0].lower())
    }
    all_payment_total = _round_money(sum(rounded_payment_totals.values(), Decimal("0")))
    sumup_total = _round_money(
        sum(amount for payment_name, amount in payment_totals.items() if payment_name.lower() == "sumup")
    )
    cash_total = _round_money(
        sum(
            amount
            for payment_name, amount in payment_totals.items()
            if payment_name.lower() == "barzahlung"
        )
    )
    rows_by_invoice_number = {
        str(row.get("Rechnungsnummer") or "").strip(): row
        for row in rows
        if str(row.get("Rechnungsnummer") or "").strip()
    }
    sumup_storno_correction_rows: list[dict[str, object]] = []
    for row in rows:
        invoice_number = str(row.get("Rechnungsnummer") or "").strip()
        sumup_amount = row_sumup_amounts.get(invoice_number, Decimal("0"))
        if sumup_amount >= 0:
            continue

        referenced_invoice_number = str(row.get("Interne Rechnungsreferenz") or "").strip()
        referenced_row = rows_by_invoice_number.get(referenced_invoice_number, {})
        storniert_am = str(row.get("Storniert am") or "").strip()
        referenced_storniert_am = str(referenced_row.get("Storniert am") or "").strip()
        if not storniert_am and not referenced_storniert_am:
            continue

        sumup_storno_correction_rows.append(
            {
                "Rechnungsnummer": invoice_number,
                "Rechnungsdatum": row.get("Rechnungsdatum", ""),
                "Zahlungsarten": row.get("Zahlungsarten", ""),
                "SumUp Betrag": sumup_amount,
                "Bezahlt am": row.get("Bezahlt am", ""),
                "Storniert am": storniert_am,
                "Referenz": referenced_invoice_number,
                "Referenz Storniert am": referenced_storniert_am,
                "Retourgebucht wegen": row.get("Retourgebucht wegen", ""),
            }
        )
    sumup_storno_correction_total = _round_money(
        abs(sum(Decimal(str(row["SumUp Betrag"])) for row in sumup_storno_correction_rows))
    )
    corrected_payment_totals = dict(rounded_payment_totals)
    for payment_name, amount in list(corrected_payment_totals.items()):
        if payment_name.lower() == "barzahlung":
            corrected_payment_totals[payment_name] = _round_money(
                amount - sumup_storno_correction_total
            )
        if payment_name.lower() == "sumup":
            corrected_payment_totals[payment_name] = _round_money(
                amount + sumup_storno_correction_total
            )

    corrected_cash_total = _round_money(cash_total - sumup_storno_correction_total)
    corrected_sumup_total = _round_money(sumup_total + sumup_storno_correction_total)

    return InvoicePaymentAnalysis(
        row_count=len(rows),
        payment_totals=rounded_payment_totals,
        corrected_payment_totals=corrected_payment_totals,
        all_payment_total=all_payment_total,
        cash_total=cash_total,
        corrected_cash_total=corrected_cash_total,
        sumup_total=sumup_total,
        corrected_sumup_total=corrected_sumup_total,
        sumup_storno_correction_total=sumup_storno_correction_total,
        sumup_storno_correction_rows=sumup_storno_correction_rows,
        sumup_storno_rows=sumup_storno_rows,
    )
