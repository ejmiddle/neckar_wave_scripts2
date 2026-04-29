from datetime import UTC, datetime, timedelta
from typing import Optional

import pandas as pd
import streamlit as st

from src.notion_access import get_notion_orders_from_today
from src.shopify_access import (
    create_excel_download_button,
    get_heidelberg_weather,
    get_last_6_days_orders_with_variants,
)


def move_column_to_end(df: "pd.DataFrame", column: str) -> "pd.DataFrame":
    if column not in df.columns:
        return df
    ordered_cols = [col for col in df.columns if col != column] + [column]
    return df[ordered_cols]


def apply_fulfillment_filter(
    df: "pd.DataFrame", filter_unfulfilled_only: bool
) -> "pd.DataFrame":
    if "Fulfillment Status" not in df.columns:
        return df

    if not filter_unfulfilled_only:
        return df

    fulfillment_status = df["Fulfillment Status"].fillna("").astype(str).str.lower()
    return df[fulfillment_status != "fulfilled"]


def style_shopify_day_matches(
    df: "pd.DataFrame",
    styler: Optional["pd.io.formats.style.Styler"] = None,
) -> "pd.io.formats.style.Styler":
    if styler is None:
        styler = df.style
    if "Wann bestellt" not in df.columns or "Variant Title" not in df.columns:
        return styler

    weekday_names = {
        0: "Montag",
        1: "Dienstag",
        2: "Mittwoch",
        3: "Donnerstag",
        4: "Freitag",
        5: "Samstag",
        6: "Sonntag",
    }

    def get_weekday_name(value: str) -> str | None:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            return None
        return weekday_names.get(parsed.weekday())

    weekdays = df["Wann bestellt"].apply(get_weekday_name)
    variant_lower = df["Variant Title"].astype(str).str.lower()
    mask = [
        bool(day) and day.lower() in variant
        for day, variant in zip(weekdays, variant_lower)
    ]
    mask_series = pd.Series(mask, index=df.index)

    def highlight_match(row: "pd.Series") -> list[str]:
        return [
            "background-color: #cfe8ff" if mask_series.loc[row.name] else ""
            for _ in row
        ]

    return styler.apply(highlight_match, axis=1)


def style_variant_matches_today(
    df: "pd.DataFrame",
    styler: Optional["pd.io.formats.style.Styler"] = None,
) -> "pd.io.formats.style.Styler":
    if styler is None:
        styler = df.style
    if "Variant Title" not in df.columns:
        return styler

    weekday_names = {
        0: "Montag",
        1: "Dienstag",
        2: "Mittwoch",
        3: "Donnerstag",
        4: "Freitag",
        5: "Samstag",
        6: "Sonntag",
    }
    today_name = weekday_names.get(datetime.now(UTC).weekday(), "")
    if not today_name:
        return styler

    variant_lower = df["Variant Title"].astype(str).str.lower()
    mask_series = variant_lower.str.contains(today_name.lower(), na=False)

    def highlight_match(row: "pd.Series") -> list[str]:
        return [
            "background-color: #cfe8ff" if mask_series.loc[row.name] else ""
            for _ in row
        ]

    return styler.apply(highlight_match, axis=1)


def style_notion_date_today(
    df: "pd.DataFrame",
    styler: Optional["pd.io.formats.style.Styler"] = None,
    date_column: str = "Date",
) -> "pd.io.formats.style.Styler":
    if styler is None:
        styler = df.style
    if date_column not in df.columns:
        return styler

    parsed_dates = pd.to_datetime(df[date_column], errors="coerce", dayfirst=True)
    today = datetime.now(UTC).date()
    match = parsed_dates.dt.date == today

    def highlight_match(row: "pd.Series") -> list[str]:
        return [
            "background-color: #cfe8ff" if match.loc[row.name] else ""
            for _ in row
        ]

    return styler.apply(highlight_match, axis=1)


