from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.accounting.upload_archive import load_upload_runs


def _file_count(run: dict[str, Any], key: str) -> int:
    files = run.get(key)
    return len(files) if isinstance(files, list) else 0


def _summary_text(run: dict[str, Any]) -> str:
    summary = run.get("summary")
    if not isinstance(summary, dict):
        return ""
    rows = summary.get("rows")
    owners = summary.get("owners")
    parts: list[str] = []
    if rows is not None:
        parts.append(f"{rows} Zeilen")
    if isinstance(owners, list) and owners:
        owner_labels = [
            f"{row.get('Finom Karteninhaber')}: {row.get('Anzahl')}"
            for row in owners
            if isinstance(row, dict)
        ]
        if owner_labels:
            parts.append(", ".join(owner_labels))
    return " | ".join(parts)


def _runs_table(runs: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Archiviert": run.get("archived_at", ""),
                "Workflow": run.get("workflow", ""),
                "Status": run.get("status", ""),
                "Inputs": _file_count(run, "inputs"),
                "Outputs": _file_count(run, "outputs"),
                "Run": run.get("run_id", ""),
                "Summary": _summary_text(run),
            }
            for run in runs
        ]
    )


def _render_file_downloads(run: dict[str, Any], key: str, title: str) -> None:
    files = run.get(key)
    if not isinstance(files, list) or not files:
        return

    st.markdown(f"**{title}**")
    for file_info in files:
        if not isinstance(file_info, dict):
            continue
        path = Path(str(file_info.get("path", "")))
        if not path.exists():
            st.caption(f"{file_info.get('filename', '-')}: Datei fehlt")
            continue
        st.download_button(
            str(file_info.get("filename") or path.name),
            data=path.read_bytes(),
            file_name=str(file_info.get("filename") or path.name),
            key=f"download_{run.get('run_id')}_{key}_{path.name}",
        )


def render_upload_archive_view() -> None:
    st.title("📚 Accounting / Upload Archiv")
    st.caption("Überblick über archivierte Upload-Läufe und erzeugte Dateien.")

    runs = load_upload_runs()
    if not runs:
        st.info("Noch keine archivierten Uploads gefunden.")
        return

    workflows = sorted({str(run.get("workflow", "")) for run in runs if run.get("workflow")})
    selected_workflow = st.selectbox("Workflow", ["Alle", *workflows])
    filtered_runs = [
        run
        for run in runs
        if selected_workflow == "Alle" or run.get("workflow") == selected_workflow
    ]

    st.dataframe(_runs_table(filtered_runs), width="stretch", hide_index=True)

    st.markdown("**Details & Downloads**")
    for run in filtered_runs:
        label = f"{run.get('archived_at', '-')} | {run.get('workflow', '-')} | {run.get('run_id', '-')}"
        with st.expander(label, expanded=False):
            if run.get("error"):
                st.error(str(run["error"]))
            if _summary_text(run):
                st.caption(_summary_text(run))
            _render_file_downloads(run, "inputs", "Originaldateien")
            _render_file_downloads(run, "outputs", "Erzeugte Dateien")
