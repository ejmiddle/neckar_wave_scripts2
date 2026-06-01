from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any

import pandas as pd
import requests
import streamlit as st

from src.accounting.common import format_currency_value, report_error
from src.accounting.ready2order import (
    DEFAULT_READY2ORDER_LIMIT,
    READY2ORDER_API_BASE,
    Ready2OrderRequestError,
    build_overall_sales_by_period,
    build_ready2order_product_group_summaries,
    fetch_ready2order_invoices_cached_by_day,
    read_ready2order_token,
    subtract_months,
)

READY2ORDER_SALES_STATE_KEY = "ready2order_product_group_sales"
READY2ORDER_SALES_SIGNATURE_KEY = "ready2order_product_group_sales_signature"
READY2ORDER_GROUP_CHART_SELECTION_KEY = "ready2order_product_group_chart_selection"


def _default_start_date() -> date:
    return subtract_months(date.today(), 2)


def _ensure_ready2order_token() -> str | None:
    token = read_ready2order_token()
    if token:
        return token
    report_error("No ready2order API token found. Set `READY2ORDER_BILL_API_TOKEN` in `.env`.")
    return None


def _format_number(value: Any, decimals: int = 2) -> str:
    try:
        return f"{float(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "-"


def _display_summary(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame(
            columns=[
                "Zeitraum",
                "Produktgruppe",
                "# Items",
                "Brutto EUR",
                "Netto EUR",
                "USt EUR",
                "Rechnungen",
                "Positionen",
            ]
        )
    display = summary.copy()
    if "period" not in display.columns:
        display["period"] = "Total"
    display["# Items"] = display["quantity"].map(lambda value: _format_number(value))
    display["Brutto EUR"] = display["gross_sales"].map(format_currency_value)
    display["Netto EUR"] = display["net_sales"].map(format_currency_value)
    display["USt EUR"] = display["vat"].map(format_currency_value)
    display["Rechnungen"] = display["invoice_count"].astype(int)
    display["Positionen"] = display["line_count"].astype(int)
    display["Produktgruppe"] = display["product_group_name"].fillna("(No product group)")
    return display[
        [
            "period",
            "Produktgruppe",
            "# Items",
            "Brutto EUR",
            "Netto EUR",
            "USt EUR",
            "Rechnungen",
            "Positionen",
        ]
    ].rename(columns={"period": "Zeitraum"})


def _totals_by_product_group(line_items: pd.DataFrame) -> pd.DataFrame:
    if line_items.empty:
        return pd.DataFrame()
    data = line_items.copy()
    for column in ("quantity", "gross_sales", "net_sales", "vat"):
        data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0)
    return (
        data.groupby(["product_group_id", "product_group_name"], dropna=False, as_index=False)
        .agg(
            quantity=("quantity", "sum"),
            gross_sales=("gross_sales", "sum"),
            net_sales=("net_sales", "sum"),
            vat=("vat", "sum"),
            line_count=("invoice_id", "size"),
            invoice_count=("invoice_id", "nunique"),
        )
        .sort_values("gross_sales", ascending=False)
    )


def _build_workbook_payload(summaries: dict[str, pd.DataFrame]) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        summaries["month"].to_excel(writer, sheet_name="month", index=False)
        summaries["week"].to_excel(writer, sheet_name="week", index=False)
        summaries["day"].to_excel(writer, sheet_name="day", index=False)
        summaries["line_items"].to_excel(writer, sheet_name="line_items", index=False)
    return buffer.getvalue()


def _render_summary_table(label: str, summary: pd.DataFrame) -> None:
    st.subheader(label)
    if summary.empty:
        st.info("No sales rows for this period.")
        return
    st.dataframe(_display_summary(summary), width="stretch", hide_index=True)
    st.download_button(
        f"Download {label} CSV",
        summary.to_csv(index=False).encode("utf-8"),
        file_name=f"ready2order_product_groups_{label.lower()}.csv",
        mime="text/csv",
        width="stretch",
    )


def _product_group_options(summary: pd.DataFrame) -> list[str]:
    if summary.empty or "product_group_name" not in summary.columns:
        return []
    return sorted(
        {
            str(value).strip()
            for value in summary["product_group_name"].fillna("(No product group)")
            if str(value).strip()
        }
    )


