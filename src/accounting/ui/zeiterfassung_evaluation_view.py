from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from src.accounting.zeiterfassung_evaluation import (
    DEFAULT_OUTPUT_DIR,
    NotionDatabaseRef,
    discover_child_databases,
    evaluate_hours,
    export_databases_cached,
    parse_month_start,
    prune_stale_cached_exports,
)

DEFAULT_NOTION_PAGE_LINK = (
    "https://www.notion.so/suedseite/Stunden-Erfassung-Altstadt-1154e28bdf9e8115a0fef60ec21bcbca"
)
DEFAULT_WIEBLINGEN_NOTION_PAGE_LINK = (
    "https://www.notion.so/suedseite/Stunden-Erfassung-Wieblingen-1214e28bdf9e80d3ae0eeac72b50f6e2"
)
DATABASES_STATE_KEY = "zeiterfassung_eval_databases"
LAST_RESULT_STATE_KEY = "zeiterfassung_eval_last_result"


def _database_label(database: NotionDatabaseRef) -> str:
    prefix = f"{database.source_label} - " if database.source_label else ""
    return f"{prefix}{database.title} ({database.database_id[:8]})"


def _database_month_start(database: NotionDatabaseRef) -> pd.Timestamp | pd.NaT:
    return parse_month_start(database.title)


def _month_option(month_start: pd.Timestamp) -> str:
    return month_start.strftime("%Y-%m")


def _selected_month_database_refs(
    databases: list[NotionDatabaseRef],
    selected_months: list[str],
) -> list[NotionDatabaseRef]:
    selected = set(selected_months)
    return [
        database
        for database in databases
        if _month_option(_database_month_start(database)) in selected
    ]


