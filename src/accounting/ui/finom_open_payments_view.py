from __future__ import annotations

import pandas as pd
import streamlit as st

from src.accounting.finom_open_payments import build_finom_open_payments_result
from src.accounting.upload_archive import archive_upload_run, sha256_bytes

FINOM_ARCHIVE_WORKFLOW = "finom_open_payments"
FINOM_ARCHIVE_SESSION_KEY = "finom_open_payments_archived_hash"


def _format_euro(value: object) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{amount:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


def _render_owner_summary(summary: pd.DataFrame) -> None:
    if summary.empty:
        return
    frame = summary.copy()
    frame["Summe_offen"] = frame["Summe_offen"].map(_format_euro)
    frame["Groesste_Position"] = frame["Groesste_Position"].map(_format_euro)
    st.markdown("**Übersicht nach Karteninhaber**")
    st.dataframe(frame, width="stretch", hide_index=True)


def _render_largest_positions(positions: pd.DataFrame) -> None:
    if positions.empty:
        return
    frame = positions.drop(columns=["_Abs Betrag"], errors="ignore")
    st.markdown("**Größte offene Positionen je Karteninhaber**")
    st.dataframe(frame, width="stretch", hide_index=True)


def _summary_for_archive(result) -> dict[str, object]:
    return {
        "rows": len(result.enriched),
        "owners": result.owner_summary.to_dict(orient="records"),
    }


def render_finom_open_payments_view() -> None:
    st.title("💳 Accounting / Finom offene Zahlungen")
    st.caption(
        "Finom-Kontoauszug mit offenen Zahlungen aus dem Accounting-System abgleichen und als XLSX exportieren."
    )

    col_open, col_finom = st.columns(2)
    with col_open:
        open_payments_file = st.file_uploader(
            "Offene Zahlungen CSV",
            type=["csv"],
            key="finom_open_payments_upload",
        )
    with col_finom:
        finom_statement_file = st.file_uploader(
            "Finom Kontoauszug CSV",
            type=["csv"],
            key="finom_statement_upload",
        )

    if open_payments_file is None or finom_statement_file is None:
        st.info("Lade beide CSV-Dateien hoch, um den Abgleich zu erstellen.")
        return

    open_payments_bytes = open_payments_file.getvalue()
    finom_statement_bytes = finom_statement_file.getvalue()
    upload_hash = sha256_bytes(open_payments_bytes + finom_statement_bytes)

    try:
        result = build_finom_open_payments_result(
            open_payments_bytes,
            finom_statement_bytes,
        )
    except Exception as exc:
        archive_key = f"{upload_hash}:error"
        if st.session_state.get(FINOM_ARCHIVE_SESSION_KEY) != archive_key:
            archive_upload_run(
                workflow=FINOM_ARCHIVE_WORKFLOW,
                input_files={
                    "open_payments": (open_payments_file.name, open_payments_bytes),
                    "finom_statement": (finom_statement_file.name, finom_statement_bytes),
                },
                status="error",
                error=str(exc),
            )
            st.session_state[FINOM_ARCHIVE_SESSION_KEY] = archive_key
        st.error(f"Die Dateien konnten nicht verarbeitet werden: {exc}")
        return

    archive_key = f"{upload_hash}:ok"
    if st.session_state.get(FINOM_ARCHIVE_SESSION_KEY) != archive_key:
        archived = archive_upload_run(
            workflow=FINOM_ARCHIVE_WORKFLOW,
            input_files={
                "open_payments": (open_payments_file.name, open_payments_bytes),
                "finom_statement": (finom_statement_file.name, finom_statement_bytes),
            },
            output_files={
                "result": ("open_payments_enriched_from_finom.xlsx", result.xlsx_bytes),
            },
            summary=_summary_for_archive(result),
            status="ok",
        )
        st.session_state[FINOM_ARCHIVE_SESSION_KEY] = archive_key
        st.caption(f"Archiviert als `{archived.run_id}`.")

    st.download_button(
        "XLSX herunterladen",
        data=result.xlsx_bytes,
        file_name="open_payments_enriched_from_finom.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )

    _render_owner_summary(result.owner_summary)
    _render_largest_positions(result.largest_positions)

    with st.expander("Vorschau Export", expanded=False):
        st.dataframe(result.enriched, width="stretch", hide_index=True)
