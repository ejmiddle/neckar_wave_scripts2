import difflib
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

BERLIN_TIMEZONE = ZoneInfo("Europe/Berlin")
NAME_COLUMN_CANDIDATES = ("Name", "Mitarbeiter", "Employee", "Person", "Titel")
AVAILABILITY_COLUMNS = ("Wann", "Kommentar", "Select", "Name")
FIXED_SCHEDULE_NAME_COLUMNS = NAME_COLUMN_CANDIDATES
FIXED_SCHEDULE_DAYS_COLUMNS = (
    "Days",
    "days",
    "Wochentage",
    "Wochentag",
    "Tage",
    "Tag",
    "Weekdays",
    "Weekday",
)
FIXED_SCHEDULE_START_COLUMNS = (
    "Start Time",
    "start_time",
    "Start",
    "Beginn",
    "Von",
    "Startzeit",
)
FIXED_SCHEDULE_END_COLUMNS = (
    "End Time",
    "end_time",
    "End",
    "Ende",
    "Bis",
    "Endzeit",
)
FIXED_SCHEDULE_SPAN_COLUMNS = (
    "Wann",
    "Date",
    "Datum",
    "Zeit",
    "Time",
    "Schicht",
)
GERMAN_WEEKDAYS = {
    "montag": "Monday",
    "mo": "Monday",
    "dienstag": "Tuesday",
    "di": "Tuesday",
    "mittwoch": "Wednesday",
    "mi": "Wednesday",
    "donnerstag": "Thursday",
    "do": "Thursday",
    "freitag": "Friday",
    "fr": "Friday",
    "samstag": "Saturday",
    "sa": "Saturday",
    "sonntag": "Sunday",
    "so": "Sunday",
}
ENGLISH_WEEKDAYS = {
    "monday": "Monday",
    "mon": "Monday",
    "tuesday": "Tuesday",
    "tue": "Tuesday",
    "wednesday": "Wednesday",
    "wed": "Wednesday",
    "thursday": "Thursday",
    "thu": "Thursday",
    "friday": "Friday",
    "fri": "Friday",
    "saturday": "Saturday",
    "sat": "Saturday",
    "sunday": "Sunday",
    "sun": "Sunday",
}


