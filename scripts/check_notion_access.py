#!/usr/bin/env python3
import argparse
import os
import sys
from typing import Any, Dict, Iterable, List

import requests
try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover - optional dependency for output
    pd = None


class NotionRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int, body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def load_env_fallback() -> None:
    # Load .env if python-dotenv is available; otherwise rely on env vars.
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        pass


def notion_request(method: str, path: str, token: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base_url = "https://api.notion.com/v1"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    url = f"{base_url}{path}"
    resp = requests.request(method, url, headers=headers, json=payload, timeout=30)
    if not resp.ok:
        raise NotionRequestError(
            f"{method} {path} failed: {resp.status_code} {resp.text}",
            resp.status_code,
            resp.text,
        )
    return resp.json()


def summarize_objects(items: List[Dict[str, Any]], label_key: str) -> List[str]:
    summaries: List[str] = []
    for item in items:
        name = None
        if label_key == "user":
            name = item.get("name") or item.get("id")
        else:
            title = item.get("title") or []
            if isinstance(title, list) and title:
                name = title[0].get("plain_text")
            name = name or item.get("id")
        summaries.append(name)
    return summaries


def iter_database_rows(token: str, database_id: str) -> Iterable[Dict[str, Any]]:
    payload: Dict[str, Any] = {"page_size": 100}
    while True:
        data = notion_request("POST", f"/databases/{database_id}/query", token, payload)
        for row in data.get("results", []):
            yield row
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")


def extract_plain_text(value: Any) -> str:
    if isinstance(value, list):
        parts = []
        for item in value:
            text = item.get("plain_text")
            if text:
                parts.append(text)
        return "".join(parts)
    if isinstance(value, dict):
        return value.get("plain_text") or value.get("name") or ""
    if value is None:
        return ""
    return str(value)


def flatten_properties(props: Dict[str, Any]) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for key, prop in props.items():
        ptype = prop.get("type")
        value = prop.get(ptype)
        if ptype in {"title", "rich_text"}:
            flat[key] = extract_plain_text(value)
        elif ptype == "select":
            flat[key] = value.get("name") if isinstance(value, dict) else None
        elif ptype == "multi_select":
            flat[key] = [v.get("name") for v in value or []]
        elif ptype == "people":
            flat[key] = [v.get("name") or v.get("id") for v in value or []]
        elif ptype in {"email", "phone_number", "url"}:
            flat[key] = value
        elif ptype == "number":
            flat[key] = value
        elif ptype == "checkbox":
            flat[key] = value
        elif ptype == "date":
            flat[key] = value
        elif ptype == "status":
            flat[key] = value.get("name") if isinstance(value, dict) else None
        elif ptype == "relation":
            flat[key] = [v.get("id") for v in value or []]
        elif ptype == "files":
            flat[key] = [v.get("name") for v in value or []]
        elif ptype == "formula":
            flat[key] = value
        elif ptype == "rollup":
            flat[key] = value
        else:
            flat[key] = value
    return flat


def append_title_suffix(title: Any, suffix: str) -> List[Dict[str, Any]]:
    if not isinstance(title, list):
        title = []
    new_title = [item for item in title if isinstance(item, dict)]
    if suffix:
        new_title.append(
            {
                "type": "text",
                "text": {"content": suffix},
            }
        )
    return new_title


def resolve_parent_for_database(token: str, parent: Dict[str, Any]) -> Dict[str, Any]:
    parent_type = parent.get("type")
    if parent_type in {"page_id", "workspace"}:
        return parent
    if parent_type == "block_id":
        block_id = parent.get("block_id")
        while block_id:
            block = notion_request("GET", f"/blocks/{block_id}", token)
            block_parent = block.get("parent") or {}
            block_parent_type = block_parent.get("type")
            if block_parent_type in {"page_id", "workspace"}:
                return block_parent
            if block_parent_type != "block_id":
                raise ValueError(f"Unsupported parent type while resolving block chain: {block_parent_type}")
            block_id = block_parent.get("block_id")
        raise ValueError("Missing block_id while resolving parent chain")
    raise ValueError(f"Unsupported parent type for copy: {parent_type}")


def copy_database(token: str, database_id: str, suffix: str = " (Copy)") -> Dict[str, Any]:
    source = notion_request("GET", f"/databases/{database_id}", token)
    parent = resolve_parent_for_database(token, source.get("parent") or {})
    payload: Dict[str, Any] = {
        "parent": parent,
        "title": append_title_suffix(source.get("title"), suffix),
        "properties": source.get("properties") or {},
    }
    if source.get("description"):
        payload["description"] = source["description"]
    if source.get("icon"):
        payload["icon"] = source["icon"]
    if source.get("cover"):
        payload["cover"] = source["cover"]
    if source.get("is_inline") is not None:
        payload["is_inline"] = source["is_inline"]
    return notion_request("POST", "/databases", token, payload)


def build_create_properties(props: Dict[str, Any], skip_props: Iterable[str]) -> Dict[str, Any]:
    writable_types = {
        "title",
        "rich_text",
        "number",
        "select",
        "multi_select",
        "date",
        "people",
        "files",
        "checkbox",
        "url",
        "email",
        "phone_number",
        "relation",
        "status",
    }
    skip = {name.strip() for name in skip_props if name.strip()}
    create_props: Dict[str, Any] = {}
    for name, prop in props.items():
        if name in skip:
            continue
        ptype = prop.get("type")
        if ptype not in writable_types:
            continue
        value = prop.get(ptype)
        if ptype == "people":
            people = []
            for person in value or []:
                pid = person.get("id")
                if pid:
                    people.append({"id": pid})
            create_props[name] = {ptype: people}
            continue
        create_props[name] = {ptype: value}
    return create_props


def heavy_property_names(props: Dict[str, Any]) -> List[str]:
    heavy_types = {"people", "files", "relation"}
    names: List[str] = []
    for name, prop in props.items():
        if prop.get("type") in heavy_types:
            names.append(name)
    return names


def copy_database_rows(
    token: str,
    source_database_id: str,
    target_database_id: str,
    skip_props: Iterable[str],
) -> int:
    count = 0
    for row in iter_database_rows(token, source_database_id):
        props = row.get("properties") or {}
        payload = {
            "parent": {"database_id": target_database_id},
            "properties": build_create_properties(props, skip_props),
        }
        notion_request("POST", "/pages", token, payload)
        count += 1
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Notion API access and optionally dump a database.")
    parser.add_argument("database_id", nargs="?", help="Notion database ID to dump")
    parser.add_argument("--no-search", action="store_true", help="Skip /search calls")
    parser.add_argument("--output", default="notion_db_output.xlsx", help="XLSX output path")
    parser.add_argument("--copy", action="store_true", help="Create a schema-only copy of the database")
    parser.add_argument("--copy-rows", action="store_true", help="Copy rows into the newly created database")
    parser.add_argument("--skip-props", default="", help="Comma-separated property names to skip when copying rows")
    return parser.parse_args()


def main() -> int:
    load_env_fallback()
    token = os.getenv("NOTION_TOKEN")
    if not token:
        print("Missing NOTION_TOKEN in environment or .env", file=sys.stderr)
        return 1
    args = parse_args()
    database_id = args.database_id or os.getenv("NOTION_DATABASE_ID")

    print(os.getenv("NOTION_DATABASE_ID"))
    try:
        try:
            users = notion_request("GET", "/users", token)
            print(f"Users visible: {len(users.get('results', []))}")
            for name in summarize_objects(users.get("results", []), "user"):
                print(f"  - {name}")
        except NotionRequestError as exc:
            if exc.status_code == 403:
                print("Users visible: skipped (missing 'Read user information' capability)")
            else:
                raise

        if not args.no_search:
            search_payload = {"page_size": 5, "sort": {"direction": "descending", "timestamp": "last_edited_time"}}
            pages = notion_request("POST", "/search", token, {**search_payload, "filter": {"property": "object", "value": "page"}})
            print(f"Pages visible (sample): {len(pages.get('results', []))}")
            for name in summarize_objects(pages.get("results", []), "page"):
                print(f"  - {name}")

            dbs = notion_request("POST", "/search", token, {**search_payload, "filter": {"property": "object", "value": "database"}})
            print(f"Databases visible (sample): {len(dbs.get('results', []))}")
            for name in summarize_objects(dbs.get("results", []), "database"):
                print(f"  - {name}")

        copied_database_id = None
        if database_id and args.copy:
            copied = copy_database(token, database_id)
            copied_database_id = copied.get("id")
            print(f"Copied database to: {copied_database_id}")
            if args.copy_rows and copied_database_id:
                source_db = notion_request("GET", f"/databases/{database_id}", token)
                default_skip = heavy_property_names(source_db.get("properties") or {})
                cli_skip = [p.strip() for p in args.skip_props.split(",")] if args.skip_props else []
                skip_props = list(dict.fromkeys(default_skip + cli_skip))
                if skip_props:
                    print(f"Skipping properties while copying rows: {', '.join(skip_props)}")
                count = copy_database_rows(token, database_id, copied_database_id, skip_props)
                print(f"Copied {count} rows into: {copied_database_id}")
        elif args.copy_rows:
            print("--copy-rows requires --copy to be set", file=sys.stderr)
            return 1

        if database_id:
            print(f"Database rows for {database_id}:")
            rows: List[Dict[str, Any]] = []
            for row in iter_database_rows(token, database_id):
                props = row.get("properties", {})
                flat = flatten_properties(props)
                rows.append(flat)
            if pd is None:
                print("Pandas not available; install it to get a DataFrame output.")
                return 1
            df = pd.DataFrame(rows)
            print("\nDataFrame preview:")
            print(df.to_string(index=False))
            df.to_excel(args.output, index=False)
            print(f"Wrote XLSX: {args.output}")

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
