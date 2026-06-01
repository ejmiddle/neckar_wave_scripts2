from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import sleep
from typing import Any, Iterable

import pandas as pd
import requests

READY2ORDER_API_BASE = "https://api.ready2order.com/v1"
DEFAULT_READY2ORDER_OUTPUT_DIR = Path("workspace/ready2order")
DEFAULT_READY2ORDER_CACHE_DIR = Path("data/ready2order/cache/invoices")
DEFAULT_READY2ORDER_LIMIT = 100
READY2ORDER_RATE_LIMIT_PAUSE_SECONDS = 65
LINE_ITEM_COLUMNS = [
    "invoice_id",
    "invoice_number",
    "invoice_timestamp",
    "invoice_date",
    "invoice_total",
    "invoice_total_net",
    "invoice_total_vat",
    "invoice_test_mode",
    "bill_type_id",
    "payment_method_id",
    "item_id",
    "item_timestamp",
    "sale_date",
    "product_id",
    "product_group_id",
    "product_group_name",
    "item_name",
    "quantity",
    "item_price",
    "item_price_net",
    "gross_sales",
    "net_sales",
    "vat",
    "item_vat_rate",
    "item_retour",
    "item_accounting_code",
    "user_id",
    "user_name",
    "table_id",
    "table_name",
]
SALES_SUMMARY_COLUMNS = [
    "period",
    "period_start",
    "product_group_id",
    "product_group_name",
    "quantity",
    "gross_sales",
    "net_sales",
    "vat",
    "line_count",
    "invoice_count",
]


class Ready2OrderRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int, body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year = month_index // 12
    month = month_index % 12 + 1
    month_lengths = [31, 29 if is_leap_year(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(value.day, month_lengths[month - 1]))


def iter_dates(date_from: date, date_to: date) -> Iterable[date]:
    if date_from > date_to:
        raise ValueError("date_from must be before or equal to date_to")
    current_date = date_from
    while current_date <= date_to:
        yield current_date
        current_date += timedelta(days=1)


def read_ready2order_token() -> str | None:
    return (
        os.getenv("READY2ORDER_BILL_API_TOKEN")
        or os.getenv("READY2ORDER_API_TOKEN")
        or os.getenv("READY2ORDER_ACCOUNT_TOKEN")
    )


def ready2order_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "neckarwave_scripts/ready2order_product_sales",
    }


def ready2order_get(
    path: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    base_url: str = READY2ORDER_API_BASE,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    for attempt in range(2):
        response = requests.get(
            url,
            headers=ready2order_headers(token),
            params=params,
            timeout=60,
        )
        if response.status_code != 429 or attempt == 1:
            break
        sleep(READY2ORDER_RATE_LIMIT_PAUSE_SECONDS)

    if not response.ok:
        raise Ready2OrderRequestError(
            f"ready2order GET {path} failed with {response.status_code}",
            response.status_code,
            response.text,
        )
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"ready2order GET {path} did not return a JSON object")
    return data


def fetch_ready2order_invoice_page(
    token: str,
    *,
    date_from: date,
    date_to: date,
    offset: int,
    limit: int = DEFAULT_READY2ORDER_LIMIT,
    date_field: str = "daily_report",
    include_test_mode: bool = False,
    base_url: str = READY2ORDER_API_BASE,
) -> dict[str, Any]:
    return ready2order_get(
        "/document/invoice",
        token,
        params={
            "offset": offset,
            "limit": limit,
            "dateField": date_field,
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "items": "true",
            "payments": "true",
            "discounts": "true",
            "testMode": "true" if include_test_mode else "false",
        },
        base_url=base_url,
    )


def fetch_ready2order_invoices(
    token: str,
    *,
    date_from: date,
    date_to: date,
    date_field: str = "daily_report",
    limit: int = DEFAULT_READY2ORDER_LIMIT,
    include_test_mode: bool = False,
    base_url: str = READY2ORDER_API_BASE,
) -> list[dict[str, Any]]:
    invoices: list[dict[str, Any]] = []
    offset = 0
    effective_limit = max(1, min(limit, 100))

    while True:
        if offset and offset % (effective_limit * 55) == 0:
            sleep(READY2ORDER_RATE_LIMIT_PAUSE_SECONDS)

        page = fetch_ready2order_invoice_page(
            token,
            date_from=date_from,
            date_to=date_to,
            offset=offset,
            limit=effective_limit,
            date_field=date_field,
            include_test_mode=include_test_mode,
            base_url=base_url,
        )
        page_invoices = page.get("invoices", [])
        if not isinstance(page_invoices, list):
            raise RuntimeError("ready2order invoice response field 'invoices' is not a list")

        valid_page_invoices = [item for item in page_invoices if isinstance(item, dict)]
        invoices.extend(valid_page_invoices)
        if not valid_page_invoices or len(page_invoices) < effective_limit:
            break
        offset += len(page_invoices)

    return invoices