def _render_month_location_shift_chart(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No data available for chart.")
        return

    chart_df = df.copy()
    chart_df["_month_start"] = chart_df["Monat"].map(parse_month_start)
    chart_df = chart_df[chart_df["_month_start"].notna()].copy()
    if chart_df.empty:
        st.info("No chartable month data available.")
        return
    chart_df["Monat"] = chart_df["_month_start"].dt.strftime("%y-%m")
    month_order = sorted(chart_df["Monat"].unique())

    overall_df = (
        chart_df.groupby(["Monat", "Shift"], as_index=False)["Stunden"]
        .sum()
        .sort_values(["Monat", "Shift"])
    )
    overall_total_df = (
        chart_df.groupby("Monat", as_index=False)["Stunden"]
        .sum()
        .sort_values("Monat")
        .rename(columns={"Stunden": "Gesamtstunden"})
    )
    st.markdown("**Overall**")
    overall_bars = (
        alt.Chart(overall_df)
        .mark_bar()
        .encode(
            x=alt.X("Monat:N", sort=month_order, title="Month"),
            y=alt.Y("Stunden:Q", title="Hours"),
            color=alt.Color("Shift:N", title="Shift"),
            tooltip=["Monat:N", "Shift:N", alt.Tooltip("Stunden:Q", format=",.2f")],
        )
        .properties(height=280)
    )
    overall_total_line = (
        alt.Chart(overall_total_df)
        .mark_line(point=True, color="#111827", strokeWidth=2)
        .encode(
            x=alt.X("Monat:N", sort=month_order, title="Month"),
            y=alt.Y("Gesamtstunden:Q", title="Hours"),
            tooltip=["Monat:N", alt.Tooltip("Gesamtstunden:Q", format=",.2f")],
        )
    )
    overall_chart = alt.layer(overall_bars, overall_total_line).resolve_scale(y="shared")
    st.altair_chart(overall_chart, width="stretch")

    locations = sorted(chart_df["Location"].dropna().astype(str).unique())
    preferred_locations = [location for location in ["ALT", "WIE"] if location in locations]
    other_locations = [location for location in locations if location not in preferred_locations]

    for location in [*preferred_locations, *other_locations]:
        location_df = chart_df[chart_df["Location"].astype(str) == location]
        st.markdown(f"**{location}**")
        chart = (
            alt.Chart(location_df)
            .mark_bar()
            .encode(
                x=alt.X("Monat:N", sort=month_order, title="Month"),
                y=alt.Y("Stunden:Q", title="Hours"),
                color=alt.Color("Shift:N", title="Shift"),
                tooltip=["Monat:N", "Location:N", "Shift:N", alt.Tooltip("Stunden:Q", format=",.2f")],
            )
            .properties(height=280)
        )
        st.altair_chart(chart, width="stretch")


def render_zeiterfassung_evaluation_view() -> None:
    st.title("Evaluation Zeiterfassung")
    st.caption("Download selected Notion time-tracking databases and run basic hour evaluations.")

    with st.expander("Notion source settings", expanded=False):
        alt_link = st.text_input(
            "ALT Notion page link",
            value=st.session_state.get("zeiterfassung_eval_page_link", DEFAULT_NOTION_PAGE_LINK),
            key="zeiterfassung_eval_page_link",
        )
        wie_link = st.text_input(
            "WIE Notion page link",
            value=st.session_state.get(
                "zeiterfassung_eval_wie_page_link",
                DEFAULT_WIEBLINGEN_NOTION_PAGE_LINK,
            ),
            key="zeiterfassung_eval_wie_page_link",
        )
        st.caption("These links define the Notion source pages used for ALT and WIE.")

    if st.button("Load databases from links", type="secondary"):
        try:
            with st.spinner("Loading Notion databases..."):
                databases = [
                    *discover_child_databases(alt_link, source_label="ALT"),
                    *discover_child_databases(wie_link, source_label="WIE"),
                ]
            st.session_state[DATABASES_STATE_KEY] = databases
            st.success(f"Found {len(databases)} databases.")
        except Exception as exc:
            st.error(f"Could not load databases: {exc}")

    databases = st.session_state.get(DATABASES_STATE_KEY, [])
    if not databases:
        st.info("Load the Notion page first, then select the databases to download.")
        return

    invalid_databases = [
        database for database in databases if pd.isna(_database_month_start(database))
    ]
    if invalid_databases:
        st.error(
            "Could not determine the month from these Notion database names. "
            "Please rename them to include a clear month and year, for example `Zeiterfassung Mai 2026`."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Location": database.source_label,
                        "Database": database.title,
                        "ID": database.database_id,
                    }
                    for database in invalid_databases
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        return

    month_starts = sorted({_database_month_start(database) for database in databases})
    month_options = [_month_option(month_start) for month_start in month_starts]
    selected_months = st.multiselect(
        "Months to download",
        options=month_options,
        default=month_options,
        help="Months are parsed from the Notion database names. Each selected month downloads all matching ALT/WIE databases.",
    )
    selected_databases = _selected_month_database_refs(databases, selected_months)

    if selected_databases:
        st.caption(
            f"Selected {len(selected_months)} month(s), covering {len(selected_databases)} database(s). "
            f"Downloads and cache are stored below `{DEFAULT_OUTPUT_DIR}`."
        )
    else:
        st.warning("Select at least one month before starting the evaluation.")

    start_col, refresh_col = st.columns([1, 1])
    start_clicked = start_col.button("Start evaluation", type="primary", disabled=not selected_databases)
    refresh_clicked = refresh_col.button("Refresh downloaded data", type="secondary", disabled=not selected_databases)

    if start_clicked or refresh_clicked:
        try:
            force_refresh = refresh_clicked
            spinner_text = (
                "Refreshing selected databases from Notion and evaluating hours..."
                if force_refresh
                else "Loading cached downloads when available and evaluating hours..."
            )
            with st.spinner(spinner_text):
                removed_cache_dirs = (
                    prune_stale_cached_exports(databases)
                    if force_refresh
                    else []
                )
                run_dir, combined_df, manifest = export_databases_cached(
                    selected_databases,
                    force_refresh=force_refresh,
                )
                evaluation = evaluate_hours(combined_df)
            st.session_state[LAST_RESULT_STATE_KEY] = {
                "run_dir": str(run_dir),
                "combined_df": combined_df,
                "manifest": manifest,
                "loaded_from_cache": manifest.get("loaded_from_cache", False),
                "removed_cache_dirs": removed_cache_dirs,
                "total_hours": evaluation.total_hours,
                "hours_by_employee": evaluation.hours_by_employee,
                "hours_by_month_location_shift": evaluation.hours_by_month_location_shift,
                "hours_by_month_employee": evaluation.hours_by_month_employee,
                "shift_value_overview": evaluation.shift_value_overview,
                "festangestellte_hours": evaluation.festangestellte_hours,
                "festangestellte_weekly_hours": evaluation.festangestellte_weekly_hours,
                "row_count": evaluation.row_count,
            }
            if manifest.get("loaded_from_cache"):
                st.success("Evaluation complete. Used cached downloaded data.")
            else:
                st.success(f"Evaluation complete. Stored fresh downloads in `{run_dir}`.")
            if removed_cache_dirs:
                st.info(f"Removed {len(removed_cache_dirs)} stale cached download(s).")
        except Exception as exc:
            st.error(f"Evaluation failed: {exc}")

    result = st.session_state.get(LAST_RESULT_STATE_KEY)
    if not result:
        return

    st.subheader("Latest Evaluation")
    st.caption(f"Run directory: `{result['run_dir']}`")
    if result.get("loaded_from_cache"):
        st.caption("Source: cached downloaded data. Use refresh to fetch current Notion data.")

    with st.expander("Festangestellte: Sollstunden vs Iststunden", expanded=True):
        festangestellte_hours = result.get("festangestellte_hours", pd.DataFrame())
        if festangestellte_hours.empty:
            st.info("No Festangestellte comparison available for the evaluated months.")
        else:
            st.dataframe(
                festangestellte_hours,
                hide_index=True,
                width="stretch",
                column_config={
                    "Wochenstunden": st.column_config.NumberColumn(format="%.2f"),
                    "Sollstunden": st.column_config.NumberColumn(format="%.2f"),
                    "Iststunden": st.column_config.NumberColumn(format="%.2f"),
                    "Bereinigt": st.column_config.NumberColumn(format="%.2f"),
                    "Differenz": st.column_config.NumberColumn(format="%.2f"),
                    "Erfuellung %": st.column_config.NumberColumn(format="%.1f %%"),
                },
            )

    with st.expander("Festangestellte: Wochenbewertung", expanded=True):
        festangestellte_weekly_hours = result.get("festangestellte_weekly_hours", pd.DataFrame())
        if festangestellte_weekly_hours.empty:
            st.info("No full-week Festangestellte comparison available for the evaluated months.")
        else:
            st.dataframe(
                festangestellte_weekly_hours,
                hide_index=True,
                width="stretch",
                column_config={
                    "Wochenstunden": st.column_config.NumberColumn(format="%.2f"),
                    "SOLL": st.column_config.NumberColumn(format="%.2f"),
                    "IST": st.column_config.NumberColumn(format="%.2f"),
                    "Stunden bereinigt": st.column_config.NumberColumn(format="%.2f"),
                },
            )

    with st.expander("Hours by Month, Location and Shift", expanded=True):
        _render_month_location_shift_chart(result["hours_by_month_location_shift"])
        st.dataframe(result["hours_by_month_location_shift"], hide_index=True, width="stretch")

    with st.expander("Hours by Month and Employee", expanded=False):
        st.dataframe(result["hours_by_month_employee"], hide_index=True, width="stretch")

    with st.expander("Checks: Shift Values Entered", expanded=False):
        st.dataframe(result["shift_value_overview"], hide_index=True, width="stretch")

    with st.expander("Downloaded databases", expanded=False):
        st.dataframe(pd.DataFrame(result["manifest"].get("databases", [])), hide_index=True, width="stretch")

    with st.expander("Raw combined rows", expanded=False):
        st.dataframe(result["combined_df"], hide_index=True, width="stretch")
