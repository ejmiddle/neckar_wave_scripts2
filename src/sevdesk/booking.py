from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .api import book_voucher, create_voucher, request_voucher_by_id, request_voucher_positions
from .voucher import (
    build_voucher_accounting_type_update_payload,
    build_voucher_accounting_type_update_payload_for_positions,
    extract_voucher_accounting_type_ids,
    first_object_from_response,
)


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


def update_voucher_accounting_type(
    base_url: str,
    token: str,
    voucher_id: str,
    accounting_type: dict[str, Any] | None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    target_voucher_id = str(voucher_id).strip()
    if not target_voucher_id:
        raise RuntimeError("Target Beleg id is missing.")

    target_accounting_type_id = str(
        (accounting_type or {}).get("id", "")
    ).strip()
    if not target_accounting_type_id:
        raise RuntimeError("Selected accounting type has no id.")

    existing = request_voucher_by_id(base_url, token, target_voucher_id)
    if existing is None:
        raise RuntimeError(
            f"Safety check failed: target Beleg id={target_voucher_id} not found before update."
        )

    before_update = str(existing.get("update", "")).strip()
    before_accounting_type_ids = extract_voucher_accounting_type_ids(existing)

    if before_accounting_type_ids and set(before_accounting_type_ids) == {target_accounting_type_id}:
        return {
            "voucher_id": target_voucher_id,
            "before_update": before_update,
            "after_update": before_update,
            "before_accounting_type_ids": before_accounting_type_ids,
            "after_accounting_type_ids": before_accounting_type_ids,
            "target_accounting_type_id": target_accounting_type_id,
            "change_status": "skipped",
            "booking_payload": None,
            "response_payload": None,
            "response_object": None,
            "updated_voucher": existing,
        }

    booking_payload = build_voucher_accounting_type_update_payload(existing, accounting_type)
    if dry_run:
        return {
            "voucher_id": target_voucher_id,
            "before_update": before_update,
            "after_update": before_update,
            "before_accounting_type_ids": before_accounting_type_ids,
            "after_accounting_type_ids": before_accounting_type_ids,
            "target_accounting_type_id": target_accounting_type_id,
            "change_status": "dry_run",
            "booking_payload": booking_payload,
            "response_payload": None,
            "response_object": None,
            "updated_voucher": existing,
        }

    response_payload = create_voucher(base_url, token, booking_payload)

    updated = request_voucher_by_id(base_url, token, target_voucher_id)
    if updated is None:
        raise RuntimeError(
            f"Post-update verification failed: could not load Beleg id={target_voucher_id}."
        )

    after_update = str(updated.get("update", "")).strip()
    after_accounting_type_ids = extract_voucher_accounting_type_ids(updated)

    if not after_accounting_type_ids:
        raise RuntimeError(
            "Post-update verification failed: could not read any accountingType values from the voucher."
        )
    if set(after_accounting_type_ids) != {target_accounting_type_id}:
        raise RuntimeError(
            "Post-update verification failed: accountingType did not match the selected target "
            f"(expected {target_accounting_type_id!r}, got {after_accounting_type_ids!r})."
        )
    if before_update and after_update and before_update == after_update:
        raise RuntimeError(
            "Post-update verification failed: voucher update timestamp did not change "
            f"(still {after_update})."
        )

    return {
        "voucher_id": target_voucher_id,
        "before_update": before_update,
        "after_update": after_update,
        "before_accounting_type_ids": before_accounting_type_ids,
        "after_accounting_type_ids": after_accounting_type_ids,
        "target_accounting_type_id": target_accounting_type_id,
        "change_status": "success",
        "booking_payload": booking_payload,
        "response_payload": response_payload,
        "response_object": first_object_from_response(response_payload),
        "updated_voucher": updated,
    }


def update_voucher_accounting_type_for_positions(
    base_url: str,
    token: str,
    voucher_id: str,
    accounting_type: dict[str, Any] | None,
    voucher_position_ids: list[str],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    target_voucher_id = str(voucher_id).strip()
    if not target_voucher_id:
        raise RuntimeError("Target Beleg id is missing.")

    target_accounting_type_id = str((accounting_type or {}).get("id", "")).strip()
    if not target_accounting_type_id:
        raise RuntimeError("Selected accounting type has no id.")

    target_position_ids = {
        str(position_id).strip() for position_id in voucher_position_ids if str(position_id).strip()
    }
    if not target_position_ids:
        raise RuntimeError("No booking positions selected.")

    existing = request_voucher_by_id(base_url, token, target_voucher_id)
    if existing is None:
        raise RuntimeError(
            f"Safety check failed: target Beleg id={target_voucher_id} not found before update."
        )

    existing_positions = request_voucher_positions(
        base_url,
        token,
        filters={
            "voucher[id]": target_voucher_id,
            "voucher[objectName]": "Voucher",
            "depth": "1",
        },
    )
    existing = {**existing, "voucherPos": existing_positions}

    before_update = str(existing.get("update", "")).strip()
    before_position_accounting_type_ids: dict[str, str] = {}
    for position in existing_positions:
        position_id = str(position.get("id", "")).strip()
        accounting_type_value = position.get("accountingType")
        if (
            position_id in target_position_ids
            and isinstance(accounting_type_value, dict)
            and str(accounting_type_value.get("id", "")).strip()
        ):
            before_position_accounting_type_ids[position_id] = str(accounting_type_value.get("id", "")).strip()

    if (
        before_position_accounting_type_ids
        and set(before_position_accounting_type_ids.keys()) == target_position_ids
        and set(before_position_accounting_type_ids.values()) == {target_accounting_type_id}
    ):
        return {
            "voucher_id": target_voucher_id,
            "updated_position_ids": sorted(target_position_ids),
            "before_update": before_update,
            "after_update": before_update,
            "before_position_accounting_type_ids": before_position_accounting_type_ids,
            "after_position_accounting_type_ids": before_position_accounting_type_ids,
            "target_accounting_type_id": target_accounting_type_id,
            "change_status": "skipped",
            "booking_payload": None,
            "response_payload": None,
            "response_object": None,
            "updated_voucher": existing,
        }

    booking_payload = build_voucher_accounting_type_update_payload_for_positions(
        existing,
        accounting_type,
        sorted(target_position_ids),
    )
    if dry_run:
        return {
            "voucher_id": target_voucher_id,
            "updated_position_ids": sorted(target_position_ids),
            "before_update": before_update,
            "after_update": before_update,
            "before_position_accounting_type_ids": before_position_accounting_type_ids,
            "after_position_accounting_type_ids": before_position_accounting_type_ids,
            "target_accounting_type_id": target_accounting_type_id,
            "change_status": "dry_run",
            "booking_payload": booking_payload,
            "response_payload": None,
            "response_object": None,
            "updated_voucher": existing,
        }

    response_payload = create_voucher(base_url, token, booking_payload)

    updated = request_voucher_by_id(base_url, token, target_voucher_id)
    if updated is None:
        raise RuntimeError(
            f"Post-update verification failed: could not load Beleg id={target_voucher_id}."
        )
    updated_positions = request_voucher_positions(
        base_url,
        token,
        filters={
            "voucher[id]": target_voucher_id,
            "voucher[objectName]": "Voucher",
            "depth": "1",
        },
    )
    updated = {**updated, "voucherPos": updated_positions}

    after_update = str(updated.get("update", "")).strip()
    after_position_accounting_type_ids: dict[str, str] = {}
    for position in updated_positions:
        position_id = str(position.get("id", "")).strip()
        accounting_type_value = position.get("accountingType")
        if (
            position_id in target_position_ids
            and isinstance(accounting_type_value, dict)
            and str(accounting_type_value.get("id", "")).strip()
        ):
            after_position_accounting_type_ids[position_id] = str(accounting_type_value.get("id", "")).strip()

    if set(after_position_accounting_type_ids.keys()) != target_position_ids:
        raise RuntimeError(
            "Post-update verification failed: could not read accountingType values for all selected positions."
        )
    if set(after_position_accounting_type_ids.values()) != {target_accounting_type_id}:
        raise RuntimeError(
            "Post-update verification failed: selected booking positions did not match the selected target "
            f"(expected {target_accounting_type_id!r}, got {after_position_accounting_type_ids!r})."
        )
    if before_update and after_update and before_update == after_update:
        raise RuntimeError(
            "Post-update verification failed: voucher update timestamp did not change "
            f"(still {after_update})."
        )

    return {
        "voucher_id": target_voucher_id,
        "updated_position_ids": sorted(target_position_ids),
        "before_update": before_update,
        "after_update": after_update,
        "before_position_accounting_type_ids": before_position_accounting_type_ids,
        "after_position_accounting_type_ids": after_position_accounting_type_ids,
        "target_accounting_type_id": target_accounting_type_id,
        "change_status": "success",
        "booking_payload": booking_payload,
        "response_payload": response_payload,
        "response_object": first_object_from_response(response_payload),
        "updated_voucher": updated,
    }
