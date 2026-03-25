from __future__ import annotations

import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


def load_env_fallback() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        pass


def read_token() -> str | None:
    return (
        os.getenv("SEVDESK_KEY")
        or os.getenv("SEVDEKS_KEY")
        or os.getenv("SEVDESK_API_TOKEN")
        or os.getenv("SEVDESK_API_KEY")
    )


def sevdesk_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "neckarwave_scripts/sevdesk_belege",
    }


def sevdesk_multipart_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "neckarwave_scripts/sevdesk_belege",
    }


def sevdesk_request(
    method: str,
    base_url: str,
    token: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    response = requests.request(
        method,
        url,
        headers=sevdesk_headers(token),
        params=params,
        json=payload,
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"sevDesk request failed ({method} {path}) with {response.status_code}: {response.text}"
        )
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("sevDesk response is not a JSON object")
    return data


def request_voucher_page(
    base_url: str,
    token: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/Voucher",
        params={"limit": max(1, limit), "offset": max(0, offset)},
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


def _parse_voucher_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _voucher_sort_key(row: dict[str, Any]) -> tuple[int, float, int]:
    timestamp = _parse_voucher_timestamp(row.get("create")) or _parse_voucher_timestamp(
        row.get("update")
    )
    item_id = str(row.get("id", "")).strip()
    try:
        numeric_id = int(item_id)
    except ValueError:
        numeric_id = 0
    if timestamp is None:
        return (0, 0.0, numeric_id)
    return (1, timestamp.timestamp(), numeric_id)


def request_vouchers(base_url: str, token: str, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    offset = 0
    effective_page_size = 200

    while True:
        page = request_voucher_page(base_url, token, effective_page_size, offset)
        if not page:
            break

        for item in page:
            item_id = str(item.get("id", "")).strip()
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            rows.append(item)

        if len(page) < effective_page_size:
            break
        offset += len(page)

    rows.sort(key=_voucher_sort_key, reverse=True)
    return rows[: max(1, limit)]


def request_voucher_by_id(base_url: str, token: str, voucher_id: str) -> dict[str, Any] | None:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        f"/Voucher/{voucher_id}",
    )
    objects = payload.get("objects")
    if isinstance(objects, dict):
        return objects
    if isinstance(objects, list) and objects and isinstance(objects[0], dict):
        return objects[0]
    return None


def request_accounting_types(
    base_url: str,
    token: str,
    limit: int,
    offset: int,
    sort: str,
) -> list[dict[str, Any]]:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/AccountingType",
        params={
            "limit": max(1, limit),
            "offset": max(0, offset),
            "sort": sort,
        },
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


def request_check_accounts(
    base_url: str,
    token: str,
    limit: int,
    offset: int,
    sort: str,
) -> list[dict[str, Any]]:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/CheckAccount",
        params={
            "limit": max(1, limit),
            "offset": max(0, offset),
            "sort": sort,
        },
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


def request_tax_rules(
    base_url: str,
    token: str,
    limit: int,
    offset: int,
    sort: str,
) -> list[dict[str, Any]]:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/TaxRule",
        params={
            "limit": max(1, limit),
            "offset": max(0, offset),
            "sort": sort,
        },
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


def request_tax_sets(
    base_url: str,
    token: str,
    limit: int,
    offset: int,
    sort: str,
) -> list[dict[str, Any]]:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/TaxSet",
        params={
            "limit": max(1, limit),
            "offset": max(0, offset),
            "sort": sort,
        },
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


def request_contacts(
    base_url: str,
    token: str,
    limit: int,
    offset: int,
    sort: str,
) -> list[dict[str, Any]]:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/Contact",
        params={
            "limit": max(1, limit),
            "offset": max(0, offset),
            "sort": sort,
        },
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


def request_check_account_transactions(
    base_url: str,
    token: str,
    limit: int,
    offset: int,
    sort: str,
) -> list[dict[str, Any]]:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/CheckAccountTransaction",
        params={
            "limit": max(1, limit),
            "offset": max(0, offset),
            "sort": sort,
        },
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


def fetch_all_accounting_types(
    base_url: str,
    token: str,
    page_size: int,
    sort: str = "id",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    effective_page_size = min(1000, max(1, page_size))

    while True:
        page = request_accounting_types(base_url, token, effective_page_size, offset, sort)
        if not page:
            break
        rows.extend(page)
        if len(page) < effective_page_size:
            break
        offset += len(page)

    return rows


def fetch_all_check_accounts(
    base_url: str,
    token: str,
    page_size: int,
    sort: str = "id",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    effective_page_size = min(1000, max(1, page_size))

    while True:
        page = request_check_accounts(base_url, token, effective_page_size, offset, sort)
        if not page:
            break
        rows.extend(page)
        if len(page) < effective_page_size:
            break
        offset += len(page)

    return rows


def fetch_all_tax_rules(
    base_url: str,
    token: str,
    page_size: int,
    sort: str = "id",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    effective_page_size = min(1000, max(1, page_size))

    while True:
        page = request_tax_rules(base_url, token, effective_page_size, offset, sort)
        if not page:
            break
        rows.extend(page)
        if len(page) < effective_page_size:
            break
        offset += len(page)

    return rows


def fetch_all_tax_sets(
    base_url: str,
    token: str,
    page_size: int,
    sort: str = "id",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    effective_page_size = min(1000, max(1, page_size))

    while True:
        page = request_tax_sets(base_url, token, effective_page_size, offset, sort)
        if not page:
            break
        rows.extend(page)
        if len(page) < effective_page_size:
            break
        offset += len(page)

    return rows


def fetch_all_contacts(
    base_url: str,
    token: str,
    page_size: int,
    sort: str = "id",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    effective_page_size = min(1000, max(1, page_size))

    while True:
        page = request_contacts(base_url, token, effective_page_size, offset, sort)
        if not page:
            break
        rows.extend(page)
        if len(page) < effective_page_size:
            break
        offset += len(page)

    return rows


def fetch_latest_transactions_for_check_account(
    base_url: str,
    token: str,
    check_account_id: str,
    wanted_rows: int,
    *,
    page_size: int = 200,
    sort: str = "-valueDate",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    offset = 0
    target_count = max(1, wanted_rows)
    effective_page_size = min(1000, max(1, page_size))
    wanted_account_id = str(check_account_id).strip()

    while len(rows) < target_count:
        page = request_check_account_transactions(base_url, token, effective_page_size, offset, sort)
        if not page:
            break

        for item in page:
            account = item.get("checkAccount")
            if not isinstance(account, dict):
                continue
            if str(account.get("id", "")).strip() != wanted_account_id:
                continue
            item_id = str(item.get("id", "")).strip()
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            rows.append(item)
            if len(rows) >= target_count:
                break

        if len(page) < effective_page_size:
            break
        offset += len(page)

    return rows[:target_count]


def fetch_all_transactions_for_check_account(
    base_url: str,
    token: str,
    check_account_id: str,
    *,
    page_size: int = 200,
    sort: str = "-valueDate",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    offset = 0
    effective_page_size = min(1000, max(1, page_size))
    wanted_account_id = str(check_account_id).strip()

    while True:
        page = request_check_account_transactions(base_url, token, effective_page_size, offset, sort)
        if not page:
            break

        for item in page:
            account = item.get("checkAccount")
            if not isinstance(account, dict):
                continue
            if str(account.get("id", "")).strip() != wanted_account_id:
                continue
            item_id = str(item.get("id", "")).strip()
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            rows.append(item)

        if len(page) < effective_page_size:
            break
        offset += len(page)

    return rows


def create_voucher(base_url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return sevdesk_request(
        "POST",
        base_url,
        token,
        "/Voucher/Factory/saveVoucher",
        payload=payload,
    )


def create_contact(base_url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return sevdesk_request(
        "POST",
        base_url,
        token,
        "/Contact",
        payload=payload,
    )


def upload_voucher_temp_file(base_url: str, token: str, file_path: str | Path) -> str:
    path = Path(file_path)
    if not path.exists():
        raise RuntimeError(f"Voucher upload file not found: {path}")
    if not path.is_file():
        raise RuntimeError(f"Voucher upload path is not a file: {path}")

    guessed_content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    url = f"{base_url.rstrip('/')}/Voucher/Factory/uploadTempFile"
    with path.open("rb") as handle:
        response = requests.post(
            url,
            headers=sevdesk_multipart_headers(token),
            files={
                "file": (
                    path.name,
                    handle,
                    guessed_content_type,
                )
            },
            timeout=60,
        )

    if not response.ok:
        raise RuntimeError(
            "sevDesk temp-file upload failed "
            f"(POST /Voucher/Factory/uploadTempFile) with {response.status_code}: {response.text}"
        )

    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("sevDesk temp-file upload response is not a JSON object")

    objects = data.get("objects")
    if not isinstance(objects, dict):
        raise RuntimeError("sevDesk temp-file upload response has no objects payload")

    remote_filename = objects.get("filename")
    if not isinstance(remote_filename, str) or not remote_filename.strip():
        raise RuntimeError("sevDesk temp-file upload response has no filename")
    return remote_filename.strip()


def book_voucher(base_url: str, token: str, voucher_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return sevdesk_request(
        "PUT",
        base_url,
        token,
        f"/Voucher/{voucher_id}/bookAmount",
        payload=payload,
    )