def _cache_token(value: Any) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value))


def ready2order_invoice_cache_path(
    report_date: date,
    *,
    date_field: str = "daily_report",
    include_test_mode: bool = False,
    cache_dir: Path = DEFAULT_READY2ORDER_CACHE_DIR,
) -> Path:
    test_mode_token = "test" if include_test_mode else "live"
    return (
        cache_dir
        / _cache_token(date_field)
        / test_mode_token
        / f"{report_date.isoformat()}.json"
    )


def load_ready2order_invoice_cache(
    report_date: date,
    *,
    date_field: str = "daily_report",
    include_test_mode: bool = False,
    cache_dir: Path = DEFAULT_READY2ORDER_CACHE_DIR,
) -> list[dict[str, Any]] | None:
    cache_path = ready2order_invoice_cache_path(
        report_date,
        date_field=date_field,
        include_test_mode=include_test_mode,
        cache_dir=cache_dir,
    )
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    invoices = payload.get("invoices") if isinstance(payload, dict) else None
    if not isinstance(invoices, list):
        return None
    return [invoice for invoice in invoices if isinstance(invoice, dict)]


def write_ready2order_invoice_cache(
    report_date: date,
    invoices: list[dict[str, Any]],
    *,
    date_field: str = "daily_report",
    include_test_mode: bool = False,
    cache_dir: Path = DEFAULT_READY2ORDER_CACHE_DIR,
) -> Path:
    cache_path = ready2order_invoice_cache_path(
        report_date,
        date_field=date_field,
        include_test_mode=include_test_mode,
        cache_dir=cache_dir,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "report_date": report_date.isoformat(),
        "date_field": date_field,
        "include_test_mode": include_test_mode,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "invoice_count": len(invoices),
        "invoices": invoices,
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cache_path


def dedupe_ready2order_invoices(invoices: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, invoice in enumerate(invoices):
        invoice_key = str(
            invoice.get("invoice_id")
            or invoice.get("invoice_numberFull")
            or invoice.get("invoice_number")
            or f"fallback-{index}"
        )
        if invoice_key in seen:
            continue
        seen.add(invoice_key)
        deduped.append(invoice)
    return deduped


def fetch_ready2order_invoices_cached_by_day(
    token: str,
    *,
    date_from: date,
    date_to: date,
    date_field: str = "daily_report",
    limit: int = DEFAULT_READY2ORDER_LIMIT,
    include_test_mode: bool = False,
    base_url: str = READY2ORDER_API_BASE,
    cache_dir: Path = DEFAULT_READY2ORDER_CACHE_DIR,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    invoices: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "days": 0,
        "cache_hits": 0,
        "api_fetches": 0,
        "refreshed_days": 0,
        "invoice_count_before_dedupe": 0,
    }

    for report_date in iter_dates(date_from, date_to):
        stats["days"] += 1
        daily_invoices = None
        if not force_refresh:
            daily_invoices = load_ready2order_invoice_cache(
                report_date,
                date_field=date_field,
                include_test_mode=include_test_mode,
                cache_dir=cache_dir,
            )
        if daily_invoices is not None:
            stats["cache_hits"] += 1
        else:
            daily_invoices = fetch_ready2order_invoices(
                token,
                date_from=report_date,
                date_to=report_date,
                date_field=date_field,
                limit=limit,
                include_test_mode=include_test_mode,
                base_url=base_url,
            )
            write_ready2order_invoice_cache(
                report_date,
                daily_invoices,
                date_field=date_field,
                include_test_mode=include_test_mode,
                cache_dir=cache_dir,
            )
            stats["api_fetches"] += 1
            if force_refresh:
                stats["refreshed_days"] += 1

        invoices.extend(daily_invoices)

    stats["invoice_count_before_dedupe"] = len(invoices)
    deduped_invoices = dedupe_ready2order_invoices(invoices)
    stats["invoice_count"] = len(deduped_invoices)
    stats["deduped_invoices"] = len(invoices) - len(deduped_invoices)
    return deduped_invoices, stats


def decimal_value(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def parse_ready2order_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace(" ", "T"), text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _sale_date(invoice: dict[str, Any], item: dict[str, Any]) -> date | None:
    for value in (item.get("item_timestamp"), invoice.get("invoice_timestamp")):
        parsed = parse_ready2order_datetime(value)
        if parsed is not None:
            return parsed.date()
    return None


def flatten_ready2order_line_items(invoices: Iterable[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for invoice in invoices:
        items = invoice.get("items") or []
        if not isinstance(items, list):
            continue
        invoice_timestamp = invoice.get("invoice_timestamp")
        invoice_date = None
        parsed_invoice_timestamp = parse_ready2order_datetime(invoice_timestamp)
        if parsed_invoice_timestamp is not None:
            invoice_date = parsed_invoice_timestamp.date().isoformat()

        for item in items:
            if not isinstance(item, dict):
                continue
            quantity = decimal_value(item.get("item_quantity") or item.get("item_qty"))
            gross_sales = decimal_value(item.get("item_total"))
            net_sales = decimal_value(item.get("item_totalNet"))
            vat = decimal_value(item.get("item_vat"))
            if item.get("item_retour"):
                quantity = -abs(quantity)
                gross_sales = -abs(gross_sales)
                net_sales = -abs(net_sales)
                vat = -abs(vat)

            sale_date = _sale_date(invoice, item)
            rows.append(
                {
                    "invoice_id": invoice.get("invoice_id"),
                    "invoice_number": invoice.get("invoice_numberFull") or invoice.get("invoice_number"),
                    "invoice_timestamp": invoice_timestamp,
                    "invoice_date": invoice_date,
                    "invoice_total": invoice.get("invoice_total"),
                    "invoice_total_net": invoice.get("invoice_totalNet"),
                    "invoice_total_vat": invoice.get("invoice_totalVat"),
                    "invoice_test_mode": invoice.get("invoice_testMode"),
                    "bill_type_id": invoice.get("billType_id"),
                    "payment_method_id": invoice.get("paymentMethod_id"),
                    "item_id": item.get("item_id"),
                    "item_timestamp": item.get("item_timestamp"),
                    "sale_date": sale_date.isoformat() if sale_date is not None else None,
                    "product_id": item.get("product_id"),
                    "product_group_id": item.get("productGroup_id") or item.get("productgroup_id"),
                    "product_group_name": item.get("productgroup_name") or "(No product group)",
                    "item_name": item.get("item_name"),
                    "quantity": float(quantity),
                    "item_price": item.get("item_price"),
                    "item_price_net": item.get("item_priceNet"),
                    "gross_sales": float(gross_sales),
                    "net_sales": float(net_sales),
                    "vat": float(vat),
                    "item_vat_rate": item.get("item_vatRate") or item.get("item_product_vatRate"),
                    "item_retour": item.get("item_retour"),
                    "item_accounting_code": item.get("item_accountingCode"),
                    "user_id": item.get("user_id") or invoice.get("user_id"),
                    "user_name": item.get("user_name"),
                    "table_id": item.get("table_id") or invoice.get("table_id"),
                    "table_name": item.get("table_name"),
                }
            )

    return pd.DataFrame(rows, columns=LINE_ITEM_COLUMNS)


def aggregate_ready2order_product_group_sales(
    line_items: pd.DataFrame,
    frequency: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> pd.DataFrame:
    if line_items.empty:
        return _complete_product_group_periods(
            pd.DataFrame(columns=SALES_SUMMARY_COLUMNS),
            line_items,
            frequency,
            date_from=date_from,
            date_to=date_to,
        )

    data = line_items.copy()
    data["sale_date"] = pd.to_datetime(data["sale_date"], errors="coerce")
    data = data.dropna(subset=["sale_date"])
    if data.empty:
        return _complete_product_group_periods(
            pd.DataFrame(columns=SALES_SUMMARY_COLUMNS),
            line_items,
            frequency,
            date_from=date_from,
            date_to=date_to,
        )

    for column in ("quantity", "gross_sales", "net_sales", "vat"):
        data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0)

    if frequency == "day":
        data["period_start"] = data["sale_date"].dt.to_period("D").dt.start_time.dt.date
        data["period"] = data["period_start"].astype(str)
    elif frequency == "week":
        period = data["sale_date"].dt.to_period("W-SUN")
        data["period_start"] = period.dt.start_time.dt.date
        iso = pd.to_datetime(data["period_start"]).dt.isocalendar()
        data["period"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    elif frequency == "month":
        data["period_start"] = data["sale_date"].dt.to_period("M").dt.start_time.dt.date
        data["period"] = pd.to_datetime(data["period_start"]).dt.strftime("%Y-%m")
    else:
        raise ValueError(f"Unsupported frequency: {frequency}")

    summary = (
        data.groupby(
            ["period", "period_start", "product_group_id", "product_group_name"],
            dropna=False,
            as_index=False,
        )
        .agg(
            quantity=("quantity", "sum"),
            gross_sales=("gross_sales", "sum"),
            net_sales=("net_sales", "sum"),
            vat=("vat", "sum"),
            line_count=("invoice_id", "size"),
            invoice_count=("invoice_id", "nunique"),
        )
        .sort_values(["period_start", "gross_sales"], ascending=[False, False])
    )
    return _complete_product_group_periods(
        summary[SALES_SUMMARY_COLUMNS],
        line_items,
        frequency,
        date_from=date_from,
        date_to=date_to,
    )


def build_period_frame(frequency: str, date_from: date, date_to: date) -> pd.DataFrame:
    if date_from > date_to:
        raise ValueError("date_from must be before or equal to date_to")

    if frequency == "day":
        period_start = pd.date_range(date_from, date_to, freq="D")
        period = period_start.strftime("%Y-%m-%d")
    elif frequency == "week":
        first_week_start = pd.Timestamp(date_from).to_period("W-SUN").start_time.date()
        last_week_start = pd.Timestamp(date_to).to_period("W-SUN").start_time.date()
        period_start = pd.date_range(first_week_start, last_week_start, freq="W-MON")
        iso = period_start.isocalendar()
        period = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    elif frequency == "month":
        first_month_start = date(date_from.year, date_from.month, 1)
        last_month_start = date(date_to.year, date_to.month, 1)
        period_start = pd.date_range(first_month_start, last_month_start, freq="MS")
        period = period_start.strftime("%Y-%m")
    else:
        raise ValueError(f"Unsupported frequency: {frequency}")

    return pd.DataFrame({"period": period, "period_start": period_start.date})


def _product_group_frame(line_items: pd.DataFrame) -> pd.DataFrame:
    if line_items.empty:
        return pd.DataFrame(columns=["product_group_id", "product_group_name"])
    groups = line_items[["product_group_id", "product_group_name"]].copy()
    groups["product_group_name"] = groups["product_group_name"].fillna("(No product group)")
    return groups.drop_duplicates().sort_values("product_group_name")


def _complete_product_group_periods(
    summary: pd.DataFrame,
    line_items: pd.DataFrame,
    frequency: str,
    *,
    date_from: date | None,
    date_to: date | None,
) -> pd.DataFrame:
    if date_from is None or date_to is None:
        return summary[SALES_SUMMARY_COLUMNS]

    period_frame = build_period_frame(frequency, date_from, date_to)
    product_groups = _product_group_frame(line_items)
    if period_frame.empty or product_groups.empty:
        return pd.DataFrame(columns=SALES_SUMMARY_COLUMNS)

    full_index = period_frame.merge(product_groups, how="cross")
    completed = full_index.merge(
        summary,
        on=["period", "period_start", "product_group_id", "product_group_name"],
        how="left",
    )
    for column in ("quantity", "gross_sales", "net_sales", "vat"):
        completed[column] = pd.to_numeric(completed[column], errors="coerce").fillna(0.0)
    for column in ("line_count", "invoice_count"):
        completed[column] = pd.to_numeric(completed[column], errors="coerce").fillna(0).astype(int)
    return completed[SALES_SUMMARY_COLUMNS].sort_values(
        ["period_start", "product_group_name"],
        ascending=[False, True],
    )


def build_overall_sales_by_period(
    summary: pd.DataFrame,
    frequency: str,
    *,
    date_from: date,
    date_to: date,
) -> pd.DataFrame:
    period_frame = build_period_frame(frequency, date_from, date_to)
    if summary.empty:
        totals = pd.DataFrame(columns=["period", "period_start", "gross_sales"])
    else:
        normalized_summary = summary.copy()
        normalized_summary["period_start"] = pd.to_datetime(
            normalized_summary["period_start"],
            errors="coerce",
        ).dt.date
        totals = (
            normalized_summary.groupby(["period", "period_start"], as_index=False)
            .agg(gross_sales=("gross_sales", "sum"))
            .sort_values("period_start")
        )
    chart_data = period_frame.merge(totals, on=["period", "period_start"], how="left")
    chart_data["gross_sales"] = pd.to_numeric(chart_data["gross_sales"], errors="coerce").fillna(0.0)
    return chart_data.sort_values("period_start")


def build_ready2order_product_group_summaries(
    invoices: Iterable[dict[str, Any]],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, pd.DataFrame]:
    line_items = flatten_ready2order_line_items(invoices)
    return {
        "line_items": line_items,
        "day": aggregate_ready2order_product_group_sales(
            line_items,
            "day",
            date_from=date_from,
            date_to=date_to,
        ),
        "week": aggregate_ready2order_product_group_sales(
            line_items,
            "week",
            date_from=date_from,
            date_to=date_to,
        ),
        "month": aggregate_ready2order_product_group_sales(
            line_items,
            "month",
            date_from=date_from,
            date_to=date_to,
        ),
    }
