#!/usr/bin/env python3
"""Export Notion child databases from a page into downstream-friendly files."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests
from dotenv import load_dotenv


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int, body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class NotionDatabaseRef:
    database_id: str
    title: str


def notion_request(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    response = requests.request(
        method,
        f"{NOTION_API_BASE}{path}",
        headers=headers,
        params=payload if method.upper() == "GET" else None,
        json=payload if method.upper() != "GET" else None,
        timeout=30,
    )
    if not response.ok:
        raise NotionRequestError(
            f"{method} {path} failed with {response.status_code}",
            response.status_code,
            response.text,
        )
    return response.json()


def extract_notion_id(ref: str) -> str | None:
    raw = ref.strip()
    if not raw:
        return None

    compact = raw.replace("-", "")
    if re.fullmatch(r"[0-9a-fA-F]{32}", compact):
        return compact.lower()

    id_pattern = r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})"
    direct_match = re.search(id_pattern, raw)
    if direct_match:
        return direct_match.group(1).replace("-", "").lower()

    parsed = urlparse(raw)
    for value in [parsed.path or "", *(v[0] for v in parse_qs(parsed.query).values() if v)]:
        match = re.search(id_pattern, value)
        if match:
            return match.group(1).replace("-", "").lower()
    return None


def rich_text_to_plain(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(part.get("plain_text", "") for part in value if isinstance(part, dict))


def database_title(database_payload: dict[str, Any], fallback: str) -> str:
    title = rich_text_to_plain(database_payload.get("title"))
    return title.strip() or fallback


def iter_block_children(token: str, block_id: str) -> Iterable[dict[str, Any]]:
    payload: dict[str, Any] = {"page_size": 100}
    while True:
        data = notion_request("GET", f"/blocks/{block_id}/children", token, payload)
        yield from data.get("results", [])
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")


def discover_child_databases(token: str, page_id: str) -> list[NotionDatabaseRef]:
    seen_blocks: set[str] = set()
    seen_databases: set[str] = set()
    found: list[NotionDatabaseRef] = []

    def walk(block_id: str) -> None:
        if block_id in seen_blocks:
            return
        seen_blocks.add(block_id)
        for block in iter_block_children(token, block_id):
            child_id = (block.get("id") or "").replace("-", "")
            block_type = block.get("type")
            if block_type == "child_database" and child_id:
                title = ((block.get("child_database") or {}).get("title") or "").strip()
                database_id = child_id.lower()
                if database_id not in seen_databases:
                    seen_databases.add(database_id)
                    found.append(NotionDatabaseRef(database_id=database_id, title=title))
            if block.get("has_children") and child_id:
                walk(child_id)

    walk(page_id)
    return found


def iter_database_rows(token: str, database_id: str) -> Iterable[dict[str, Any]]:
    payload: dict[str, Any] = {"page_size": 100}
    while True:
        data = notion_request("POST", f"/databases/{database_id}/query", token, payload)
        yield from data.get("results", [])
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")


def format_notion_value(prop: dict[str, Any]) -> Any:
    prop_type = prop.get("type")
    value = prop.get(prop_type)
    if prop_type in {"title", "rich_text"}:
        return rich_text_to_plain(value)
    if prop_type == "select":
        return value.get("name") if isinstance(value, dict) else None
    if prop_type == "multi_select":
        return [item.get("name") for item in value or [] if isinstance(item, dict)]
    if prop_type == "people":
        return [item.get("name") or item.get("id") for item in value or [] if isinstance(item, dict)]
    if prop_type == "date":
        if not isinstance(value, dict):
            return None
        start = value.get("start")
        end = value.get("end")
        return f"{start} -> {end}" if start and end else start
    if prop_type == "status":
        return value.get("name") if isinstance(value, dict) else None
    if prop_type == "relation":
        return [item.get("id") for item in value or [] if isinstance(item, dict)]
    if prop_type == "files":
        return [item.get("name") for item in value or [] if isinstance(item, dict)]
    if prop_type in {"formula", "rollup"}:
        return value
    return value


def flatten_page(row: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {
        "_notion_page_id": row.get("id"),
        "_created_time": row.get("created_time"),
        "_last_edited_time": row.get("last_edited_time"),
        "_archived": row.get("archived"),
        "_url": row.get("url"),
    }
    for key, prop in (row.get("properties") or {}).items():
        flat[key] = format_notion_value(prop)
    return flat


def dataframe_for_export(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for column in df.columns:
        df[column] = df[column].map(
            lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
        )
    return df


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return slug or "notion_database"


def excel_sheet_name(title: str, used: set[str]) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", "_", title).strip() or "Database"
    base = cleaned[:31]
    candidate = base
    index = 2
    while candidate in used:
        suffix = f"_{index}"
        candidate = f"{base[: 31 - len(suffix)]}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page_ref", help="Notion page URL or ID containing child databases")
    parser.add_argument("--title-ending", default="2026", help="Only export databases whose title ends with this text")
    parser.add_argument(
        "--output-dir",
        default="workspace/notion/stunden_erfassung_altstadt_2026",
        help="Directory for CSV, XLSX, raw JSON, and manifest output",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    token = os.getenv("NOTION_TOKEN")
    if not token:
        print("Missing NOTION_TOKEN in environment or .env", file=sys.stderr)
        return 1

    args = parse_args()
    page_id = extract_notion_id(args.page_ref)
    if not page_id:
        print(f"Could not extract a Notion page ID from: {args.page_ref}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    csv_dir = output_dir / "csv"
    raw_dir = output_dir / "raw"
    csv_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    discovered = discover_child_databases(token, page_id)
    selected = [db for db in discovered if db.title.strip().endswith(args.title_ending)]

    manifest: dict[str, Any] = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "source_page_id": page_id,
        "title_ending": args.title_ending,
        "discovered_database_count": len(discovered),
        "exported_database_count": len(selected),
        "databases": [],
    }
    all_frames: list[pd.DataFrame] = []
    workbook_path = output_dir / "notion_databases_2026.xlsx"

    with pd.ExcelWriter(workbook_path) as writer:
        used_sheet_names: set[str] = set()
        for db in selected:
            schema = notion_request("GET", f"/databases/{db.database_id}", token)
            title = database_title(schema, db.title)
            rows_raw = list(iter_database_rows(token, db.database_id))
            rows_flat = [flatten_page(row) for row in rows_raw]
            df = dataframe_for_export(rows_flat)

            slug = slugify(f"{title}_{db.database_id[:8]}")
            csv_path = csv_dir / f"{slug}.csv"
            raw_path = raw_dir / f"{slug}.json"
            df.to_csv(csv_path, index=False)
            df.to_excel(writer, sheet_name=excel_sheet_name(title, used_sheet_names), index=False)

            raw_path.write_text(
                json.dumps({"database": schema, "rows": rows_raw}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            combined_df = df.copy()
            combined_df.insert(0, "_database_title", title)
            combined_df.insert(1, "_database_id", db.database_id)
            all_frames.append(combined_df)

            manifest["databases"].append(
                {
                    "title": title,
                    "database_id": db.database_id,
                    "row_count": len(df),
                    "csv_path": str(csv_path),
                    "raw_path": str(raw_path),
                }
            )

        if not selected:
            pd.DataFrame().to_excel(writer, sheet_name="No matching databases", index=False)

    combined_path = output_dir / "combined_2026.csv"
    if all_frames:
        pd.concat(all_frames, ignore_index=True, sort=False).to_csv(combined_path, index=False)
    else:
        pd.DataFrame().to_csv(combined_path, index=False)

    manifest["combined_csv_path"] = str(combined_path)
    manifest["workbook_path"] = str(workbook_path)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Discovered databases: {len(discovered)}")
    print(f"Exported databases ending with {args.title_ending!r}: {len(selected)}")
    for db_info in manifest["databases"]:
        print(f"- {db_info['title']}: {db_info['row_count']} rows")
    print(f"Manifest: {manifest_path}")
    print(f"Combined CSV: {combined_path}")
    print(f"Workbook: {workbook_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
