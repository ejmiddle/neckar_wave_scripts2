from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pandas as pd
from pandas.errors import ParserError

REQUIRED_COLUMNS = ("artikel_bezeichnung", "artikel_menge", "artikel_summe")
ENCODINGS = ("utf-8-sig", "utf-8", "latin1")
SUMMARY_COLUMNS = ["artikel_bezeichnung", "zeilen", "menge_summe", "brutto_summe"]
DETAIL_COLUMNS = [
    "rechnung_nummer",
    "rechnung_datum",
    "buchung_datum",
    "artikel_bezeichnung",
    "warengruppe_bezeichnung",
    "artikel_menge",
    "artikel_preisProEinheit",
    "artikel_summe",
    "artikel_summe_num",
    "rechnung_stornoDatum",
    "retourbuchung_boolean",
    "beleg_typ",
    "rechnug_zahlungsart",
    "tisch_kunde",
    "product_id",
    "bill_id",
]


class ToGoUstKorrekturError(ValueError):
    """Raised when a ready2order CSV cannot be processed for this report."""


@dataclass(frozen=True)
class ToGoUstKorrekturResult:
    overview: pd.DataFrame
    to_go_kuh_summary: pd.DataFrame
    to_go_without_kuh_summary: pd.DataFrame
    to_go_kuh_rows: pd.DataFrame
    to_go_without_kuh_rows: pd.DataFrame
    row_count: int


def read_ready2order_csv(source: str | Path | bytes | BinaryIO) -> pd.DataFrame:
    raw = _read_bytes(source)
    last_decode_error: UnicodeDecodeError | None = None
    for encoding in ENCODINGS:
        try:
            return pd.read_csv(
                BytesIO(raw),
                sep=";",
                dtype=str,
                keep_default_na=False,
                encoding=encoding,
            )
        except UnicodeDecodeError as exc:
            last_decode_error = exc
        except ParserError as exc:
            raise ToGoUstKorrekturError(f"Could not parse CSV: {exc}") from exc
    if last_decode_error is not None:
        raise ToGoUstKorrekturError("Could not decode CSV with UTF-8 or latin1.") from last_decode_error
    raise ToGoUstKorrekturError("Could not read CSV input.")


def build_to_go_ust_korrektur(df: pd.DataFrame) -> ToGoUstKorrekturResult:
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ToGoUstKorrekturError(f"Missing required CSV columns: {', '.join(missing_columns)}")

    prepared = df.copy()
    prepared["artikel_summe_num"] = _parse_german_number(
        prepared["artikel_summe"],
        column_name="artikel_summe",
    )
    prepared["artikel_menge_num"] = _parse_german_number(
        prepared["artikel_menge"],
        column_name="artikel_menge",
    )

    names = prepared["artikel_bezeichnung"].fillna("")
    has_to_go = names.str.contains("TO GO", case=False, na=False, regex=False)
    has_kuh = names.str.contains("Kuh", case=False, na=False, regex=False)

    to_go_kuh_rows = prepared[has_to_go & has_kuh].copy()
    to_go_without_kuh_rows = prepared[has_to_go & ~has_kuh].copy()

    to_go_kuh_summary = _summarize_variants(to_go_kuh_rows)
    to_go_without_kuh_summary = _summarize_variants(to_go_without_kuh_rows)
    overview = pd.DataFrame(
        [
            _overview_row("TO GO + Kuh", to_go_kuh_summary, to_go_kuh_rows),
            _overview_row("TO GO ohne Kuh", to_go_without_kuh_summary, to_go_without_kuh_rows),
        ]
    )

    return ToGoUstKorrekturResult(
        overview=overview,
        to_go_kuh_summary=to_go_kuh_summary,
        to_go_without_kuh_summary=to_go_without_kuh_summary,
        to_go_kuh_rows=_select_detail_columns(to_go_kuh_rows),
        to_go_without_kuh_rows=_select_detail_columns(to_go_without_kuh_rows),
        row_count=len(prepared),
    )


def create_to_go_ust_korrektur_workbook(result: ToGoUstKorrekturResult) -> bytes:
    sheets = [
        ("Overview", result.overview),
        ("TO GO + Kuh summary", result.to_go_kuh_summary),
        ("TO GO no Kuh summary", result.to_go_without_kuh_summary),
        ("TO GO + Kuh rows", result.to_go_kuh_rows),
        ("TO GO no Kuh rows", result.to_go_without_kuh_rows),
    ]
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, frame in sheets:
            frame.to_excel(writer, sheet_name=sheet_name, index=False)

        for sheet in writer.book.worksheets:
            _format_sheet(sheet)

    return output.getvalue()


def analyze_to_go_ust_korrektur_csv(source: str | Path | bytes | BinaryIO) -> ToGoUstKorrekturResult:
    return build_to_go_ust_korrektur(read_ready2order_csv(source))


def _read_bytes(source: str | Path | bytes | BinaryIO) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    return source.read()


def _parse_german_number(values: pd.Series, *, column_name: str) -> pd.Series:
    normalized = (
        values.fillna("")
        .astype(str)
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .replace("", "0")
    )
    parsed = pd.to_numeric(normalized, errors="coerce")
    invalid = values[parsed.isna()].astype(str).head(3).tolist()
    if invalid:
        examples = ", ".join(repr(value) for value in invalid)
        raise ToGoUstKorrekturError(f"Column {column_name!r} contains non-numeric values: {examples}")
    return parsed.astype(float)


def _summarize_variants(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    result = (
        frame.groupby("artikel_bezeichnung", dropna=False)
        .agg(
            zeilen=("artikel_bezeichnung", "size"),
            menge_summe=("artikel_menge_num", "sum"),
            brutto_summe=("artikel_summe_num", "sum"),
        )
        .reset_index()
        .sort_values(["brutto_summe", "artikel_bezeichnung"], ascending=[False, True])
    )
    result["menge_summe"] = result["menge_summe"].round(4)
    result["brutto_summe"] = result["brutto_summe"].round(2)
    return result[SUMMARY_COLUMNS]


def _overview_row(label: str, summary: pd.DataFrame, rows: pd.DataFrame) -> dict[str, float | int | str]:
    return {
        "gruppe": label,
        "varianten": len(summary),
        "zeilen": len(rows),
        "menge_summe": round(float(rows["artikel_menge_num"].sum()), 4),
        "brutto_summe": round(float(rows["artikel_summe_num"].sum()), 2),
    }


def _select_detail_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[[column for column in DETAIL_COLUMNS if column in frame.columns]].copy()


def _format_sheet(sheet) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        max_len = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column)
        sheet.column_dimensions[letter].width = min(max(max_len + 2, 10), 45)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, float):
                cell.number_format = "#,##0.00"
