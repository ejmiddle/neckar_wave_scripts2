from __future__ import annotations

import base64
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import requests

from src.logging_config import logger


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
    *,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    params = {"limit": max(1, limit), "offset": max(0, offset)}
    if filters:
        params.update(filters)
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/Voucher",
        params=params,
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


def _sanitize_voucher_list_item(item: dict[str, Any]) -> dict[str, Any]:
    sanitized_item = dict(item)
    sanitized_item.pop("document", None)
    sanitized_item.pop("documents", None)
    sanitized_item.pop("attachments", None)
    return sanitized_item


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


def request_vouchers(
    base_url: str,
    token: str,
    limit: int,
    *,
    filters: dict[str, Any] | None = None,
    fetch_all: bool = True,
) -> list[dict[str, Any]]:
    started_at = perf_counter()
    effective_filters = dict(filters or {})
    if not fetch_all and "orderByVoucherNumber" not in effective_filters:
        effective_filters["orderByVoucherNumber"] = "DESC"
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    offset = 0
    effective_page_size = 200 if fetch_all else min(200, max(1, limit))
    page_number = 0

    logger.info(
        "Loading sevDesk vouchers for 'Belege laden' with requested limit=%s, page_size=%s, filters=%s, fetch_all=%s.",
        limit,
        effective_page_size,
        effective_filters,
        fetch_all,
    )

    while True:
        page_number += 1
        page_started_at = perf_counter()
        page = request_voucher_page(
            base_url,
            token,
            effective_page_size,
            offset,
            filters=effective_filters,
        )
        logger.info(
            "Fetched voucher page %s (offset=%s, size=%s) in %.2fs.",
            page_number,
            offset,
            len(page),
            perf_counter() - page_started_at,
        )
        if not page:
            break

        for item in page:
            item_id = str(item.get("id", "")).strip()
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            rows.append(_sanitize_voucher_list_item(item))
            if not fetch_all and len(rows) >= max(1, limit):
                break

        if not fetch_all and len(rows) >= max(1, limit):
            break
        if len(page) < effective_page_size:
            break
        offset += len(page)

    rows.sort(key=_voucher_sort_key, reverse=True)
    limited_rows = rows[: max(1, limit)]
    logger.info(
        "Loaded %s unique vouchers across %s pages in %.2fs; returning newest %s rows.",
        len(rows),
        page_number,
        perf_counter() - started_at,
        len(limited_rows),
    )
    return limited_rows


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


def request_tag_by_id(base_url: str, token: str, tag_id: str) -> dict[str, Any] | None:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        f"/Tag/{tag_id}",
    )
    objects = payload.get("objects")
    if isinstance(objects, dict):
        return objects
    if isinstance(objects, list) and objects and isinstance(objects[0], dict):
        return objects[0]
    return None