def _parse_datetime_text(value: object):
    missing = pd.isna(value)
    if isinstance(missing, bool) and missing:
        return pd.NaT
    text = str(value).strip()
    if not text:
        return pd.NaT
    dayfirst = not bool(re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", text))
    parsed = pd.to_datetime(text, dayfirst=dayfirst, errors="coerce")
    if pd.notna(parsed) and getattr(parsed, "tzinfo", None) is not None:
        parsed = parsed.tz_convert(BERLIN_TIMEZONE).tz_localize(None)
    return parsed


def _parse_notion_date_value(value: dict) -> tuple[object, object]:
    start = _parse_datetime_text(value.get("start"))
    end_raw = value.get("end")
    end = _parse_datetime_text(end_raw)

    if pd.notna(end) and isinstance(end_raw, str) and "T" not in end_raw:
        end = datetime.combine(end.date(), time(23, 59, 59))
    if pd.isna(end) and pd.notna(start) and isinstance(value.get("start"), str) and "T" not in value["start"]:
        end = datetime.combine(start.date(), time(23, 59, 59))

    return start, end


def parse_wann(wann_str):
    if isinstance(wann_str, dict) and wann_str.get("start"):
        return _parse_notion_date_value(wann_str)
    if pd.isna(wann_str):
        return pd.NaT, pd.NaT
    wann_str = str(wann_str)
    parts = re.split(r"\s*(?:→|->|–)\s*", wann_str, maxsplit=1)
    start = parts[0].strip()
    end = parts[1].strip() if len(parts) > 1 else None
    try:
        start = re.sub(r'\s*\(GMT[^\)]*\)', '', start)
        start_time = _parse_datetime_text(start)
    except Exception:
        start_time = pd.NaT
    end_time = pd.NaT
    if end:
        if re.match(r"^\d{1,2}:\d{2}", end):
            if pd.isna(start_time):
                return start_time, pd.NaT
            end = f"{start_time.strftime('%B %d, %Y')} {end}"
        else:
            end = re.sub(r'\s*\(GMT[^\)]*\)', '', end)
            if re.match(r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}$", end.strip()):
                try:
                    date_only = _parse_datetime_text(end.strip()).date()
                    end = datetime.combine(date_only, time(23, 59, 59))
                except Exception:
                    end = pd.NaT
        try:
            if not isinstance(end, datetime):
                end_time = _parse_datetime_text(end)
            else:
                end_time = end
        except Exception:
            end_time = pd.NaT
    return start_time, end_time


def has_explicit_time_frame(value: Any) -> bool:
    if isinstance(value, dict):
        for key in ("start", "end"):
            raw = value.get(key)
            if isinstance(raw, str) and "T" in raw:
                return True
        return False

    if pd.isna(value):
        return False
    text = str(value).strip()
    if not text:
        return False
    cleaned = re.sub(r'\s*\(GMT[^\)]*\)', '', text)
    return bool(re.search(r"\b\d{1,2}[:.]\d{2}\b", cleaned) or re.search(r"T\d{1,2}:\d{2}", cleaned))


def split_multiday_entries(df):
    split_rows = []
    for _, row in df.iterrows():
        start = row['Start Time']
        end = row['End Time']
        if pd.isna(end) or start.date() == end.date():
            split_rows.append(row)
            continue
        current_start = start
        while current_start.date() < end.date():
            current_end = datetime.combine(current_start.date(), datetime.max.time()).replace(microsecond=0)
            new_row = row.copy()
            new_row['Start Time'] = current_start
            new_row['End Time'] = current_end
            split_rows.append(new_row)
            current_start = current_end + timedelta(seconds=1)
        new_row = row.copy()
        new_row['Start Time'] = current_start
        new_row['End Time'] = end
        split_rows.append(new_row)
    return pd.DataFrame(split_rows)

def fill_missing_end_times(df):
    df = df.copy()
    df['End Time'] = df.apply(
        lambda row: datetime.combine(row['Start Time'].date(), time(23, 59, 59)) 
        if pd.isna(row['End Time']) and pd.notna(row['Start Time']) 
        else row['End Time'],
        axis=1
    )
    return df

def match_name(name, name_list):
    if pd.isna(name):
        return None
    name = str(name).strip()
    match = difflib.get_close_matches(name, name_list, n=1, cutoff=0.6)
    return match[0] if match else None


def _first_non_empty(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    for candidate in candidates:
        value = row.get(candidate)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        return value
    return None


def _normalize_weekday(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    key = text.casefold().rstrip(".")
    return ENGLISH_WEEKDAYS.get(key) or GERMAN_WEEKDAYS.get(key)


def _normalize_weekdays(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_days = value
    else:
        raw_days = re.split(r"\s*[,;/|]\s*|\s+(?:und|and)\s+", str(value), flags=re.IGNORECASE)

    days = []
    for raw_day in raw_days:
        normalized = _normalize_weekday(raw_day)
        if normalized and normalized not in days:
            days.append(normalized)
    return days


def _parse_fixed_schedule_time(value: Any) -> time | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)

    text = str(value).strip()
    if not text:
        return None

    time_match = re.search(r"(\d{1,2})(?::|\.)(\d{2})", text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)

    hour_match = re.fullmatch(r"(\d{1,2})", text)
    if hour_match:
        hour = int(hour_match.group(1))
        if 0 <= hour <= 23:
            return time(hour, 0)

    parsed = _parse_datetime_text(text)
    if pd.notna(parsed):
        return parsed.time().replace(second=0, microsecond=0)
    return None


def normalize_fixed_schedule_records(records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Convert flat Notion rows into the existing fixed schedule dict format."""
    schedules: dict[str, dict[str, Any]] = {}
    errors = []
    calendar_weeks: set[tuple[int, int]] = set()

    for index, row in enumerate(records, start=1):
        name_value = _first_non_empty(row, FIXED_SCHEDULE_NAME_COLUMNS)
        days_value = _first_non_empty(row, FIXED_SCHEDULE_DAYS_COLUMNS)
        start_value = _first_non_empty(row, FIXED_SCHEDULE_START_COLUMNS)
        end_value = _first_non_empty(row, FIXED_SCHEDULE_END_COLUMNS)
        span_value = _first_non_empty(row, FIXED_SCHEDULE_SPAN_COLUMNS)

        name = str(name_value or "").strip()
        days = _normalize_weekdays(days_value)
        start_time = _parse_fixed_schedule_time(start_value)
        end_time = _parse_fixed_schedule_time(end_value)
        span_start = pd.NaT
        span_end = pd.NaT
        if span_value is not None:
            # A dated Notion value is only a template for weekday and clock times.
            # The concrete date/week must not constrain recurring fixed schedules.
            span_start, span_end = parse_wann(span_value)
            if pd.notna(span_start):
                iso_calendar = span_start.date().isocalendar()
                calendar_weeks.add((iso_calendar.year, iso_calendar.week))

        if span_value is not None and (not days or start_time is None or end_time is None):
            if pd.notna(span_start):
                if not days:
                    days = [span_start.strftime("%A")]
                if start_time is None:
                    start_time = span_start.time().replace(second=0, microsecond=0)
            if pd.notna(span_end) and end_time is None:
                end_time = span_end.time().replace(second=0, microsecond=0)

        missing = []
        if not name:
            missing.append("Name")
        if not days:
            missing.append("Days/Wochentage")
        if start_time is None:
            missing.append("Start Time")
        if end_time is None:
            missing.append("End Time")
        if missing:
            errors.append(f"Row {index}: missing or invalid {', '.join(missing)}")
            continue

        existing = schedules.get(name)
        if existing:
            if existing["start_time"] != start_time or existing["end_time"] != end_time:
                errors.append(f"Row {index}: duplicate schedule for {name} has a different time window")
                continue
            existing_days = existing["days"]
            existing_days.extend(day for day in days if day not in existing_days)
            continue

        schedules[name] = {
            "days": days,
            "start_time": start_time,
            "end_time": end_time,
        }

    if len(calendar_weeks) > 1:
        week_labels = ", ".join(f"{year}-W{week:02d}" for year, week in sorted(calendar_weeks))
        errors.append(f"Fixed schedule calendar must contain exactly one week. Found: {week_labels}")
        return {}, errors

    return schedules, errors

def generate_fixed_schedule_entries(start_date, end_date, schedule_dict):
    rows = []
    current = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    while current <= end:
        weekday_name = current.strftime('%A')
        for name, info in schedule_dict.items():
            if weekday_name in info['days']:
                start_dt = datetime.combine(current, info['start_time'])
                end_dt = datetime.combine(current, info['end_time'])
                rows.append({'Name': name, 'Start Time': start_dt, 'End Time': end_dt, 'new_name': name})
        current += timedelta(days=1)
    return pd.DataFrame(rows)

def normalize_long_shifts(df, max_hours=10):
    df = df.copy()
    def adjust_if_too_long(row):
        start = row['Start Time']
        end = row['End Time']
        if pd.notna(start) and pd.notna(end):
            duration = (end - start).total_seconds() / 3600
            if duration > max_hours:
                new_start = datetime.combine(start.date(), time(10, 0))
                new_end = datetime.combine(start.date(), time(18, 0))
                return pd.Series([new_start, new_end])
        return pd.Series([start, end])
    df[['Start Time', 'End Time']] = df.apply(adjust_if_too_long, axis=1)
    return df


def fill_missing_non_fixed_shift_times(
    df: pd.DataFrame,
    start_time: time = time(11, 0),
    end_time: time = time(16, 0),
) -> pd.DataFrame:
    df = df.copy()
    has_explicit_time = df.get("_has_explicit_time")
    if has_explicit_time is None:
        has_explicit_time = pd.Series(False, index=df.index)
    fallback_mask = ~has_explicit_time.fillna(False).astype(bool)
    df.loc[fallback_mask, "Start Time"] = df.loc[fallback_mask, "Start Time"].map(
        lambda value: datetime.combine(value.date(), start_time) if pd.notna(value) else value
    )
    df.loc[fallback_mask, "End Time"] = df.loc[fallback_mask, "End Time"].map(
        lambda value: datetime.combine(value.date(), end_time) if pd.notna(value) else value
    )
    return df

def transform_to_schedule_format(df, person_info):
    info_lookup = {name: (location, task) for name, location, task in person_info}
    df_copy = df.copy()
    df_copy['Date'] = (
        df_copy['Start Time'].dt.strftime('%Y-%m-%d %H:%M')
        + ' → '
        + df_copy['End Time'].dt.strftime('%Y-%m-%d %H:%M')
    )
    df_copy['Name'] = df_copy['new_name']
    df_copy['Employee'] = df_copy['new_name']
    df_copy['Task'] = df_copy['new_name'].map(lambda name: info_lookup.get(name, (None, None))[1])
    df_copy['Location'] = df_copy['new_name'].map(lambda name: info_lookup.get(name, (None, None))[0])
    
    # Define base columns for output
    output_columns = ['Name', 'Date', 'Employee', 'Task', 'Location']
    
    # Include Kommentar column if it exists in the original data
    if 'Kommentar' in df_copy.columns:
        output_columns.append('Kommentar')
    
    final_df = df_copy[output_columns]
    return final_df

def detect_availability_time_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Detect the current availability time field."""
    if "Wann" in df.columns:
        return {"mode": "span", "span": "Wann"}
    return {"mode": None, "span": None}


def _prepare_availability_dataframe(availability_data):
    if not isinstance(availability_data, pd.DataFrame):
        raise TypeError("availability_data must be a pandas DataFrame.")
    df = availability_data.copy()
    if df.empty:
        raise ValueError("availability_data is empty.")

    if list(df.columns) != list(AVAILABILITY_COLUMNS):
        raise ValueError(
            "availability_data must contain exactly these columns in this order: "
            f"{', '.join(AVAILABILITY_COLUMNS)}."
        )

    parsed_spans = df["Wann"].map(parse_wann)
    df["_has_explicit_time"] = df["Wann"].map(has_explicit_time_frame)
    df["Start Time"] = parsed_spans.map(lambda value: value[0])
    df["End Time"] = parsed_spans.map(lambda value: value[1])

    df = df[df["Start Time"].notna()].copy()
    df = split_multiday_entries(df).reset_index(drop=True)
    df = fill_missing_end_times(df)
    df = df[df["Name"].notna()].copy()
    df["Name"] = df["Name"].astype(str).str.strip()
    df = df[df["Name"] != ""]

    return df


def _filter_by_date_range(df: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    start_dt = pd.to_datetime(start_date).normalize()
    end_dt = pd.to_datetime(end_date).normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

    if end_dt < start_dt:
        raise ValueError("end_date must be on or after start_date.")

    in_range = (df["Start Time"] <= end_dt) & (df["End Time"] >= start_dt)
    return df[in_range].copy()


def _write_output_files(formatted_df: pd.DataFrame, output_dir):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    output_files = {}
    export_path = output_path / "schedule_export.csv"
    formatted_df.to_csv(export_path, index=False)
    output_files["export"] = str(export_path)

    df_alt = formatted_df[formatted_df["Location"] == "ALT"]
    df_wie = formatted_df[formatted_df["Location"] == "WIE"]
    df_bak = formatted_df[formatted_df["Location"] == "BAK"]
    df_both_locations = pd.concat([df_alt, df_wie], ignore_index=True)

    both_path = output_path / "both_locations.csv"
    df_both_locations.to_csv(both_path, index=False)
    output_files["both"] = str(both_path)

    alt_path = output_path / "schedule_ALT.csv"
    df_alt.to_csv(alt_path, index=False)
    output_files["alt"] = str(alt_path)

    wie_path = output_path / "schedule_WIE.csv"
    df_wie.to_csv(wie_path, index=False)
    output_files["wie"] = str(wie_path)

    bak_path = output_path / "schedule_BAK.csv"
    df_bak.to_csv(bak_path, index=False)
    output_files["bak"] = str(bak_path)

    return output_files


def generate_schichtplan(availability_data, start_date, end_date, person_info, fixed_schedules=None, output_dir="Schichtplan"):
    """
    Generate schichtplan and return output files along with name evaluation analysis.
    
    Returns:
        tuple: (output_files_dict, evaluation_dict)
    """
    # Load and process in-memory availability_data.
    df = _prepare_availability_dataframe(availability_data)
    df = _filter_by_date_range(df, start_date, end_date)

    # Store names from selected time window for evaluation.
    original_unique_names = df["Name"].dropna().str.strip().unique()

    # Create name lists for matching
    name_list = [name for name, _, _ in person_info]
    person_info_names = set(name_list)

    # Match names and filter
    df["new_name"] = df["Name"].apply(lambda n: match_name(n, name_list))
    matched_names = set(df["new_name"].dropna().unique())
    df = df[df["new_name"].notna()]
    df = fill_missing_non_fixed_shift_times(df)

    # Add fixed schedules if provided
    if fixed_schedules:
        fixed_df = generate_fixed_schedule_entries(start_date, end_date, fixed_schedules)
        df = pd.concat([df, fixed_df], ignore_index=True)

    if not df.empty:
        df = df.sort_values(["Start Time", "new_name"]).reset_index(drop=True)

    # Transform to final format
    formatted_df = transform_to_schedule_format(df, person_info)

    # Write output files
    output_files = _write_output_files(formatted_df, output_dir)

    # Perform name evaluation analysis
    unique_names_set = set(original_unique_names)

    # Names in uploaded CSV but not in person_info (potential typos or missing employees)
    names_not_in_person_info = unique_names_set - person_info_names

    # Names in person_info but not in uploaded CSV (employees not scheduled)
    names_not_in_csv = person_info_names - unique_names_set

    # Names that couldn't be matched (too different from person_info)
    unmatched_names = unique_names_set - {
        name for name in original_unique_names if match_name(name, name_list) is not None
    }

    evaluation = {
        "names_in_csv_not_in_person_info": sorted(list(names_not_in_person_info)),
        "names_in_person_info_not_in_csv": sorted(list(names_not_in_csv)),
        "names_in_availability_not_in_person_info": sorted(list(names_not_in_person_info)),
        "names_in_person_info_not_in_availability": sorted(list(names_not_in_csv)),
        "successfully_matched_names": sorted(list(matched_names)),
        "unmatched_names": sorted(list(unmatched_names)),
        "total_original_names": len(original_unique_names),
        "total_person_info_names": len(person_info_names),
        "total_matched": len(matched_names),
    }

    return output_files, evaluation
