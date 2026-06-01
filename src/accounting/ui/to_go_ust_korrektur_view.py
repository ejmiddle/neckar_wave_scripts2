from __future__ import annotations

import streamlit as st

from src.accounting.to_go_ust_korrektur import (
    ToGoUstKorrekturError,
    analyze_to_go_ust_korrektur_csv,
    create_to_go_ust_korrektur_workbook,
)


def render_to_go_ust_korrektur_view() -> None:
    st.title("TO GO UST Korrektur")
    st.caption(
        "Upload a ready2order CSV export. The output groups all `artikel_bezeichnung` variants "
        "with `TO GO` and `Kuh`, plus all `TO GO` variants without `Kuh`, and sums `artikel_summe`."
    )
    st.info(
        "Die CSV-Datei im ready2order Frontend unter "
        "`Einstellungen -> Daten -> Datenexport -> Daten pro Monat herunterladen` herunterladen."
    )

    uploaded_file = st.file_uploader(
        "ready2order CSV export",
        type=["csv"],
        accept_multiple_files=False,
        key="to_go_ust_korrektur_upload",
    )
    if uploaded_file is None:
        return

    try:
        result = analyze_to_go_ust_korrektur_csv(uploaded_file.getvalue())
        workbook = create_to_go_ust_korrektur_workbook(result)
    except ToGoUstKorrekturError as exc:
        st.error(f"Could not process CSV: {exc}")
        return

    st.success(f"Processed {result.row_count} CSV rows.")
    st.dataframe(result.overview, width="stretch", hide_index=True)

    left, right = st.columns(2)
    with left:
        st.markdown("**TO GO + Kuh**")
        st.dataframe(result.to_go_kuh_summary, width="stretch", hide_index=True)
    with right:
        st.markdown("**TO GO ohne Kuh**")
        st.dataframe(result.to_go_without_kuh_summary, width="stretch", hide_index=True)

    st.download_button(
        "Download XLSX",
        data=workbook,
        file_name=_output_file_name(uploaded_file.name),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )


def _output_file_name(upload_name: str) -> str:
    base_name = upload_name.rsplit(".", 1)[0].strip() or "to-go-ust-korrektur"
    return f"{base_name}-to-go-ust-korrektur.xlsx"