def _product_group_chart_data(summary: pd.DataFrame, selected_groups: list[str]) -> pd.DataFrame:
    if summary.empty or not selected_groups:
        return pd.DataFrame()

    data = summary.copy()
    data["product_group_name"] = data["product_group_name"].fillna("(No product group)").astype(str)
    data = data[data["product_group_name"].isin(selected_groups)]
    if data.empty:
        return pd.DataFrame()
    data["gross_sales"] = pd.to_numeric(data["gross_sales"], errors="coerce").fillna(0.0)
    chart_data = (
        data.pivot_table(
            index="period",
            columns="product_group_name",
            values="gross_sales",
            aggfunc="sum",
            fill_value=0.0,
        )
        .sort_index()
        .reset_index()
    )
    return chart_data.set_index("period")


def _render_product_group_split_charts(
    weekly_summary: pd.DataFrame,
    daily_summary: pd.DataFrame,
) -> None:
    options = _product_group_options(weekly_summary)
    if not options:
        return

    selected_groups = st.multiselect(
        "Produktgruppen",
        options=options,
        default=options,
        key=READY2ORDER_GROUP_CHART_SELECTION_KEY,
    )
    split_week, split_day = st.tabs(["By week", "By day"])
    with split_week:
        chart_data = _product_group_chart_data(weekly_summary, selected_groups)
        if chart_data.empty:
            st.info("No product groups selected.")
        else:
            st.line_chart(chart_data, height=320)
    with split_day:
        chart_data = _product_group_chart_data(daily_summary, selected_groups)
        if chart_data.empty:
            st.info("No product groups selected.")
        else:
            st.line_chart(chart_data, height=320)


def _load_ready2order_sales(
    *,
    token: str,
    date_from: date,
    date_to: date,
    include_test_mode: bool,
    limit: int,
    force_refresh: bool,
) -> dict[str, Any]:
    invoices, cache_stats = fetch_ready2order_invoices_cached_by_day(
        token,
        date_from=date_from,
        date_to=date_to,
        date_field="daily_report",
        include_test_mode=include_test_mode,
        limit=limit,
        base_url=READY2ORDER_API_BASE,
        force_refresh=force_refresh,
    )
    summaries = build_ready2order_product_group_summaries(
        invoices,
        date_from=date_from,
        date_to=date_to,
    )
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "date_field": "daily_report",
        "include_test_mode": include_test_mode,
        "invoices": invoices,
        "cache_stats": cache_stats,
        "summaries": summaries,
    }


