import difflib
import re
from datetime import datetime, time, timedelta
from pathlib import Path

import pandas as pd

NAME_COLUMN_CANDIDATES = ("Name", "Mitarbeiter", "Employee", "Person", "Titel")
SPAN_COLUMN_CANDIDATES = ("Wann?", "Wann", "Date", "Zeitraum", "Zeitspanne")

def parse_wann(wann_str):
    if pd.isna(wann_str):
        return pd.NaT, pd.NaT
    wann_str = str(wann_str)
    parts = wann_str.split("→")
    start = parts[0].strip()
    end = parts[1].strip() if len(parts) > 1 else None
    try:
        start = re.sub(r'\s*\(GMT[^\)]*\)', '', start)
        # Handle European date format (DD/MM/YYYY) with dayfirst=True
        start_time = pd.to_datetime(start, dayfirst=True)
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
                    date_only = pd.to_datetime(end.strip(), dayfirst=True).date()
                    end = datetime.combine(date_only, time(23, 59, 59))
                except Exception:
                    end = pd.NaT
        try:
            if not isinstance(end, datetime):
                end_time = pd.to_datetime(end, dayfirst=True)
            else:
                end_time = end
        except Exception:
            end_time = pd.NaT
    return start_time, end_time

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

def transform_to_schedule_format(df, person_info):
    info_lookup = {name: (location, task) for name, location, task in person_info}
    df_copy = df.copy()
    df_copy['Start Time'] = df_copy['Start Time'] - pd.Timedelta(hours=2)
    df_copy['End Time'] = df_copy['End Time'] - pd.Timedelta(hours=2)
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

def _select_best_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    best_col = None
    best_count = -1
    for col in candidates:
        if col not in df.columns:
            continue
        values = df[col].dropna().astype(str).str.strip()
        non_empty = (values != "").sum()
        if non_empty > best_count:
            best_col = col
            best_count = int(non_empty)
    return best_col


def _prepare_availability_dataframe(availability_data):
    if not isinstance(availability_data, pd.DataFrame):
        raise TypeError("availability_data must be a pandas DataFrame.")
    df = availability_data.copy()
    if df.empty:
        raise ValueError("availability_data is empty.")

    name_col = _select_best_column(df, NAME_COLUMN_CANDIDATES)
    if not name_col:
        raise ValueError("availability_data must contain a name column (e.g. Name).")
    if name_col != "Name":
        df = df.rename(columns={name_col: "Name"})

    has_start_end = "Start Time" in df.columns and "End Time" in df.columns
    if has_start_end:
        df["Start Time"] = pd.to_datetime(df["Start Time"], dayfirst=True, errors="coerce")
        df["End Time"] = pd.to_datetime(df["End Time"], dayfirst=True, errors="coerce")
    else:
        span_col = _select_best_column(df, SPAN_COLUMN_CANDIDATES)
        if not span_col:
            raise ValueError(
                "availability_data must contain either 'Start Time'/'End Time' "
                "or a timespan column like 'Wann?'."
            )
        df[["Start Time", "End Time"]] = df[span_col].apply(lambda x: pd.Series(parse_wann(x)))

    df = df[df["Start Time"].notna()].copy()
    df = split_multiday_entries(df)
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
    df = normalize_long_shifts(df)

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
