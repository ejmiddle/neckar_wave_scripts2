from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pandas as pd

CARD_OWNERS = {
    "0937": "Andi",
    "0119": "Hugo",
    "7000": "Jan",
    "8242": "Till",
}

FINOM_EXPORT_COLUMNS = [
    "Auftraggeber/Empfänger",
    "Tags",
    "Kartennummer",
    "Ursprungswährung",
    "Ursprungsbetrag",
    "Zahlungswährung",
    "Zahlungsbetrag",
    "Transaktions-ID",
]


@dataclass(frozen=True)
class FinomOpenPaymentsResult:
    enriched: pd.DataFrame
    owner_summary: pd.DataFrame
    largest_positions: pd.DataFrame
    xlsx_bytes: bytes


def normalize_name(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def parse_german_amount(value: object) -> float:
    text = str(value).strip().replace(".", "").replace(",", ".")
    return round(float(text), 2)


def parse_open_date(value: object) -> pd.Timestamp:
    return pd.to_datetime(value, format="%d.%m.%Y", errors="coerce")


def parse_finom_date(value: object) -> pd.Timestamp:
    return pd.to_datetime(value, format="%d.%m.%Y", errors="coerce")


def card_owner(value: object) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    return CARD_OWNERS.get(digits[-4:], "")


def _read_csv(source: str | bytes | Path | BinaryIO, *, sep: str, encoding: str | None = None) -> pd.DataFrame:
    if isinstance(source, bytes):
        return pd.read_csv(BytesIO(source), sep=sep, encoding=encoding)
    return pd.read_csv(source, sep=sep, encoding=encoding)


def read_open_payments_csv(source: str | bytes | Path | BinaryIO) -> pd.DataFrame:
    df = _read_csv(source, sep=";", encoding="utf-8-sig")
    return df.loc[:, ~df.columns.str.startswith("Unnamed")]


def read_finom_statement_csv(source: str | bytes | Path | BinaryIO) -> pd.DataFrame:
    return _read_csv(source, sep=",")


def match_score(open_row: pd.Series, finom_row: pd.Series) -> tuple[float, str]:
    open_name = open_row["_norm_name"]
    finom_name = finom_row["_norm_name"]
    ratio = SequenceMatcher(None, open_name, finom_name).ratio()

    if open_name and finom_name and (open_name in finom_name or finom_name in open_name):
        ratio = max(ratio, 0.98)

    date_diff = abs((open_row["_date"] - finom_row["_date"]).days)
    date_points = max(0.0, 1.0 - min(date_diff, 5) / 5)
    score = (ratio * 0.75) + (date_points * 0.25)
    reason = f"name={ratio:.2f}; date_diff_days={date_diff}"
    return score, reason


def enrich_open_payments(open_df: pd.DataFrame, finom_df: pd.DataFrame) -> pd.DataFrame:
    open_df = open_df.copy()
    finom_df = finom_df.copy()

    open_df["_amount"] = open_df["Betrag"].apply(parse_german_amount)
    open_df["_date"] = open_df["Bezahldatum"].apply(parse_open_date)
    open_df["_norm_name"] = open_df["Name"].apply(normalize_name)

    finom_df["_amount"] = finom_df["Zahlungsbetrag"].astype(float).round(2)
    finom_df["_date"] = finom_df["Buchungsdatum"].apply(parse_finom_date)
    finom_df["_norm_name"] = finom_df["Auftraggeber/Empfänger"].apply(normalize_name)

    rows: list[dict[str, object]] = []
    used_finom_indices: set[int] = set()

    for _, open_row in open_df.iterrows():
        candidates = finom_df[
            (finom_df["_amount"] == open_row["_amount"])
            & (~finom_df.index.isin(used_finom_indices))
        ]

        best = None
        match_status = "no amount match"
        if not candidates.empty:
            scored = [
                (*match_score(open_row, finom_row), finom_index)
                for finom_index, finom_row in candidates.iterrows()
            ]
            score, _, finom_index = sorted(scored, reverse=True)[0]
            best = finom_df.loc[finom_index]
            used_finom_indices.add(finom_index)
            match_status = "matched" if score >= 0.65 else "amount match, weak name/date"

        output_row: dict[str, object] = {
            "Finom Karteninhaber": card_owner(best["Kartennummer"]) if best is not None else "",
            "Open Status": open_row["Status"],
            "Open Name": open_row["Name"],
            "Open Beschreibung": open_row["Beschreibung"],
            "Open Bezahldatum": open_row["Bezahldatum"],
            "Open Betrag": open_row["Betrag"],
            "_Match Status": match_status,
            "_Betrag Numeric": open_row["_amount"],
            "_Abs Betrag": abs(open_row["_amount"]),
        }

        for column in FINOM_EXPORT_COLUMNS:
            output_row[f"Finom {column}"] = best[column] if best is not None else ""

        rows.append(output_row)

    return pd.DataFrame(rows)


def display_enriched_frame(enriched: pd.DataFrame) -> pd.DataFrame:
    return enriched.drop(
        columns=[column for column in enriched.columns if column.startswith("_")],
        errors="ignore",
    )


def summarize_by_owner(enriched: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        enriched.assign(
            **{
                "Finom Karteninhaber": enriched["Finom Karteninhaber"].fillna("").replace("", "Ohne Karte")
            }
        )
        .groupby("Finom Karteninhaber", dropna=False)
        .agg(
            Anzahl=("Open Name", "count"),
            Summe_offen=("_Betrag Numeric", "sum"),
            Groesste_Position=("_Abs Betrag", "max"),
        )
        .reset_index()
        .sort_values("Groesste_Position", ascending=False)
    )
    return grouped


def largest_positions(enriched: pd.DataFrame, *, top_n_per_owner: int = 5) -> pd.DataFrame:
    frame = enriched.copy()
    frame["Finom Karteninhaber"] = (
        frame["Finom Karteninhaber"].fillna("").replace("", "Ohne Karte")
    )
    frame = frame.sort_values(["Finom Karteninhaber", "_Abs Betrag"], ascending=[True, False])
    columns = [
        "Finom Karteninhaber",
        "Open Name",
        "Open Beschreibung",
        "Open Bezahldatum",
        "Open Betrag",
        "Finom Auftraggeber/Empfänger",
        "Finom Tags",
        "Finom Kartennummer",
        "Finom Transaktions-ID",
        "_Abs Betrag",
    ]
    return frame.groupby("Finom Karteninhaber", group_keys=False).head(top_n_per_owner)[columns]


def dataframe_to_xlsx_bytes(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    excel_df = df.copy()
    if "Open Bezahldatum" in excel_df.columns:
        excel_df["Open Bezahldatum"] = pd.to_datetime(
            excel_df["Open Bezahldatum"],
            format="%d.%m.%Y",
            errors="coerce",
        ).dt.date
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        excel_df.to_excel(writer, index=False, sheet_name="Open payments enriched")
        worksheet = writer.sheets["Open payments enriched"]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        if "Open Bezahldatum" in excel_df.columns:
            date_col = excel_df.columns.get_loc("Open Bezahldatum") + 1
            for row in worksheet.iter_rows(min_row=2, min_col=date_col, max_col=date_col):
                row[0].number_format = "DD.MM.YYYY"

        for column_cells in worksheet.columns:
            header = str(column_cells[0].value or "")
            max_len = max(len(str(cell.value or "")) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(
                max(max_len + 2, len(header) + 2),
                42,
            )
    return buffer.getvalue()


def build_finom_open_payments_result(
    open_payments_source: str | bytes | Path | BinaryIO,
    finom_statement_source: str | bytes | Path | BinaryIO,
) -> FinomOpenPaymentsResult:
    open_df = read_open_payments_csv(open_payments_source)
    finom_df = read_finom_statement_csv(finom_statement_source)
    enriched = enrich_open_payments(open_df, finom_df)
    display_df = display_enriched_frame(enriched)
    return FinomOpenPaymentsResult(
        enriched=display_df,
        owner_summary=summarize_by_owner(enriched),
        largest_positions=largest_positions(enriched),
        xlsx_bytes=dataframe_to_xlsx_bytes(display_df),
    )
