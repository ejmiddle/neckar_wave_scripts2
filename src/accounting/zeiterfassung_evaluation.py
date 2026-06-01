from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_OUTPUT_DIR = Path("workspace/notion/evaluation_zeiterfassung")
DEFAULT_EMPLOYEE_OVERVIEW_PATH = Path("workspace/Festangestellte_overview.xlsx")

GERMAN_MONTHS = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "maerz": 3,
    "märz": 3,
    "mrz": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
}


class NotionRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int, body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class NotionDatabaseRef:
    database_id: str
    title: str
    source_label: str = ""


@dataclass(frozen=True)
class ZeiterfassungEvaluation:
    total_hours: float
    hours_by_employee: pd.DataFrame
    hours_by_month_location_shift: pd.DataFrame
    hours_by_month_employee: pd.DataFrame
    shift_value_overview: pd.DataFrame
    row_count: int
    festangestellte_hours: pd.DataFrame
    festangestellte_weekly_hours: pd.DataFrame


def notion_token() -> str:
    token = os.getenv("NOTION_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing NOTION_TOKEN in environment or .env")
    return token


def notion_request(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = requests.request(
        method,
        f"{NOTION_API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
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
    raw = (ref or "").strip()
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
    candidates = [parsed.path or ""]
    candidates.extend(value[0] for value in parse_qs(parsed.query).values() if value)
    for value in candidates:
        match = re.search(id_pattern, value)
        if match:
            return match.group(1).replace("-", "").lower()
    return None


def _rich_text_to_plain(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(part.get("plain_text", "") for part in value if isinstance(part, dict))


def _iter_block_children(token: str, block_id: str) -> Iterable[dict[str, Any]]:
    payload: dict[str, Any] = {"page_size": 100}
    while True:
        data = notion_request("GET", f"/blocks/{block_id}/children", token, payload)
        yield from data.get("results", [])
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")


def discover_child_databases(
    page_ref: str,
    token: str | None = None,
    source_label: str = "",
) -> list[NotionDatabaseRef]:
    page_id = extract_notion_id(page_ref)
    if not page_id:
        raise ValueError("Could not extract a Notion page ID from the provided link.")

    token = token or notion_token()
    seen_databases: set[str] = set()
    found: list[NotionDatabaseRef] = []

    for block in _iter_block_children(token, page_id):
        child_id = (block.get("id") or "").replace("-", "").lower()
        block_type = block.get("type")
        if block_type != "child_database" or not child_id:
            continue
        title = ((block.get("child_database") or {}).get("title") or "").strip()
        if child_id in seen_databases:
            continue
        seen_databases.add(child_id)
        found.append(
            NotionDatabaseRef(
                database_id=child_id,
                title=title or child_id,
                source_label=source_label,
            )
        )
    return found


def _iter_database_rows(token: str, database_id: str) -> Iterable[dict[str, Any]]:
    payload: dict[str, Any] = {"page_size": 100}
    while True:
        data = notion_request("POST", f"/databases/{database_id}/query", token, payload)
        yield from data.get("results", [])
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")


def _format_notion_value(prop: dict[str, Any]) -> Any:
    prop_type = prop.get("type")
    value = prop.get(prop_type)
    if prop_type in {"title", "rich_text"}:
        return _rich_text_to_plain(value)
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
    return value


def _flatten_page(row: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {
        "_notion_page_id": row.get("id"),
        "_created_time": row.get("created_time"),
        "_last_edited_time": row.get("last_edited_time"),
        "_archived": row.get("archived"),
        "_url": row.get("url"),
    }
    for key, prop in (row.get("properties") or {}).items():
        flat[key] = _format_notion_value(prop)
    return flat


def _dataframe_for_export(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for column in df.columns:
        df[column] = df[column].map(
            lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
        )
    return df


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return slug or "notion_database"


def database_cache_key(databases: Iterable[NotionDatabaseRef]) -> str:
    selection = sorted(
        (
            {
                "database_id": database.database_id,
                "source_label": database.source_label,
            }
            for database in databases
        ),
        key=lambda item: (item["source_label"], item["database_id"]),
    )
    payload = json.dumps(selection, sort_keys=True)
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_dir(output_root: Path, databases: Iterable[NotionDatabaseRef]) -> Path:
    return output_root / "cache" / database_cache_key(databases)


def load_cached_export(
    databases: Iterable[NotionDatabaseRef],
    output_root: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[Path, pd.DataFrame, dict[str, Any]] | None:
    databases = list(databases)
    cache_dir = _cache_dir(output_root, databases)
    manifest_path = cache_dir / "manifest.json"
    combined_path = cache_dir / "combined.csv"
    if not manifest_path.exists() or not combined_path.exists():
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("database_cache_key") != database_cache_key(databases):
        return None

    combined = pd.read_csv(combined_path)
    manifest["loaded_from_cache"] = True
    manifest["combined_csv_path"] = str(combined_path)
    return Path(manifest.get("run_dir", cache_dir)), combined, manifest


def _manifest_database_ids(manifest: dict[str, Any]) -> set[str]:
    return {
        str(database.get("database_id", "")).replace("-", "").lower()
        for database in manifest.get("databases", [])
        if database.get("database_id")
    }


def prune_stale_cached_exports(
    active_databases: Iterable[NotionDatabaseRef],
    output_root: Path = DEFAULT_OUTPUT_DIR,
) -> list[Path]:
    active_database_ids = {
        database.database_id.replace("-", "").lower()
        for database in active_databases
    }
    cache_root = output_root / "cache"
    if not cache_root.exists():
        return []

    removed: list[Path] = []
    for cache_dir in cache_root.iterdir():
        if not cache_dir.is_dir():
            continue
        manifest_path = cache_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            shutil.rmtree(cache_dir)
            removed.append(cache_dir)
            continue

        cached_database_ids = _manifest_database_ids(manifest)
        if not cached_database_ids or not cached_database_ids.issubset(active_database_ids):
            shutil.rmtree(cache_dir)
            removed.append(cache_dir)
    return removed


def export_databases(
    databases: Iterable[NotionDatabaseRef],
    output_root: Path = DEFAULT_OUTPUT_DIR,
    token: str | None = None,
) -> tuple[Path, pd.DataFrame, dict[str, Any]]:
    databases = list(databases)
    token = token or notion_token()
    run_dir = output_root / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_dir = run_dir / "csv"
    raw_dir = run_dir / "raw"
    csv_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "database_cache_key": database_cache_key(databases),
        "loaded_from_cache": False,
        "databases": [],
    }
    frames: list[pd.DataFrame] = []

    for database in databases:
        schema = notion_request("GET", f"/databases/{database.database_id}", token)
        rows_raw = list(_iter_database_rows(token, database.database_id))
        rows_flat = [_flatten_page(row) for row in rows_raw]
        df = _dataframe_for_export(rows_flat)

        slug = _slugify(f"{database.title}_{database.database_id[:8]}")
        csv_path = csv_dir / f"{slug}.csv"
        raw_path = raw_dir / f"{slug}.json"
        df.to_csv(csv_path, index=False)
        raw_path.write_text(
            json.dumps({"database": schema, "rows": rows_raw}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        combined_df = df.copy()
        combined_df.insert(0, "_database_title", database.title)
        combined_df.insert(1, "_database_id", database.database_id)
        combined_df.insert(2, "_source_location", database.source_label)
        frames.append(combined_df)

        manifest["databases"].append(
            {
                "title": database.title,
                "database_id": database.database_id,
                "source_location": database.source_label,
                "row_count": len(df),
                "csv_path": str(csv_path),
                "raw_path": str(raw_path),
            }
        )

    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    combined_path = run_dir / "combined.csv"
    manifest_path = run_dir / "manifest.json"
    latest_manifest_path = output_root / "latest_manifest.json"
    combined.to_csv(combined_path, index=False)
    manifest["combined_csv_path"] = str(combined_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    cache_dir = _cache_dir(output_root, databases)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_combined_path = cache_dir / "combined.csv"
    cache_manifest_path = cache_dir / "manifest.json"
    combined.to_csv(cache_combined_path, index=False)
    cache_manifest = {**manifest, "combined_csv_path": str(cache_combined_path)}
    cache_manifest_path.write_text(json.dumps(cache_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir, combined, manifest


def export_databases_cached(
    databases: Iterable[NotionDatabaseRef],
    output_root: Path = DEFAULT_OUTPUT_DIR,
    token: str | None = None,
    force_refresh: bool = False,
) -> tuple[Path, pd.DataFrame, dict[str, Any]]:
    databases = list(databases)
    if not force_refresh:
        cached = load_cached_export(databases, output_root=output_root)
        if cached is not None:
            return cached
    return export_databases(databases, output_root=output_root, token=token)


def _first_existing_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    normalized = {str(column).strip().lower(): str(column) for column in df.columns}
    for candidate in candidates:
        match = normalized.get(candidate.strip().lower())
        if match:
            return match
    return None


def _normalize_group_value(value: Any) -> str:
    if value is None:
        return "Unbekannt"
    if not isinstance(value, (list, dict)) and pd.isna(value):
        return "Unbekannt"
    text = str(value).strip()
    if not text:
        return "Unbekannt"
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(parsed, list):
            values = [str(item).strip() for item in parsed if str(item).strip()]
            return ", ".join(values) if values else "Unbekannt"
    return text


def shift_cluster(value: Any) -> str:
    normalized = _normalize_group_value(value)
    if normalized == "Bakery":
        return "Bakery"
    if "roasting" in normalized.lower():
        return "Roasting"
    return "Service"


def easter_sunday(year: int) -> date:
    """Return Gregorian Easter Sunday using the Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def baden_wuerttemberg_holidays(year: int) -> dict[date, str]:
    easter = easter_sunday(year)
    return {
        date(year, 1, 1): "Neujahr",
        date(year, 1, 6): "Heilige Drei Koenige",
        easter - timedelta(days=2): "Karfreitag",
        easter + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Tag der Arbeit",
        easter + timedelta(days=39): "Christi Himmelfahrt",
        easter + timedelta(days=50): "Pfingstmontag",
        easter + timedelta(days=60): "Fronleichnam",
        date(year, 10, 3): "Tag der Deutschen Einheit",
        date(year, 11, 1): "Allerheiligen",
        date(year, 12, 25): "1. Weihnachtstag",
        date(year, 12, 26): "2. Weihnachtstag",
    }


def parse_month_start(value: Any) -> pd.Timestamp | pd.NaT:
    text = _normalize_group_value(value)
    iso_match = re.search(r"\b(20\d{2})[-_/\.](\d{1,2})\b", text)
    if iso_match:
        year = int(iso_match.group(1))
        month = int(iso_match.group(2))
        if 1 <= month <= 12:
            return pd.Timestamp(year=year, month=month, day=1)

    month_pattern = "|".join(sorted((re.escape(month) for month in GERMAN_MONTHS), key=len, reverse=True))
    german_match = re.search(rf"\b({month_pattern})\b.*\b(20\d{{2}})\b", text, flags=re.IGNORECASE)
    if german_match:
        month = GERMAN_MONTHS[german_match.group(1).lower()]
        year = int(german_match.group(2))
        return pd.Timestamp(year=year, month=month, day=1)

    return pd.NaT


def workdays_without_bw_holidays(month_start: pd.Timestamp) -> int:
    month_date = month_start.date()
    holidays = baden_wuerttemberg_holidays(month_date.year)
    day_count = pd.Period(month_start, freq="M").days_in_month
    return sum(
        1
        for day in range(1, day_count + 1)
        if (current := date(month_date.year, month_date.month, day)).weekday() < 5
        and current not in holidays
    )


def _parse_entry_date(value: Any) -> pd.Timestamp | pd.NaT:
    if value is None:
        return pd.NaT
    if not isinstance(value, (list, dict)) and pd.isna(value):
        return pd.NaT
    text = str(value).strip()
    if not text:
        return pd.NaT
    start_text = text.split(" -> ", maxsplit=1)[0].strip()
    return pd.to_datetime(start_text, errors="coerce")


def _week_start(value: pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(value)
    if value.tzinfo is not None:
        value = value.tz_convert(None)
    normalized = value.normalize()
    return normalized - pd.Timedelta(days=normalized.weekday())


def _week_label(week_start: pd.Timestamp) -> str:
    iso = week_start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def workdays_without_bw_holidays_for_week(week_start: pd.Timestamp) -> int:
    start = week_start.date()
    years = {start.year, (start + timedelta(days=6)).year}
    holidays = {
        holiday
        for year in years
        for holiday in baden_wuerttemberg_holidays(year)
    }
    return sum(
        1
        for offset in range(5)
        if (start + timedelta(days=offset)) not in holidays
    )


def _is_full_week_in_months(week_start: pd.Timestamp, month_starts: set[pd.Timestamp]) -> bool:
    return all(
        pd.Timestamp(week_start + pd.Timedelta(days=offset)).replace(day=1).normalize()
        in month_starts
        for offset in range(7)
    )


def load_festangestellte(path: Path = DEFAULT_EMPLOYEE_OVERVIEW_PATH) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="aktuell")
    required = {"Name", "Stunden"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns in {path}: {sorted(missing)}")

    employees = df.loc[:, ["Name", "Stunden"]].copy()
    employees["Name"] = employees["Name"].astype(str).str.strip()
    employees["Stunden"] = pd.to_numeric(employees["Stunden"], errors="coerce")
    employees = employees[employees["Name"].ne("") & employees["Stunden"].notna()].reset_index(drop=True)
    employees = employees.rename(columns={"Name": "Mitarbeiter", "Stunden": "Wochenstunden"})
    employees["Taegliche Sollstunden"] = employees["Wochenstunden"] / 5
    return employees


def build_festangestellte_hours_evaluation(
    hours_by_month_employee: pd.DataFrame,
    employees: pd.DataFrame | None = None,
) -> pd.DataFrame:
    employees = employees if employees is not None else load_festangestellte()
    if employees.empty or hours_by_month_employee.empty:
        return pd.DataFrame(
            columns=[
                "Monat",
                "Mitarbeiter",
                "Wochenstunden",
                "Arbeitstage",
                "Sollstunden",
                "Iststunden",
                "Bereinigt",
                "Differenz",
                "Erfuellung %",
            ]
        )

    actual = hours_by_month_employee.copy()
    actual["_month_start"] = actual["Monat"].map(parse_month_start)
    actual = actual[actual["_month_start"].notna()].copy()
    if actual.empty:
        return pd.DataFrame()

    month_labels = (
        actual.sort_values(["_month_start", "Monat"])
        .drop_duplicates("_month_start")
        .loc[:, ["_month_start", "Monat"]]
    )
    months = month_labels["_month_start"].tolist()
    base = employees.merge(pd.DataFrame({"_month_start": months}), how="cross")
    base = base.merge(month_labels, on="_month_start", how="left")
    base["Arbeitstage"] = base["_month_start"].map(workdays_without_bw_holidays)
    base["Sollstunden"] = (base["Taegliche Sollstunden"] * base["Arbeitstage"]).round(2)

    actual_sum = (
        actual.groupby(["_month_start", "Mitarbeiter"], dropna=False)["Stunden"]
        .sum()
        .reset_index()
        .rename(columns={"Stunden": "Iststunden"})
    )
    result = base.merge(actual_sum, on=["_month_start", "Mitarbeiter"], how="left")
    result["Iststunden"] = result["Iststunden"].fillna(0.0).round(2)
    result["Bereinigt"] = (result["Iststunden"] - result["Sollstunden"] * 0.10).round(2)
    result["Differenz"] = (result["Iststunden"] - result["Sollstunden"]).round(2)
    result["Erfuellung %"] = (
        (result["Iststunden"] / result["Sollstunden"]).where(result["Sollstunden"].ne(0), 0) * 100
    ).round(1)
    result = result.sort_values(["_month_start", "Mitarbeiter"])
    return result[
        [
            "Monat",
            "Mitarbeiter",
            "Wochenstunden",
            "Arbeitstage",
            "Sollstunden",
            "Iststunden",
            "Bereinigt",
            "Differenz",
            "Erfuellung %",
        ]
    ]


def build_festangestellte_weekly_hours_evaluation(
    analysis: pd.DataFrame,
    hours_column: str,
    employee_column: str,
    date_column: str | None = None,
    employees: pd.DataFrame | None = None,
) -> pd.DataFrame:
    columns = [
        "Woche",
        "Von",
        "Bis",
        "Mitarbeiter",
        "Wochenstunden",
        "Arbeitstage",
        "SOLL",
        "IST",
        "Stunden bereinigt",
    ]
    employees = employees if employees is not None else load_festangestellte()
    if employees.empty or analysis.empty or not date_column:
        return pd.DataFrame(columns=columns)

    weekly = analysis.copy()
    weekly["_entry_date"] = weekly[date_column].map(_parse_entry_date)
    weekly = weekly[weekly["_entry_date"].notna()].copy()
    if weekly.empty:
        return pd.DataFrame(columns=columns)

    weekly["_month_start"] = weekly["_month_label"].map(parse_month_start)
    weekly = weekly[weekly["_month_start"].notna()].copy()
    if weekly.empty:
        return pd.DataFrame(columns=columns)

    evaluated_months = {
        pd.Timestamp(month).normalize()
        for month in weekly["_month_start"].dropna().unique()
    }
    week_starts = sorted(
        {
            _week_start(entry_date)
            for entry_date in weekly["_entry_date"]
            if _is_full_week_in_months(_week_start(entry_date), evaluated_months)
        }
    )
    if not week_starts:
        return pd.DataFrame(columns=columns)

    employee_names = set(employees["Mitarbeiter"])
    weekly["_employee_label"] = weekly[employee_column].fillna("").astype(str).str.strip()
    weekly["_hours_numeric"] = pd.to_numeric(weekly[hours_column], errors="coerce").fillna(0.0)
    weekly["_week_start"] = weekly["_entry_date"].map(_week_start)
    weekly = weekly[
        weekly["_employee_label"].isin(employee_names)
        & weekly["_week_start"].isin(week_starts)
    ]

    actual = (
        weekly.groupby(["_week_start", "_employee_label"], dropna=False)["_hours_numeric"]
        .sum()
        .reset_index()
        .rename(columns={"_employee_label": "Mitarbeiter", "_hours_numeric": "IST"})
    )

    base = employees.merge(pd.DataFrame({"_week_start": week_starts}), how="cross")
    base["Arbeitstage"] = base["_week_start"].map(workdays_without_bw_holidays_for_week)
    base["SOLL"] = (base["Taegliche Sollstunden"] * base["Arbeitstage"]).round(2)
    result = base.merge(actual, on=["_week_start", "Mitarbeiter"], how="left")
    result["IST"] = result["IST"].fillna(0.0).round(2)
    result["Stunden bereinigt"] = (result["IST"] - 4).clip(lower=result["SOLL"]).round(2)
    result["Von"] = result["_week_start"].dt.date.astype(str)
    result["Bis"] = (result["_week_start"] + pd.Timedelta(days=6)).dt.date.astype(str)
    result["Woche"] = result["_week_start"].map(_week_label)
    result = result.sort_values(["_week_start", "Mitarbeiter"])
    return result[
        [
            "Woche",
            "Von",
            "Bis",
            "Mitarbeiter",
            "Wochenstunden",
            "Arbeitstage",
            "SOLL",
            "IST",
            "Stunden bereinigt",
        ]
    ]


def evaluate_hours(df: pd.DataFrame) -> ZeiterfassungEvaluation:
    hours_column = _first_existing_column(df, ["Worked Hours", "Hours", "Stunden", "Arbeitsstunden"])
    employee_column = _first_existing_column(df, ["Mitarbeiter", "Employee", "Person", "Name"])
    shift_column = _first_existing_column(df, ["Shift", "Schicht"])
    date_column = _first_existing_column(df, ["Date", "Datum", "Tag"])
    if not hours_column:
        raise ValueError("Could not find an hours column. Expected one of: Worked Hours, Hours, Stunden.")
    if not employee_column:
        raise ValueError("Could not find an employee column. Expected one of: Mitarbeiter, Employee, Person, Name.")

    analysis = df.copy()
    analysis["_hours_numeric"] = pd.to_numeric(analysis[hours_column], errors="coerce").fillna(0.0)
    analysis["_employee_label"] = analysis[employee_column].fillna("").astype(str).str.strip()
    analysis.loc[analysis["_employee_label"] == "", "_employee_label"] = "Unbekannt"
    analysis["_month_label"] = (
        analysis["_database_title"].map(_normalize_group_value)
        if "_database_title" in analysis.columns
        else "Unbekannt"
    )
    analysis["_source_location_label"] = (
        analysis["_source_location"].map(_normalize_group_value)
        if "_source_location" in analysis.columns
        else "Unbekannt"
    )
    analysis["_shift_label"] = (
        analysis[shift_column].map(_normalize_group_value) if shift_column else "Unbekannt"
    )
    analysis["_shift_cluster"] = analysis["_shift_label"].map(shift_cluster)

    by_employee = (
        analysis.groupby("_employee_label", dropna=False)["_hours_numeric"]
        .sum()
        .reset_index()
        .rename(columns={"_employee_label": "Mitarbeiter", "_hours_numeric": "Stunden"})
        .sort_values("Stunden", ascending=False)
    )
    by_month_location_shift = (
        analysis.groupby(["_month_label", "_source_location_label", "_shift_cluster"], dropna=False)[
            "_hours_numeric"
        ]
        .sum()
        .reset_index()
        .rename(
            columns={
                "_month_label": "Monat",
                "_source_location_label": "Location",
                "_shift_cluster": "Shift",
                "_hours_numeric": "Stunden",
            }
        )
        .sort_values(["Monat", "Location", "Shift"])
    )
    by_month_employee = (
        analysis.groupby(["_month_label", "_employee_label"], dropna=False)["_hours_numeric"]
        .sum()
        .reset_index()
        .rename(
            columns={
                "_month_label": "Monat",
                "_employee_label": "Mitarbeiter",
                "_hours_numeric": "Stunden",
            }
        )
        .sort_values(["Monat", "Mitarbeiter"])
    )
    shift_value_overview = (
        analysis.groupby(["_shift_label", "_shift_cluster"], dropna=False)
        .agg(
            Eintraege=("_shift_label", "size"),
            Stunden=("_hours_numeric", "sum"),
        )
        .reset_index()
        .rename(columns={"_shift_label": "Shift Value", "_shift_cluster": "Cluster"})
        .sort_values(["Cluster", "Shift Value"])
    )
    festangestellte_hours = build_festangestellte_hours_evaluation(by_month_employee)
    festangestellte_weekly_hours = build_festangestellte_weekly_hours_evaluation(
        analysis,
        hours_column=hours_column,
        employee_column=employee_column,
        date_column=date_column,
    )
    return ZeiterfassungEvaluation(
        total_hours=float(analysis["_hours_numeric"].sum()),
        hours_by_employee=by_employee,
        hours_by_month_location_shift=by_month_location_shift,
        hours_by_month_employee=by_month_employee,
        shift_value_overview=shift_value_overview,
        row_count=len(analysis),
        festangestellte_hours=festangestellte_hours,
        festangestellte_weekly_hours=festangestellte_weekly_hours,
    )