def render_ready2order_sales_view() -> None:
    st.title("📊 Accounting / ready2order Produktgruppen")
    st.caption("ready2order sales by product group, grouped by month, week, and day.")

    with st.expander("Connection & Zeitraum", expanded=True):
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            date_from = st.date_input("Von", value=_default_start_date())
        with col2:
            date_to = st.date_input("Bis", value=date.today())
        with col3:
            include_test_mode = st.checkbox("Testmodus einbeziehen", value=False)

        force_refresh = st.checkbox(
            "Ausgewählten Zeitraum neu von ready2order laden",
            value=False,
            help="Standardmäßig werden bereits geladene Tage aus dem lokalen Cache verwendet.",
        )
        load_clicked = st.button("ready2order laden", type="primary", width="stretch")

    if date_from > date_to:
        st.error("Start date must be before or equal to end date.")
        return

    signature = (
        str(date_from),
        str(date_to),
        bool(include_test_mode),
    )
    if load_clicked:
        token = _ensure_ready2order_token()
        if token:
            try:
                with st.spinner("ready2order bills and line items are loading..."):
                    st.session_state[READY2ORDER_SALES_STATE_KEY] = _load_ready2order_sales(
                        token=token,
                        date_from=date_from,
                        date_to=date_to,
                        include_test_mode=include_test_mode,
                        limit=DEFAULT_READY2ORDER_LIMIT,
                        force_refresh=force_refresh,
                    )
                    st.session_state[READY2ORDER_SALES_SIGNATURE_KEY] = signature
            except Ready2OrderRequestError as exc:
                report_error(f"{exc}: {exc.body}", log_message="ready2order request failed")
                if exc.status_code == 401 and "Developer-Tokens" in exc.body:
                    st.warning(
                        "`READY2ORDER_BILL_API_TOKEN` must be a ready2order Account Token, "
                        "not the Developer Token."
                    )
            except requests.RequestException as exc:
                report_error(f"ready2order request failed: {exc}", exc_info=True)

    loaded = st.session_state.get(READY2ORDER_SALES_STATE_KEY)
    if not isinstance(loaded, dict):
        st.info("Load ready2order data to show product group sales.")
        return

    if st.session_state.get(READY2ORDER_SALES_SIGNATURE_KEY) != signature:
        st.warning("The displayed data uses a previous filter set. Click `ready2order laden` to refresh.")

    summaries = loaded.get("summaries")
    if not isinstance(summaries, dict):
        st.info("No ready2order summary data loaded.")
        return

    line_items = summaries.get("line_items")
    if not isinstance(line_items, pd.DataFrame):
        st.info("No ready2order line items loaded.")
        return

    total_quantity = float(pd.to_numeric(line_items.get("quantity"), errors="coerce").fillna(0).sum())
    total_gross = float(pd.to_numeric(line_items.get("gross_sales"), errors="coerce").fillna(0).sum())
    total_net = float(pd.to_numeric(line_items.get("net_sales"), errors="coerce").fillna(0).sum())

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    with metric_col1:
        st.metric("Invoices", len(loaded.get("invoices") or []))
    with metric_col2:
        st.metric("# Items", _format_number(total_quantity))
    with metric_col3:
        st.metric("Brutto EUR", format_currency_value(total_gross))
    with metric_col4:
        st.metric("Netto EUR", format_currency_value(total_net))

    cache_stats = loaded.get("cache_stats")
    if isinstance(cache_stats, dict):
        st.caption(
            "Cache: "
            f"{cache_stats.get('cache_hits', 0)} Tage aus Cache, "
            f"{cache_stats.get('api_fetches', 0)} Tage von ready2order geladen, "
            f"{cache_stats.get('deduped_invoices', 0)} doppelte Rechnungen entfernt."
        )

    workbook_payload = _build_workbook_payload(summaries)
    st.download_button(
        "Download Excel Export",
        workbook_payload,
        file_name=f"ready2order_product_groups_{loaded.get('date_from')}_{loaded.get('date_to')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )

    st.subheader("Overall Sales")
    weekly_summary = summaries.get("week", pd.DataFrame())
    daily_summary = summaries.get("day", pd.DataFrame())
    weekly_chart = build_overall_sales_by_period(
        weekly_summary,
        "week",
        date_from=date_from,
        date_to=date_to,
    )
    daily_chart = build_overall_sales_by_period(
        daily_summary,
        "day",
        date_from=date_from,
        date_to=date_to,
    )
    chart_week, chart_day = st.tabs(["By week", "By day"])
    with chart_week:
        st.line_chart(
            weekly_chart.set_index("period")["gross_sales"],
            height=280,
        )
    with chart_day:
        st.line_chart(
            daily_chart.set_index("period")["gross_sales"],
            height=280,
        )

    st.subheader("Product Group Sales")
    _render_product_group_split_charts(weekly_summary, daily_summary)

    totals = _totals_by_product_group(line_items)
    _render_summary_table("Total", totals)

    tab_month, tab_week, tab_day, tab_line_items = st.tabs(["Month", "Week", "Day", "Line items"])
    with tab_month:
        _render_summary_table("Month", summaries.get("month", pd.DataFrame()))
    with tab_week:
        _render_summary_table("Week", summaries.get("week", pd.DataFrame()))
    with tab_day:
        _render_summary_table("Day", summaries.get("day", pd.DataFrame()))
    with tab_line_items:
        st.subheader("Line items")
        st.dataframe(line_items, width="stretch", hide_index=True)
        st.download_button(
            "Download Line Items CSV",
            line_items.to_csv(index=False).encode("utf-8"),
            file_name="ready2order_line_items.csv",
            mime="text/csv",
            width="stretch",
        )
