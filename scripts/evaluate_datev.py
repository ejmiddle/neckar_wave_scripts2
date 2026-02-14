#!/usr/bin/env python3
"""Evaluate DATEV EXTF exports and print a reusable overview."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


@dataclass
class ExtfFile:
    path: Path
    meta: list[str]
    header: list[str]
    rows: list[list[str]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze DATEV EXTF Buchungsstapel and Debitoren/Kreditoren exports.",
    )
    parser.add_argument(
        "--datev-dir",
        type=Path,
        default=Path("data/DATEV"),
        help="Directory containing DATEV EXTF CSV files.",
    )
    parser.add_argument(
        "--bookings-file",
        type=Path,
        default=None,
        help="Optional explicit path to the Buchungsstapel CSV.",
    )
    parser.add_argument(
        "--master-file",
        type=Path,
        default=None,
        help="Optional explicit path to the Debitoren/Kreditoren CSV.",
    )
    parser.add_argument(
        "--encoding",
        default="cp1252",
        help="File encoding (DATEV exports are commonly cp1252).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of top entries to include in ranking fields.",
    )
    parser.add_argument(
        "--igl-bu-keys",
        default="2222",
        help=(
            "Comma-separated BU keys for strict iGL candidates "
            "(used in addition to non-DE UStID + EU-Steuersatz=0)."
        ),
    )
    parser.add_argument(
        "--extract-bu-keys",
        default="1111,2222,3333",
        help="Comma-separated BU keys to extract and analyze in detail.",
    )
    parser.add_argument(
        "--plots-dir",
        type=Path,
        default=Path("data/DATEV/analysis"),
        help="Directory for generated plots and optional extracted CSV.",
    )
    parser.add_argument(
        "--write-extracted-csv",
        action="store_true",
        help="Write extracted BU rows to CSV in --plots-dir.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON only.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path for JSON result.",
    )
    return parser.parse_args()


def normalize_path(path: Path, base_dir: Path) -> Path:
    return path if path.is_absolute() else base_dir / path


def find_latest_file_by_prefix(datev_dir: Path, prefix: str) -> Path:
    matches = list(datev_dir.glob(f"{prefix}*.csv"))
    if not matches:
        raise FileNotFoundError(f"No files matching prefix {prefix!r} in {datev_dir}")
    matches.sort(key=lambda path: (path.stat().st_mtime, path.name))
    return matches[-1]


def read_extf(path: Path, encoding: str) -> ExtfFile:
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        meta = next(reader)
        header = next(reader)
        rows = [row for row in reader if any((col or "").strip() for col in row)]
    return ExtfFile(path=path, meta=meta, header=header, rows=rows)


def money_to_decimal(raw: str) -> Decimal:
    value = (raw or "").strip().replace(".", "").replace(",", ".")
    if not value:
        return Decimal("0")
    try:
        return Decimal(value)
    except InvalidOperation:
        return Decimal("0")


def row_get(row: list[str], index_map: dict[str, int], column: str) -> str:
    idx = index_map.get(column)
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip().strip('"')


def parse_country_prefix(ustid: str) -> str:
    cleaned = (ustid or "").replace(" ", "").upper()
    if len(cleaned) < 2:
        return ""
    prefix = cleaned[:2]
    return prefix if prefix.isalpha() else ""


def summarize_amounts(rows: list[list[str]], idx: dict[str, int]) -> dict[str, str]:
    total_s = Decimal("0")
    total_h = Decimal("0")
    for row in rows:
        amount = money_to_decimal(row_get(row, idx, "Umsatz"))
        sign = row_get(row, idx, "Soll-/Haben-Kennzeichen")
        if sign == "S":
            total_s += amount
        elif sign == "H":
            total_h += amount
    return {
        "debit_S_total": str(total_s),
        "credit_H_total": str(total_h),
        "net_S_minus_H": str(total_s - total_h),
    }


def signed_amount(amount: Decimal, sign: str) -> Decimal:
    if sign == "S":
        return amount
    if sign == "H":
        return -amount
    return Decimal("0")


def booking_partner_name(row: list[str], bookings_idx: dict[str, int]) -> str:
    for i in range(1, 9):
        art = row_get(row, bookings_idx, f"Beleginfo-Art {i}")
        inhalt = row_get(row, bookings_idx, f"Beleginfo-Inhalt {i}")
        if art == "Name" and inhalt:
            return inhalt
    return ""


def extract_bu_rows(
    rows: list[list[str]],
    bookings_idx: dict[str, int],
    bu_keys: set[str],
) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    for row in rows:
        bu_key = row_get(row, bookings_idx, "BU-Schlüssel")
        if bu_key not in bu_keys:
            continue
        amount = money_to_decimal(row_get(row, bookings_idx, "Umsatz"))
        sign = row_get(row, bookings_idx, "Soll-/Haben-Kennzeichen")
        ustid_best = row_get(row, bookings_idx, "EU-Mitgliedstaat u. UStID (Bestimmung)")
        extracted.append(
            {
                "bu_key": bu_key,
                "amount": str(amount),
                "sign": sign,
                "signed_amount": str(signed_amount(amount, sign)),
                "belegdatum": row_get(row, bookings_idx, "Belegdatum"),
                "month_mm": row_get(row, bookings_idx, "Belegdatum")[2:4]
                if len(row_get(row, bookings_idx, "Belegdatum")) == 4
                else "",
                "konto": row_get(row, bookings_idx, "Konto"),
                "gegenkonto": row_get(row, bookings_idx, "Gegenkonto (ohne BU-Schlüssel)"),
                "buchungstext": row_get(row, bookings_idx, "Buchungstext"),
                "partner_name": booking_partner_name(row, bookings_idx),
                "eu_ustid_bestimmung": ustid_best,
                "eu_steuer_bestimmung": row_get(row, bookings_idx, "EU-Steuersatz (Bestimmung)"),
                "country_prefix": parse_country_prefix(ustid_best),
            }
        )
    return extracted


def summarize_extracted_amounts(extracted: list[dict[str, Any]]) -> dict[str, str]:
    total_s = Decimal("0")
    total_h = Decimal("0")
    for row in extracted:
        amount = Decimal(row["amount"])
        sign = row["sign"]
        if sign == "S":
            total_s += amount
        elif sign == "H":
            total_h += amount
    return {
        "debit_S_total": str(total_s),
        "credit_H_total": str(total_h),
        "net_S_minus_H": str(total_s - total_h),
    }


def plot_extracted_bu_rows(
    extracted: list[dict[str, Any]],
    plots_dir: Path,
    top_n: int,
    extract_bu_keys: set[str],
) -> list[str]:
    if not extracted:
        return []

    import matplotlib.pyplot as plt

    plots_dir.mkdir(parents=True, exist_ok=True)
    created_files: list[str] = []

    by_bu = Counter(row["bu_key"] for row in extracted)
    by_partner = Counter(row["partner_name"] for row in extracted if row["partner_name"])
    by_country = Counter(
        row["country_prefix"] for row in extracted if row["country_prefix"] and row["country_prefix"] != "DE"
    )
    by_konto_abs_sum: dict[str, Decimal] = {}
    month_bu_net: dict[tuple[str, str], Decimal] = {}

    for row in extracted:
        konto = row["konto"]
        amount = Decimal(row["amount"])
        sign = row["sign"]
        signed = signed_amount(amount, sign)
        month = row["month_mm"] or "??"
        bu = row["bu_key"]
        month_bu_net[(month, bu)] = month_bu_net.get((month, bu), Decimal("0")) + signed
        if konto:
            by_konto_abs_sum[konto] = by_konto_abs_sum.get(konto, Decimal("0")) + abs(signed)

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))

    # 1) Monthly net amount per BU key
    ax = axes[0, 0]
    months = sorted({m for m, _ in month_bu_net.keys()})
    keys = sorted(by_bu.keys())
    x = list(range(len(months)))
    width = 0.8 / max(1, len(keys))
    for idx_key, key in enumerate(keys):
        vals = [float(month_bu_net.get((m, key), Decimal("0"))) for m in months]
        xpos = [val + (idx_key - (len(keys) - 1) / 2) * width for val in x]
        ax.bar(xpos, vals, width=width, label=f"BU {key}")
    ax.set_xticks(x)
    ax.set_xticklabels(months)
    ax.set_title("Net Umsatz by Month and BU-Key")
    ax.set_xlabel("Month (MM)")
    ax.set_ylabel("Net amount (S positive, H negative)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    # 2) Top partner names
    ax = axes[0, 1]
    partner_items = by_partner.most_common(top_n)
    labels = [item[0] for item in partner_items]
    values = [item[1] for item in partner_items]
    if labels:
        ax.barh(labels[::-1], values[::-1])
    ax.set_title("Top Partners in Extracted Rows")
    ax.set_xlabel("Count")

    # 3) Top konto by absolute turnover
    ax = axes[1, 0]
    konto_items = sorted(by_konto_abs_sum.items(), key=lambda item: item[1], reverse=True)[:top_n]
    labels = [item[0] for item in konto_items]
    values = [float(item[1]) for item in konto_items]
    if labels:
        ax.bar(labels, values)
        ax.tick_params(axis="x", rotation=45)
    ax.set_title("Top Konto by Absolute Turnover")
    ax.set_ylabel("Absolute amount")

    # 4) Country split + BU count annotation
    ax = axes[1, 1]
    country_items = by_country.most_common(top_n)
    labels = [item[0] for item in country_items]
    values = [item[1] for item in country_items]
    if labels:
        ax.bar(labels, values)
    ax.set_title("Non-DE Country Prefixes (UStID Bestimmung)")
    ax.set_ylabel("Count")
    annotation = ", ".join([f"{key}:{count}" for key, count in sorted(by_bu.items())])
    ax.text(0.01, 0.95, f"BU counts: {annotation}", transform=ax.transAxes, va="top", fontsize=10)

    fig.tight_layout()
    key_suffix = "_".join(sorted(extract_bu_keys))
    out_file = plots_dir / f"bu_{key_suffix}_overview.png"
    fig.savefig(out_file, dpi=150)
    plt.close(fig)
    created_files.append(str(out_file))

    return created_files


def evaluate(
    bookings: ExtfFile,
    master: ExtfFile,
    top_n: int,
    igl_bu_keys: set[str],
    extract_bu_keys: set[str],
    plots_dir: Path,
    write_extracted_csv: bool,
) -> dict[str, Any]:
    bookings_idx = {name: i for i, name in enumerate(bookings.header)}
    master_idx = {name: i for i, name in enumerate(master.header)}

    total_s = Decimal("0")
    total_h = Decimal("0")
    by_month = Counter()
    by_konto = Counter()
    by_gegenkonto = Counter()
    by_text = Counter()
    by_name = Counter()
    fee_like_rows = 0

    for row in bookings.rows:
        amount = money_to_decimal(row_get(row, bookings_idx, "Umsatz"))
        sign = row_get(row, bookings_idx, "Soll-/Haben-Kennzeichen")
        if sign == "S":
            total_s += amount
        elif sign == "H":
            total_h += amount

        belegdatum = row_get(row, bookings_idx, "Belegdatum")
        if len(belegdatum) == 4 and belegdatum.isdigit():
            by_month[belegdatum[2:4]] += 1

        konto = row_get(row, bookings_idx, "Konto")
        gegenkonto = row_get(row, bookings_idx, "Gegenkonto (ohne BU-Schlüssel)")
        text = row_get(row, bookings_idx, "Buchungstext")

        if konto:
            by_konto[konto] += 1
        if gegenkonto:
            by_gegenkonto[gegenkonto] += 1
        if text:
            by_text[text] += 1
            if "Gebühr" in text or "Fee" in text:
                fee_like_rows += 1

        for i in range(1, 9):
            art = row_get(row, bookings_idx, f"Beleginfo-Art {i}")
            inhalt = row_get(row, bookings_idx, f"Beleginfo-Inhalt {i}")
            if art == "Name" and inhalt:
                by_name[inhalt] += 1

    extracted_rows = extract_bu_rows(bookings.rows, bookings_idx, extract_bu_keys)
    extracted_summaries = summarize_extracted_amounts(extracted_rows)
    plot_files = plot_extracted_bu_rows(
        extracted_rows,
        plots_dir=plots_dir,
        top_n=top_n,
        extract_bu_keys=extract_bu_keys,
    )
    extracted_csv_path = None
    if write_extracted_csv and extracted_rows:
        plots_dir.mkdir(parents=True, exist_ok=True)
        key_suffix = "_".join(sorted(extract_bu_keys))
        extracted_csv_path = plots_dir / f"bu_{key_suffix}_rows.csv"
        with extracted_csv_path.open("w", encoding="utf-8", newline="") as handle:
            fieldnames = list(extracted_rows[0].keys())
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(extracted_rows)

    master_accounts: list[str] = []
    master_names: list[str] = []
    for row in master.rows:
        konto = row_get(row, master_idx, "Konto")
        name_company = row_get(row, master_idx, "Name (Adressatentyp Unternehmen)")
        name_person = row_get(row, master_idx, "Name (Adressatentyp natürl. Person)")
        firstname_person = row_get(row, master_idx, "Vorname (Adressatentyp natürl. Person)")
        name_unknown = row_get(row, master_idx, "Name (Adressatentyp keine Angabe)")
        name = name_company or " ".join([firstname_person, name_person]).strip() or name_unknown
        if konto:
            master_accounts.append(konto)
        master_names.append(name)

    set_master = set(master_accounts)
    set_booking_konto = {
        row_get(row, bookings_idx, "Konto")
        for row in bookings.rows
        if row_get(row, bookings_idx, "Konto")
    }
    set_booking_gegenkonto = {
        row_get(row, bookings_idx, "Gegenkonto (ohne BU-Schlüssel)")
        for row in bookings.rows
        if row_get(row, bookings_idx, "Gegenkonto (ohne BU-Schlüssel)")
    }
    set_booking_accounts = set_booking_konto | set_booking_gegenkonto

    # iGL candidates:
    # 1) broad: non-DE UStID in EU-Mitgliedstaat/UStID (Bestimmung)
    # 2) tax0: broad + EU-Steuersatz (Bestimmung)=0
    # 3) strict: tax0 + BU-Schlüssel in configured keys
    rows_igl_non_de: list[list[str]] = []
    rows_igl_tax0: list[list[str]] = []
    rows_igl_strict: list[list[str]] = []
    countries_non_de = Counter()
    countries_tax0 = Counter()
    countries_strict = Counter()

    for row in bookings.rows:
        ustid = row_get(row, bookings_idx, "EU-Mitgliedstaat u. UStID (Bestimmung)")
        eu_tax_rate = row_get(row, bookings_idx, "EU-Steuersatz (Bestimmung)")
        bu_key = row_get(row, bookings_idx, "BU-Schlüssel")
        country = parse_country_prefix(ustid)
        is_non_de = bool(country) and country != "DE"

        if not is_non_de:
            continue

        rows_igl_non_de.append(row)
        countries_non_de[country] += 1

        if eu_tax_rate == "0":
            rows_igl_tax0.append(row)
            countries_tax0[country] += 1

            if bu_key in igl_bu_keys:
                rows_igl_strict.append(row)
                countries_strict[country] += 1

    return {
        "input": {
            "bookings_file": str(bookings.path),
            "master_file": str(master.path),
        },
        "bookings": {
            "meta_type": bookings.meta[3] if len(bookings.meta) > 3 else "",
            "rows": len(bookings.rows),
            "debit_S_total": str(total_s),
            "credit_H_total": str(total_h),
            "net_S_minus_H": str(total_s - total_h),
            "top_konto": by_konto.most_common(top_n),
            "top_gegenkonto": by_gegenkonto.most_common(top_n),
            "top_month_mm": by_month.most_common(),
            "top_booking_text": by_text.most_common(top_n),
            "fee_like_rows": fee_like_rows,
            "top_names_in_beleginfo": by_name.most_common(top_n),
        },
        "master_data": {
            "meta_type": master.meta[3] if len(master.meta) > 3 else "",
            "rows": len(master.rows),
            "konto_unique": len(set_master),
            "konto_min": min(master_accounts) if master_accounts else None,
            "konto_max": max(master_accounts) if master_accounts else None,
            "blank_name_rows": sum(1 for name in master_names if not name),
            "sample_names": master_names[: min(12, len(master_names))],
        },
        "crosscheck": {
            "booking_unique_accounts_combined": len(set_booking_accounts),
            "booking_accounts_found_in_master": len(set_booking_accounts & set_master),
            "booking_accounts_not_in_master": sorted(set_booking_accounts - set_master),
        },
        "innergemeinschaftliche_lieferungen": {
            "rule_note": (
                "broad=non-DE UStID prefix in 'EU-Mitgliedstaat u. UStID (Bestimmung)'; "
                "tax0=broad + EU-Steuersatz(Bestimmung)=0; "
                f"strict=tax0 + BU-Schlüssel in {sorted(igl_bu_keys)}"
            ),
            "broad_non_de_ustid": {
                "rows": len(rows_igl_non_de),
                **summarize_amounts(rows_igl_non_de, bookings_idx),
                "top_country_prefix": countries_non_de.most_common(top_n),
            },
            "tax0_non_de_ustid": {
                "rows": len(rows_igl_tax0),
                **summarize_amounts(rows_igl_tax0, bookings_idx),
                "top_country_prefix": countries_tax0.most_common(top_n),
            },
            "strict_with_bu_key": {
                "rows": len(rows_igl_strict),
                **summarize_amounts(rows_igl_strict, bookings_idx),
                "top_country_prefix": countries_strict.most_common(top_n),
                "bu_keys": sorted(igl_bu_keys),
            },
        },
        "bu_key_extract": {
            "keys": sorted(extract_bu_keys),
            "rows": len(extracted_rows),
            **extracted_summaries,
            "by_bu_key": Counter(row["bu_key"] for row in extracted_rows).most_common(top_n),
            "by_sign": Counter(row["sign"] for row in extracted_rows).most_common(),
            "top_partner_names": Counter(
                row["partner_name"] for row in extracted_rows if row["partner_name"]
            ).most_common(top_n),
            "top_konto": Counter(row["konto"] for row in extracted_rows if row["konto"]).most_common(top_n),
            "top_country_prefix_non_de": Counter(
                row["country_prefix"]
                for row in extracted_rows
                if row["country_prefix"] and row["country_prefix"] != "DE"
            ).most_common(top_n),
            "plots": plot_files,
            "extracted_csv": str(extracted_csv_path) if extracted_csv_path else None,
            "preview": extracted_rows[: min(20, len(extracted_rows))],
        },
    }


def print_text_summary(result: dict[str, Any]) -> None:
    input_info = result["input"]
    bu_extract = result["bu_key_extract"]
    print("DATEV Evaluation")
    print(f"- Bookings file: {input_info['bookings_file']}")
    print(f"- Master file:   {input_info['master_file']}")
    print("")
    print("BU Extract")
    print(
        f"- Keys={bu_extract['keys']} rows={bu_extract['rows']} "
        f"net={bu_extract['net_S_minus_H']}"
    )
    print(f"- Top partners: {bu_extract['top_partner_names'][:5]}")
    print(f"- Plots: {bu_extract['plots']}")
    if bu_extract["extracted_csv"]:
        print(f"- Extracted CSV: {bu_extract['extracted_csv']}")


def build_display_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "input": result["input"],
        "bu_key_extract": result["bu_key_extract"],
    }


def main() -> int:
    args = parse_args()
    datev_dir = args.datev_dir.resolve()

    bookings_path = (
        normalize_path(args.bookings_file, datev_dir)
        if args.bookings_file
        else find_latest_file_by_prefix(datev_dir, "EXTF_Buchungsstapel_")
    )
    master_path = (
        normalize_path(args.master_file, datev_dir)
        if args.master_file
        else find_latest_file_by_prefix(datev_dir, "EXTF_GP_Stamm_")
    )

    bookings = read_extf(bookings_path, args.encoding)
    master = read_extf(master_path, args.encoding)
    igl_bu_keys = {key.strip() for key in args.igl_bu_keys.split(",") if key.strip()}
    result = evaluate(
        bookings=bookings,
        master=master,
        top_n=args.top_n,
        igl_bu_keys=igl_bu_keys,
        extract_bu_keys={key.strip() for key in args.extract_bu_keys.split(",") if key.strip()},
        plots_dir=args.plots_dir,
        write_extracted_csv=args.write_extracted_csv,
    )
    display_result = build_display_result(result)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(display_result, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(display_result, ensure_ascii=False, indent=2))
    else:
        print_text_summary(display_result)
        print("")
        print("Full JSON")
        print(json.dumps(display_result, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
