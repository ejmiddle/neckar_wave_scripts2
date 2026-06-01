#!/usr/bin/env python3
"""Download and aggregate ready2order product group sales for a date range."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from src.accounting.ready2order import (
    DEFAULT_READY2ORDER_LIMIT,
    DEFAULT_READY2ORDER_OUTPUT_DIR,
    READY2ORDER_API_BASE,
    Ready2OrderRequestError,
    build_ready2order_product_group_summaries,
    fetch_ready2order_invoices_cached_by_day,
    read_ready2order_token,
    subtract_months,
)


@dataclass(frozen=True)
class ExportPaths:
    raw_invoices: Path
    line_items_csv: Path
    daily_summary_csv: Path
    weekly_summary_csv: Path
    monthly_summary_csv: Path
    workbook: Path


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM-DD date, got: {value}") from exc


def make_export_paths(output_dir: Path, date_from: date, date_to: date) -> ExportPaths:
    stem = f"ready2order_product_group_sales_{date_from:%Y%m%d}_{date_to:%Y%m%d}"
    return ExportPaths(
        raw_invoices=output_dir / f"{stem}_raw_invoices.json",
        line_items_csv=output_dir / f"{stem}_line_items.csv",
        daily_summary_csv=output_dir / f"{stem}_daily.csv",
        weekly_summary_csv=output_dir / f"{stem}_weekly.csv",
        monthly_summary_csv=output_dir / f"{stem}_monthly.csv",
        workbook=output_dir / f"{stem}.xlsx",
    )


def write_outputs(
    invoices: list[dict],
    paths: ExportPaths,
    *,
    date_from: date,
    date_to: date,
) -> dict[str, int]:
    paths.raw_invoices.parent.mkdir(parents=True, exist_ok=True)
    paths.raw_invoices.write_text(json.dumps(invoices, ensure_ascii=False, indent=2), encoding="utf-8")

    summaries = build_ready2order_product_group_summaries(
        invoices,
        date_from=date_from,
        date_to=date_to,
    )
    summaries["line_items"].to_csv(paths.line_items_csv, index=False)
    summaries["day"].to_csv(paths.daily_summary_csv, index=False)
    summaries["week"].to_csv(paths.weekly_summary_csv, index=False)
    summaries["month"].to_csv(paths.monthly_summary_csv, index=False)

    with pd.ExcelWriter(paths.workbook, engine="xlsxwriter") as writer:
        summaries["month"].to_excel(writer, sheet_name="month", index=False)
        summaries["week"].to_excel(writer, sheet_name="week", index=False)
        summaries["day"].to_excel(writer, sheet_name="day", index=False)
        summaries["line_items"].to_excel(writer, sheet_name="line_items", index=False)

    return {
        "line_items": len(summaries["line_items"]),
        "day": len(summaries["day"]),
        "week": len(summaries["week"]),
        "month": len(summaries["month"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download ready2order bills and aggregate product group sales."
    )
    today = date.today()
    parser.add_argument(
        "--date-from",
        type=parse_date,
        default=subtract_months(today, 2),
        help="Start date, inclusive. Default: today minus two months.",
    )
    parser.add_argument(
        "--date-to",
        type=parse_date,
        default=today,
        help="End date, inclusive. Default: today.",
    )
    parser.add_argument(
        "--date-field",
        choices=["daily_report", "dr_startDate", "bill", "b_dateTime"],
        default="daily_report",
        help="ready2order invoice date field to filter on.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_READY2ORDER_OUTPUT_DIR,
        help="Directory for generated exports.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_READY2ORDER_LIMIT,
        help="Page size for API requests.",
    )
    parser.add_argument(
        "--include-test-mode",
        action="store_true",
        help="Include ready2order training/test mode bills.",
    )
    parser.add_argument(
        "--base-url",
        default=READY2ORDER_API_BASE,
        help="ready2order API base URL.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Reload selected days from ready2order instead of reusing cached day files.",
    )
    return parser


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    if args.date_from > args.date_to:
        parser.error("--date-from must be before or equal to --date-to")
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    token = read_ready2order_token()
    if not token:
        print(
            "Missing ready2order token. Set READY2ORDER_BILL_API_TOKEN in the environment or .env.",
            file=sys.stderr,
        )
        return 2

    try:
        invoices, cache_stats = fetch_ready2order_invoices_cached_by_day(
            token,
            date_from=args.date_from,
            date_to=args.date_to,
            date_field=args.date_field,
            limit=args.limit,
            include_test_mode=args.include_test_mode,
            base_url=args.base_url,
            force_refresh=args.refresh_cache,
        )
        paths = make_export_paths(args.output_dir, args.date_from, args.date_to)
        row_counts = write_outputs(
            invoices,
            paths,
            date_from=args.date_from,
            date_to=args.date_to,
        )
    except Ready2OrderRequestError as exc:
        print(f"{exc}: {exc.body}", file=sys.stderr)
        if exc.status_code == 401 and "Developer-Tokens" in exc.body:
            print(
                "READY2ORDER_BILL_API_TOKEN must be a ready2order Account Token, "
                "not the Developer Token.",
                file=sys.stderr,
            )
        return 1
    except requests.RequestException as exc:
        print(f"ready2order request failed: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote raw invoices: {paths.raw_invoices}")
    print(f"Wrote line items: {paths.line_items_csv}")
    print(f"Wrote daily product group summary: {paths.daily_summary_csv}")
    print(f"Wrote weekly product group summary: {paths.weekly_summary_csv}")
    print(f"Wrote monthly product group summary: {paths.monthly_summary_csv}")
    print(f"Wrote workbook: {paths.workbook}")
    print(
        f"Invoices: {len(invoices)} | Line items: {row_counts['line_items']} | "
        f"Day rows: {row_counts['day']} | Week rows: {row_counts['week']} | "
        f"Month rows: {row_counts['month']}"
    )
    print(
        "Cache: "
        f"{cache_stats.get('cache_hits', 0)} days from cache, "
        f"{cache_stats.get('api_fetches', 0)} days loaded from ready2order, "
        f"{cache_stats.get('deduped_invoices', 0)} duplicate invoices removed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