def bestellungen_analyse(
    title_filter_mode: str = "include",
    default_titles: list[str] | None = None,
    state_prefix: str = "shopify_brot",
    show_orders_by_customer: bool = True,
    default_days_back: int = 7,
    show_unfulfilled_filter: bool = False,
    show_notion_button: bool = True,
) -> None:
    st.set_page_config(layout="wide")

    with st.sidebar:

        default_start_date = datetime.now(UTC).date() - timedelta(days=default_days_back)
        selected_start_date = st.date_input(
            "Bestellungen ab Datum:",
            value=default_start_date,
            help="Wählen Sie das Startdatum für die Bestellungsabfrage. Standard: 1 Woche vor heute.",
        )

        default_end_date = datetime.now(UTC).date()
        selected_end_date = st.date_input(
            "Bestellungen bis Datum:",
            value=default_end_date,
            help="Wählen Sie das Enddatum für die Bestellungsabfrage. Standard: Heute.",
        )

        filter_unfulfilled_only = False
        if show_unfulfilled_filter:
            filter_unfulfilled_only = st.checkbox(
                "Unfulfilled filtern",
                key=f"{state_prefix}_fulfillment_filter",
            )

        start_datetime = datetime.combine(selected_start_date, datetime.min.time()).replace(
            tzinfo=UTC
        )
        end_datetime = datetime.combine(selected_end_date, datetime.max.time()).replace(
            tzinfo=UTC
        )

        if selected_start_date > selected_end_date:
            st.error("⚠️ Das Startdatum darf nicht nach dem Enddatum liegen!")

        if st.button("Shopify Bestellungen aktualisieren"):
            if selected_start_date > selected_end_date:
                st.error("Bitte korrigieren Sie den Datumsbereich!")
            else:
                df = get_last_6_days_orders_with_variants(start_datetime, end_datetime)
                st.session_state.bestellungen = df
                st.session_state.start_date = selected_start_date
                st.session_state.end_date = selected_end_date
                df.to_excel("shopify_orders.xlsx", index=False)

        if show_notion_button and st.button("Notion Bestellungen ab heute laden"):
            df = get_notion_orders_from_today()
            st.session_state.notion_bestellungen = df

        if "bestellungen" in st.session_state:
            df = apply_fulfillment_filter(
                st.session_state.bestellungen.copy(), filter_unfulfilled_only
            )
            total_orders = len(df)
            total_items = df["Quantity"].sum()

            if "start_date" in st.session_state and "end_date" in st.session_state:
                st.info(
                    f"📊 Daten vom {st.session_state.start_date.strftime('%d.%m.%Y')} bis {st.session_state.end_date.strftime('%d.%m.%Y')}"
                )
            elif "start_date" in st.session_state:
                st.info(
                    f"📊 Daten ab: {st.session_state.start_date.strftime('%d.%m.%Y')}"
                )

            st.write(f"Gesamtanzahl der Bestellungen: {total_orders}")
            st.write(f"Gesamtanzahl der bestellten Artikel: {total_items}")


    st.title("Bestellungen")

    if "bestellungen" in st.session_state:
        df = move_column_to_end(
            apply_fulfillment_filter(
                st.session_state.bestellungen.copy(), filter_unfulfilled_only
            ),
            "Product Title",
        )
        with st.expander("Alle Bestellungen sehen"):
            st.dataframe(style_shopify_day_matches(df), hide_index=True)
            create_excel_download_button(
                df,
                f"alle_bestellungen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                "📥 Alle Bestellungen herunterladen",
            )

        all_product_titles = sorted(df["Product Title"].dropna().unique().tolist())
        titles_state_key = f"{state_prefix}_titles"

        if default_titles is None:
            default_titles = [
                "Gutes Brot nach Ziegelhausen/Schlierbach",
                "Unsere Brote",
                "Brote für Solawi",
            ]

        if titles_state_key not in st.session_state:
            st.session_state[titles_state_key] = [
                title for title in default_titles if title in all_product_titles
            ]

        if title_filter_mode == "exclude":
            with st.expander("Ausgeschlossene Produkttitel"):
                st.write(
                    "Diese Produkttitel werden aus der Coffee-Ansicht ausgeschlossen."
                )

                for title in all_product_titles:
                    is_excluded = title in st.session_state[titles_state_key]
                    if st.checkbox(
                        title,
                        value=is_excluded,
                        key=f"{state_prefix}_checkbox_{title}",
                    ):
                        if title not in st.session_state[titles_state_key]:
                            st.session_state[titles_state_key].append(title)
                    else:
                        if title in st.session_state[titles_state_key]:
                            st.session_state[titles_state_key].remove(title)

            filtered_df = df[
                ~df["Product Title"].isin(st.session_state[titles_state_key])
            ]
        else:
            with st.expander("Produkttitel auswählen"):
                st.write(
                    "Wählen Sie die Produkttitel aus, die angezeigt werden sollen:"
                )

                for title in all_product_titles:
                    is_selected = title in st.session_state[titles_state_key]
                    if st.checkbox(
                        title,
                        value=is_selected,
                        key=f"{state_prefix}_checkbox_{title}",
                    ):
                        if title not in st.session_state[titles_state_key]:
                            st.session_state[titles_state_key].append(title)
                    else:
                        if title in st.session_state[titles_state_key]:
                            st.session_state[titles_state_key].remove(title)

            filtered_df = df[df["Product Title"].isin(st.session_state[titles_state_key])]

        unique_values = filtered_df["Product Title"].unique()

        categories = st.multiselect(
            "Wähle Produktklassen aus",
            options=list(unique_values),
            default=list(unique_values),
        )

        if categories:
            st.write("Bestellungen summiert")
            selected_df = filtered_df[filtered_df["Product Title"].isin(categories)]
            aggregated_source_df = selected_df.copy()
            aggregated_source_df["Variant Title"] = aggregated_source_df[
                "Variant Title"
            ].fillna("default")
            aggregated_df_summe = (
                aggregated_source_df.groupby(["Product Title", "Variant Title"])[
                    "Quantity"
                ]
                .sum()
                .reset_index()
            )
            aggregated_df_summe = aggregated_df_summe.sort_values(
                by="Quantity", ascending=False
            )
            aggregated_df_summe = move_column_to_end(
                aggregated_df_summe, "Product Title"
            )
            summe_styler = style_variant_matches_today(aggregated_df_summe)
            st.dataframe(summe_styler, hide_index=True)
            create_excel_download_button(
                aggregated_df_summe,
                f"brote_summe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                "📥 Herunterladen",
            )

            if show_orders_by_customer:
                st.write("Brote nach Kunde")
                aggregated_source_df = selected_df.copy()
                aggregated_source_df["Variant Title"] = aggregated_source_df[
                    "Variant Title"
                ].fillna("default")
                aggregated_df_kunde = (
                    aggregated_source_df.groupby(
                        ["Customer", "Product Title", "Variant Title", "Wann bestellt"]
                    )["Quantity"]
                    .sum()
                    .reset_index()
                )
                aggregated_df_kunde = aggregated_df_kunde.sort_values(
                    by="Quantity", ascending=False
                )
                aggregated_df_kunde = move_column_to_end(
                    aggregated_df_kunde, "Product Title"
                )
                kunde_styler = style_variant_matches_today(aggregated_df_kunde)
                st.dataframe(kunde_styler, hide_index=True)
                create_excel_download_button(
                    aggregated_df_kunde,
                    f"brote_nach_kunde_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    "📥 Brote nach Kunde herunterladen",
                )
        else:
            st.info("Bitte wählen Sie mindestens einen Bestellort aus.")



    if "notion_bestellungen" in st.session_state:
        df = st.session_state.notion_bestellungen
        notion_styler = style_notion_date_today(df)
        st.dataframe(notion_styler, hide_index=True)
        create_excel_download_button(
            df,
            f"notion_bestellungen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            "📥 Notion Bestellungen herunterladen",
        )
