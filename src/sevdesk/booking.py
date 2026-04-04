from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .api import book_voucher, request_voucher_by_id
from .voucher import first_object_from_response


def parse_amount(value: object, field_name: str) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise RuntimeError(f"Could not parse {field_name}={value!r} as amount.") from exc


def _parse_booking_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None

    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass

    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _voucher_booking_date(existing: dict[str, Any]) -> str:
    for key in ("voucherDate", "invoiceDate"):
        raw_value = str(existing.get(key, "")).strip()
        if raw_value:
            return raw_value
    return date.today().strftime("%d.%m.%Y")


def book_voucher_to_check_account(
    base_url: str,
    token: str,
    voucher_id: str,
    check_account_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    target_voucher_id = str(voucher_id).strip()
    if not target_voucher_id:
        raise RuntimeError("Target Beleg id is missing.")

    existing = request_voucher_by_id(base_url, token, target_voucher_id)
    if existing is None:
        raise RuntimeError(
            f"Safety check failed: target Beleg id={target_voucher_id} not found before booking."
        )

    before_status = str(existing.get("status", "")).strip()
    before_paid_amount = parse_amount(existing.get("paidAmount"), "paidAmount")
    amount_to_book = parse_amount(existing.get("sumGross"), "sumGross")
    if amount_to_book <= 0:
        raise RuntimeError(
            f"Cannot book voucher id={target_voucher_id}: invalid sumGross={existing.get('sumGross')!r}."
        )
    if before_status == "1000" and before_paid_amount >= amount_to_book:
        raise RuntimeError(
            f"Voucher id={target_voucher_id} is already paid "
            f"(status={before_status}, paidAmount={before_paid_amount}). "
            "To change the payment account, reset the voucher to open first and then book it again."
        )

    selected_check_account_id = str(check_account_id).strip()
    if not selected_check_account_id:
        raise RuntimeError("Selected zahlungskonto has no id.")

    booking_date = _voucher_booking_date(existing)
    check_account_id_payload: int | str
    try:
        check_account_id_payload = int(selected_check_account_id)
    except ValueError:
        check_account_id_payload = selected_check_account_id

    booking_payload = {
        "amount": amount_to_book,
        "date": booking_date,
        "type": "FULL_PAYMENT",
        "checkAccount": {
            "id": check_account_id_payload,
            "objectName": "CheckAccount",
        },
    }

    if dry_run:
        return {
            "voucher_id": target_voucher_id,
            "before_status": before_status,
            "after_status": before_status,
            "before_paid_amount": before_paid_amount,
            "after_paid_amount": before_paid_amount,
            "pay_date": booking_date,
            "booking_payload": booking_payload,
            "response_payload": None,
            "response_object": None,
            "updated_voucher": existing,
        }

    response_payload = book_voucher(base_url, token, target_voucher_id, booking_payload)

    updated = request_voucher_by_id(base_url, token, target_voucher_id)
    if updated is None:
        raise RuntimeError(
            f"Post-booking verification failed: could not load Beleg id={target_voucher_id}."
        )

    after_status = str(updated.get("status", "")).strip()
    after_paid_amount = parse_amount(updated.get("paidAmount"), "paidAmount")
    after_pay_date = str(updated.get("payDate", "")).strip()
    if _parse_booking_date(after_pay_date) != _parse_booking_date(booking_date):
        raise RuntimeError(
            "Post-booking verification failed: payDate did not match the Belegdatum "
            f"(expected {booking_date!r}, got {after_pay_date!r})."
        )

    if before_status == after_status and after_paid_amount <= before_paid_amount:
        raise RuntimeError(
            "Post-booking verification failed: status and paid amount did not change "
            f"(status={after_status!r}, paidAmount={after_paid_amount})."
        )

    return {
        "voucher_id": target_voucher_id,
        "before_status": before_status,
        "after_status": after_status,
        "before_paid_amount": before_paid_amount,
        "after_paid_amount": after_paid_amount,
        "pay_date": after_pay_date,
        "booking_payload": booking_payload,
        "response_payload": response_payload,
        "response_object": first_object_from_response(response_payload),
        "updated_voucher": updated,
    }