def request_voucher_tag_relations(
    base_url: str,
    token: str,
    voucher_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    limit = 100

    while True:
        payload = sevdesk_request(
            "GET",
            base_url,
            token,
            "/TagRelation",
            params={
                "object[objectName]": "Voucher",
                "object[id]": voucher_id,
                "limit": limit,
                "offset": offset,
            },
        )
        objects = payload.get("objects", [])
        if not isinstance(objects, list):
            break

        page = [item for item in objects if isinstance(item, dict)]
        rows.extend(page)

        if len(page) < limit:
            break
        offset += len(page)

    return rows


def attach_voucher_tags(
    base_url: str,
    token: str,
    vouchers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    started_at = perf_counter()
    tag_cache: dict[str, str] = {}
    enriched_vouchers: list[dict[str, Any]] = []

    logger.info("Enriching %s vouchers with sevDesk tags.", len(vouchers))

    for index, voucher in enumerate(vouchers, start=1):
        voucher_id = str(voucher.get("id", "")).strip()
        if not voucher_id:
            enriched_vouchers.append({**voucher, "tags": []})
            continue

        voucher_started_at = perf_counter()
        tag_relations = request_voucher_tag_relations(base_url, token, voucher_id)
        tag_names: list[str] = []
        seen_tag_names: set[str] = set()
        newly_loaded_tag_count = 0
        for relation in tag_relations:
            tag = relation.get("tag")
            if not isinstance(tag, dict):
                continue
            tag_id = str(tag.get("id", "")).strip()
            if not tag_id:
                continue

            if tag_id not in tag_cache:
                tag_row = request_tag_by_id(base_url, token, tag_id)
                tag_cache[tag_id] = str((tag_row or {}).get("name", "")).strip()
                newly_loaded_tag_count += 1

            tag_name = tag_cache[tag_id]
            if not tag_name or tag_name in seen_tag_names:
                continue
            seen_tag_names.add(tag_name)
            tag_names.append(tag_name)

        enriched_vouchers.append({**voucher, "tags": tag_names})
        logger.info(
            "Processed voucher %s/%s (id=%s): %s tag relations, %s tags, %s new tag lookups in %.2fs.",
            index,
            len(vouchers),
            voucher_id,
            len(tag_relations),
            len(tag_names),
            newly_loaded_tag_count,
            perf_counter() - voucher_started_at,
        )

    logger.info(
        "Finished tag enrichment for %s vouchers in %.2fs; tag cache size=%s.",
        len(vouchers),
        perf_counter() - started_at,
        len(tag_cache),
    )
    return enriched_vouchers


def request_vouchers_with_tags(
    base_url: str,
    token: str,
    limit: int,
    *,
    filters: dict[str, Any] | None = None,
    fetch_all: bool = True,
) -> list[dict[str, Any]]:
    started_at = perf_counter()
    vouchers = request_vouchers(base_url, token, limit, filters=filters, fetch_all=fetch_all)
    enriched_vouchers = attach_voucher_tags(base_url, token, vouchers)
    logger.info(
        "Completed sevDesk voucher load with tags for 'Belege laden' in %.2fs.",
        perf_counter() - started_at,
    )
    return enriched_vouchers


def request_vouchers_with_tags_for_contacts(
    base_url: str,
    token: str,
    limit: int,
    contact_ids: list[str],
    *,
    filters: dict[str, Any] | None = None,
    fetch_all: bool = True,
) -> list[dict[str, Any]]:
    normalized_contact_ids = [
        str(contact_id).strip() for contact_id in contact_ids if str(contact_id).strip()
    ]
    if not normalized_contact_ids:
        return []

    started_at = perf_counter()
    merged_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    base_filters = dict(filters or {})

    logger.info(
        "Loading sevDesk vouchers for %s contact filters with requested limit=%s, filters=%s, fetch_all=%s.",
        len(normalized_contact_ids),
        limit,
        base_filters,
        fetch_all,
    )

    for contact_id in normalized_contact_ids:
        contact_filters = {
            **base_filters,
            "contact[id]": contact_id,
            "contact[objectName]": "Contact",
        }
        rows = request_vouchers(
            base_url,
            token,
            limit,
            filters=contact_filters,
            fetch_all=fetch_all,
        )
        for row in rows:
            row_id = str(row.get("id", "")).strip()
            if row_id and row_id in seen_ids:
                continue
            if row_id:
                seen_ids.add(row_id)
            merged_rows.append(row)

    merged_rows.sort(key=_voucher_sort_key, reverse=True)
    limited_rows = merged_rows[: max(1, limit)]
    enriched_vouchers = attach_voucher_tags(base_url, token, limited_rows)
    logger.info(
        "Completed sevDesk voucher load with tags for %s contacts in %.2fs; returning %s rows.",
        len(normalized_contact_ids),
        perf_counter() - started_at,
        len(enriched_vouchers),
    )
    return enriched_vouchers


def download_document(base_url: str, token: str, document_id: str) -> dict[str, Any]:
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        f"/Document/{document_id}/download",
    )
    objects = payload.get("objects")
    if not isinstance(objects, dict):
        raise RuntimeError("sevDesk document download response is missing the document payload.")

    raw_content = objects.get("content")
    if not isinstance(raw_content, str) or not raw_content:
        raise RuntimeError("sevDesk document download response did not include file content.")

    if bool(objects.get("base64Encoded")):
        document_content = base64.b64decode(raw_content)
    else:
        document_content = raw_content.encode("utf-8")

    filename = str(objects.get("filename") or f"document_{document_id}").strip()
    mime_type = (
        str(objects.get("mimetype") or objects.get("mimeType") or "").strip()
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )
    return {
        "document_id": document_id,
        "filename": filename,
        "mime_type": mime_type,
        "content": document_content,
    }


def download_voucher_document(base_url: str, token: str, voucher_id: str) -> dict[str, Any]:
    voucher = request_voucher_by_id(base_url, token, voucher_id)
    if not isinstance(voucher, dict):
        raise RuntimeError(f"Voucher `{voucher_id}` could not be loaded.")

    document = voucher.get("document")
    if not isinstance(document, dict):
        raise RuntimeError(f"Voucher `{voucher_id}` does not have an attached document.")

    document_id = str(document.get("id", "")).strip()
    if not document_id:
        raise RuntimeError(f"Voucher `{voucher_id}` does not expose a valid document id.")

    downloaded_document = download_document(base_url, token, document_id)
    downloaded_document["voucher_id"] = voucher_id
    return downloaded_document


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
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    params = {
        "limit": max(1, limit),
        "offset": max(0, offset),
        "sort": sort,
    }
    if filters:
        params.update(filters)
    payload = sevdesk_request(
        "GET",
        base_url,
        token,
        "/Contact",
        params=params,
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
