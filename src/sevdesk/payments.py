from __future__ import annotations

from typing import Any

from src.logging_config import logger

from .api import (
    create_check_account_transaction,
    request_check_account_transaction_by_id,
    transfer_check_account,
    update_check_account_transaction,
)


def _transfer_payment_purpose(existing: dict[str, Any], source_account_name: str) -> str:
    original_purpose = str(existing.get("paymtPurpose", "")).strip()
    if original_purpose:
        return f"Transfer von {original_purpose}"
    return f"Transfer von {source_account_name}"


def _normalize_check_account_id_payload(check_account_id: str) -> int | str:
    normalized_check_account_id = str(check_account_id).strip()
    try:
        return int(normalized_check_account_id)
    except ValueError:
        return normalized_check_account_id


def move_transaction_to_check_account(
    base_url: str,
    token: str,
    transaction_id: str,
    target_check_account_id: str,
    *,
    source_check_account_name: str | None = None,
    target_check_account_type: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized_transaction_id = str(transaction_id).strip()
    if not normalized_transaction_id:
        raise RuntimeError("Target payment id is missing.")

    existing = request_check_account_transaction_by_id(base_url, token, normalized_transaction_id)
    if existing is None:
        raise RuntimeError(
            f"Safety check failed: payment id={normalized_transaction_id} not found before reassignment."
        )

    current_check_account = existing.get("checkAccount")
    if not isinstance(current_check_account, dict):
        raise RuntimeError(
            f"Payment id={normalized_transaction_id} has no current check account reference."
        )

    before_check_account_id = str(current_check_account.get("id", "")).strip()
    normalized_target_check_account_id = str(target_check_account_id).strip()
    if not normalized_target_check_account_id:
        raise RuntimeError("Selected target check account has no id.")
    if before_check_account_id == normalized_target_check_account_id:
        raise RuntimeError("Selected target check account is already assigned to this payment.")
    normalized_target_check_account_type = str(target_check_account_type or "").strip().lower()

    source_account_name = (source_check_account_name or "").strip() or before_check_account_id
    transfer_payment_purpose = _transfer_payment_purpose(existing, source_account_name)

    check_account_id_payload = _normalize_check_account_id_payload(normalized_target_check_account_id)

    try:
        source_amount = float(str(existing.get("amount", "")).strip())
    except ValueError as exc:
        raise RuntimeError(
            f"Payment id={normalized_transaction_id} has an invalid amount: {existing.get('amount')!r}."
        ) from exc

    transfer_date = existing.get("entryDate") or existing.get("valueDate")
    if not transfer_date:
        raise RuntimeError(
            f"Payment id={normalized_transaction_id} is missing both entryDate and valueDate."
        )

    if normalized_target_check_account_type == "offline":
        transfer_payload = {
            "amount": -source_amount,
            "target": {
                "id": check_account_id_payload,
                "objectName": "CheckAccount",
            },
            "date": transfer_date,
            "targetTransaction": None,
            "sourceTransaction": existing,
        }

        if dry_run:
            return {
                "transaction_id": normalized_transaction_id,
                "before_check_account_id": before_check_account_id,
                "after_check_account_id": normalized_target_check_account_id,
                "transfer_payload": transfer_payload,
                "response_payload": None,
                "updated_source_transaction": {
                    **existing,
                    "status": "400",
                },
                "created_target_transaction": None,
            }

        response_payload = transfer_check_account(
            base_url,
            token,
            before_check_account_id,
            transfer_payload,
        )
        updated_source = request_check_account_transaction_by_id(base_url, token, normalized_transaction_id)
        if updated_source is None:
            raise RuntimeError(
                "Post-transfer verification failed: could not reload source payment "
                f"id={normalized_transaction_id}."
            )

        if str(updated_source.get("status", "")).strip() != "400":
            raise RuntimeError(
                "Post-transfer verification failed: source payment did not switch to booked status 400."
            )

        updated_source_target = updated_source.get("targetTransaction")
        if not isinstance(updated_source_target, dict):
            raise RuntimeError(
                "Post-transfer verification failed: source payment has no linked target transaction."
            )

        created_target_id = str(updated_source_target.get("id", "")).strip()
        if not created_target_id:
            raise RuntimeError(
                "Post-transfer verification failed: source payment targetTransaction is missing its id."
            )

        updated_target = request_check_account_transaction_by_id(base_url, token, created_target_id)
        if updated_target is None:
            raise RuntimeError(
                "Post-transfer verification failed: could not reload target payment "
                f"id={created_target_id}."
            )

        updated_target_check_account = updated_target.get("checkAccount")
        if not isinstance(updated_target_check_account, dict):
            raise RuntimeError(
                "Post-transfer verification failed: target payment is missing the check account reference."
            )

        after_check_account_id = str(updated_target_check_account.get("id", "")).strip()
        if after_check_account_id != normalized_target_check_account_id:
            raise RuntimeError(
                "Post-transfer verification failed: target payment was created on the wrong check account "
                f"(expected={normalized_target_check_account_id}, got={after_check_account_id})."
            )

        updated_target_source = updated_target.get("sourceTransaction")
        if not isinstance(updated_target_source, dict) or str(updated_target_source.get("id", "")).strip() != normalized_transaction_id:
            raise RuntimeError(
                "Post-transfer verification failed: target payment does not reference the source transaction."
            )

        return {
            "transaction_id": normalized_transaction_id,
            "before_check_account_id": before_check_account_id,
            "after_check_account_id": after_check_account_id,
            "transfer_payload": transfer_payload,
            "response_payload": response_payload,
            "updated_source_transaction": updated_source,
            "created_target_transaction": updated_target,
            "target_transaction_id": created_target_id,
        }

    create_payload = {
        "valueDate": existing.get("valueDate"),
        "entryDate": existing.get("entryDate"),
        "amount": -source_amount,
        "payeePayerName": None,
        "payeePayerAcctNo": None,
        "payeePayerBankCode": None,
        "paymtPurpose": transfer_payment_purpose,
        "checkAccount": {
            "id": check_account_id_payload,
            "objectName": "CheckAccount",
        },
        "status": 400,
        "sourceTransaction": {
            "id": int(normalized_transaction_id),
            "objectName": "CheckAccountTransaction",
        },
    }

    source_update_payload = {
        "status": 400,
    }

    if dry_run:
        return {
            "transaction_id": normalized_transaction_id,
            "before_check_account_id": before_check_account_id,
            "after_check_account_id": normalized_target_check_account_id,
            "create_payload": create_payload,
            "source_update_payload": source_update_payload,
            "response_payload": None,
            "updated_source_transaction": {
                **existing,
                "status": "400",
            },
            "created_target_transaction": {
                "valueDate": create_payload["valueDate"],
                "entryDate": create_payload["entryDate"],
                "amount": create_payload["amount"],
                "paymtPurpose": create_payload["paymtPurpose"],
                "checkAccount": create_payload["checkAccount"],
                "status": create_payload["status"],
                "sourceTransaction": create_payload["sourceTransaction"],
            },
        }

    created_target_transaction = create_check_account_transaction(
        base_url,
        token,
        create_payload,
    )
    created_target_id = str(created_target_transaction.get("id", "")).strip()
    if not created_target_id:
        raise RuntimeError(
            "Post-create verification failed: sevDesk did not return an id for the created target transaction."
        )

    source_update_payload["targetTransaction"] = {
        "id": int(created_target_id) if created_target_id.isdigit() else created_target_id,
        "objectName": "CheckAccountTransaction",
    }

    source_response_payload = update_check_account_transaction(
        base_url,
        token,
        normalized_transaction_id,
        source_update_payload,
    )
    updated_source = request_check_account_transaction_by_id(base_url, token, normalized_transaction_id)
    if updated_source is None:
        raise RuntimeError(
            "Post-update verification failed: could not reload source payment "
            f"id={normalized_transaction_id}."
        )

    updated_target = request_check_account_transaction_by_id(base_url, token, created_target_id)
    if updated_target is None:
        raise RuntimeError(
            "Post-create verification failed: could not reload target payment "
            f"id={created_target_id}."
        )

    updated_target_check_account = updated_target.get("checkAccount")
    if not isinstance(updated_target_check_account, dict):
        raise RuntimeError(
            "Post-create verification failed: created target payment is missing the check account reference."
        )

    after_check_account_id = str(updated_target_check_account.get("id", "")).strip()
    if after_check_account_id != normalized_target_check_account_id:
        raise RuntimeError(
            "Post-create verification failed: target payment was created on the wrong check account "
            f"(expected={normalized_target_check_account_id}, got={after_check_account_id})."
        )

    if str(updated_source.get("status", "")).strip() != "400":
        raise RuntimeError(
            "Post-update verification failed: source payment did not switch to booked status 400."
        )

    updated_source_target = updated_source.get("targetTransaction")
    if not isinstance(updated_source_target, dict) or str(updated_source_target.get("id", "")).strip() != created_target_id:
        raise RuntimeError(
            "Post-update verification failed: source payment does not reference the created target transaction."
        )

    updated_target_source = updated_target.get("sourceTransaction")
    if not isinstance(updated_target_source, dict) or str(updated_target_source.get("id", "")).strip() != normalized_transaction_id:
        raise RuntimeError(
            "Post-create verification failed: target payment does not reference the source transaction."
        )

    return {
        "transaction_id": normalized_transaction_id,
        "before_check_account_id": before_check_account_id,
        "after_check_account_id": after_check_account_id,
        "create_payload": create_payload,
        "source_update_payload": source_update_payload,
        "response_payload": {
            "create": created_target_transaction,
            "update": source_response_payload,
        },
        "updated_source_transaction": updated_source,
        "created_target_transaction": updated_target,
        "target_transaction_id": created_target_id,
    }


def move_transaction_to_check_account_old_logic(
    base_url: str,
    token: str,
    transaction_id: str,
    target_check_account_id: str,
    *,
    source_check_account_name: str | None = None,
    target_check_account_type: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized_transaction_id = str(transaction_id).strip()
    if not normalized_transaction_id:
        raise RuntimeError("Target payment id is missing.")

    existing = request_check_account_transaction_by_id(base_url, token, normalized_transaction_id)
    if existing is None:
        raise RuntimeError(
            f"Safety check failed: payment id={normalized_transaction_id} not found before reassignment."
        )

    current_check_account = existing.get("checkAccount")
    if not isinstance(current_check_account, dict):
        raise RuntimeError(
            f"Payment id={normalized_transaction_id} has no current check account reference."
        )

    before_check_account_id = str(current_check_account.get("id", "")).strip()
    normalized_target_check_account_id = str(target_check_account_id).strip()
    if not normalized_target_check_account_id:
        raise RuntimeError("Selected target check account has no id.")
    if before_check_account_id == normalized_target_check_account_id:
        raise RuntimeError("Selected target check account is already assigned to this payment.")

    check_account_id_payload = _normalize_check_account_id_payload(normalized_target_check_account_id)
    update_payload = {
        "checkAccount": {
            "id": check_account_id_payload,
            "objectName": "CheckAccount",
        }
    }

    if dry_run:
        updated_transaction = {
            **existing,
            "checkAccount": update_payload["checkAccount"],
        }
        return {
            "transaction_id": normalized_transaction_id,
            "before_check_account_id": before_check_account_id,
            "after_check_account_id": normalized_target_check_account_id,
            "update_payload": update_payload,
            "response_payload": None,
            "updated_transaction": updated_transaction,
            "target_transaction_id": normalized_transaction_id,
        }

    response_payload = update_check_account_transaction(
        base_url,
        token,
        normalized_transaction_id,
        update_payload,
    )
    updated_transaction = request_check_account_transaction_by_id(base_url, token, normalized_transaction_id)
    if updated_transaction is None:
        raise RuntimeError(
            "Post-update verification failed: could not reload payment "
            f"id={normalized_transaction_id}."
        )

    updated_check_account = updated_transaction.get("checkAccount")
    if not isinstance(updated_check_account, dict):
        raise RuntimeError(
            "Post-update verification failed: payment is missing the check account reference."
        )

    after_check_account_id = str(updated_check_account.get("id", "")).strip()
    if after_check_account_id != normalized_target_check_account_id:
        raise RuntimeError(
            "Post-update verification failed: payment was not moved to the selected check account "
            f"(expected={normalized_target_check_account_id}, got={after_check_account_id})."
        )

    return {
        "transaction_id": normalized_transaction_id,
        "before_check_account_id": before_check_account_id,
        "after_check_account_id": after_check_account_id,
        "update_payload": update_payload,
        "response_payload": response_payload,
        "updated_transaction": updated_transaction,
        "target_transaction_id": normalized_transaction_id,
    }
