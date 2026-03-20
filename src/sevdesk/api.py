from __future__ import annotations

import os
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


def request_vouchers(base_url: str, token: str, limit: int) -> list[dict[str, Any]]:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/Voucher",
        params={"limit": max(1, limit), "sort": "-voucherDate"},
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


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


def create_voucher(base_url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return sevdesk_request(
        "POST",
        base_url,
        token,
        "/Voucher/Factory/saveVoucher",
        payload=payload,
    )


def book_voucher(base_url: str, token: str, voucher_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return sevdesk_request(
        "PUT",
        base_url,
        token,
        f"/Voucher/{voucher_id}/bookAmount",
        payload=payload,
    )
