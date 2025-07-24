import pandas as pd
import re
from datetime import datetime, timedelta, time
import difflib

# Replace this with the path to your CSV file
csv_file_path = "Schichtplan/Verfuegbarkeiten_Juli.csv"
start_date = "2025-07-01"
end_date= "2025-07-31"

# Load the CSV
df = pd.read_csv(csv_file_path)

def parse_wann(wann_str):
    if pd.isna(wann_str):
        return pd.NaT, pd.NaT

    parts = wann_str.split("→")
    start = parts[0].strip()
    end = parts[1].strip() if len(parts) > 1 else None

    try:
        start = re.sub(r'\s*\(GMT[^\)]*\)', '', start)
        start_time = pd.to_datetime(start)
    except Exception:
        start_time = pd.NaT

    end_time = pd.NaT
    if end:
        if re.match(r"^\d{1,2}:\d{2}", end):
            # Only time is given, assume same date as start
            end = f"{start_time.strftime('%B %d, %Y')} {end}"
        else:
            end = re.sub(r'\s*\(GMT[^\)]*\)', '', end)
            # If end has no time, we assume it's a date and set time to end of day
            if re.match(r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}$", end.strip()):
                try:
                    date_only = pd.to_datetime(end.strip()).date()
                    end = datetime.combine(date_only, time(23, 59, 59))
                except Exception:
                    end = pd.NaT
        try:
            if not isinstance(end, datetime):
                end_time = pd.to_datetime(end)
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

        # If no end time or start/end on same day, keep as-is
        if pd.isna(end) or start.date() == end.date():
            split_rows.append(row)
            continue

        current_start = start

        while current_start.date() < end.date():
            # End of current day (23:59:59)
            current_end = datetime.combine(current_start.date(), datetime.max.time()).replace(microsecond=0)
            new_row = row.copy()
            new_row['Start Time'] = current_start
            new_row['End Time'] = current_end
            split_rows.append(new_row)

            # Move to next day
            current_start = current_end + timedelta(seconds=1)

        # Final chunk: last day
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

def printall(df):
    with pd.option_context(
        'display.max_rows', None,
        'display.max_columns', None,
        'display.width', None,
        'display.max_colwidth', None
    ):
        print(df)

# Function to find the closest match
def match_name(name):
    if pd.isna(name):
        return None
    name = str(name).strip()
    match = difflib.get_close_matches(name, name_list, n=1, cutoff=0.6)
    return match[0] if match else None


def generate_fixed_schedule_entries(start_date, end_date, schedule_dict):
    rows = []

    # Ensure dates are datetime.date
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
    # Convert tuple list to a lookup dictionary
    info_lookup = {name: (location, task) for name, location, task in person_info}
    
    # Make a copy to avoid changing original
    df_copy = df.copy()

    # Shift Start Time and End Time by 2 hours earlier
    df_copy['Start Time'] = df_copy['Start Time'] - pd.Timedelta(hours=2)
    df_copy['End Time'] = df_copy['End Time'] - pd.Timedelta(hours=2)

    # Create Date column in the "Start → End" format
    df_copy['Date'] = df_copy['Start Time'].dt.strftime('%Y-%m-%d %H:%M:%S') + ' → ' + df_copy['End Time'].dt.strftime('%Y-%m-%d %H:%M:%S')

    # Map name and extract info
    df_copy['Name'] = df_copy['new_name']
    df_copy['Employee'] = df_copy['new_name']
    df_copy['Task'] = df_copy['new_name'].map(lambda name: info_lookup.get(name, (None, None))[1])
    df_copy['Location'] = df_copy['new_name'].map(lambda name: info_lookup.get(name, (None, None))[0])

    # Reorder columns
    final_df = df_copy[['Name', 'Date', 'Employee', 'Task', 'Location']]

    return final_df


# Apply the parsing function
df[['Start Time', 'End Time']] = df['Wann?'].apply(lambda x: pd.Series(parse_wann(x)))
df = split_multiday_entries(df)

df = fill_missing_end_times(df)
df = df[df['Name'].notna()]
# Remove leading and trailing blank spaces in df['Name']
df['Name'] = df['Name'].str.strip()
#List of known clean names
unique_names = df['Name'].dropna().unique()
person_info = [
    ('Max', 'ALT', 'Barista'),
    ('Arne', 'WIE', 'Bakery'),
    ('Emil', 'ALT', 'Barista'),
    ('Sarah N', 'ALT', 'Service'),
    ('Sarah S', 'ALT', 'Barista'),
    ('Lara', 'WIE', 'Barista'),
    ('Till', 'WIE', 'Barista'),
    ('Kenta', 'ALT', 'Barista'),
    ('Pauline', 'WIE', 'Service'),
    ('Nina', 'ALT', 'Barista'),
    ('Simon', 'WIE', 'Bakery'),
    ('Ula', 'WIE', 'Bakery'),
    ('Moritz', 'ALT', 'Barista'),
    ('Hannah', 'WIE', 'Service'),
    ('Jaime', 'WIE', 'Barista'),
    ('Alex', 'WIE', 'Barista'),
    ('Annabell', 'WIE', 'Barista'),
    ('Sarah W', 'WIE', 'Service'),
    ('Mareike', 'WIE', 'Bakery'),
    ('Arne', 'WIE', 'Bakery'),
    ('Elio', 'WIE', 'Barista'),
    ('Arne', 'WIE', 'Barista'),
]
name_list = [name for name, _, _ in person_info]

df['new_name'] = df['Name'].apply(match_name)
df = df[df['new_name'].notna()]

df = normalize_long_shifts(df)


# Weekly schedule definition
fixed_schedules = {
    'Simon': {
        'days': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
        'start_time': time(8, 0),
        'end_time': time(16, 0)
    },
    'Ula': {
        'days': ['Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
        'start_time': time(8, 0),
        'end_time': time(16, 0)
    },
    # 'Jaime': {
    #     'days': ['Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
    #     'start_time': time(8, 0),
    #     'end_time': time(16, 0)
    # }
}


fixed_df = generate_fixed_schedule_entries(start_date, end_date, fixed_schedules)
df = pd.concat([df, fixed_df], ignore_index=True)

formatted_df = transform_to_schedule_format(df, person_info)
formatted_df.to_csv("schedule_export.csv", index=False)

# Split by location
df_alt = formatted_df[formatted_df['Location'] == 'ALT']
df_wie = formatted_df[formatted_df['Location'] == 'WIE']
# Concatenate df_alt and df_wie
df_both_locations = pd.concat([df_alt, df_wie], ignore_index=True)

# Write the concatenated DataFrame to a CSV file
df_both_locations.to_csv("Schichtplan/both_locations.csv", index=False)
# Export to CSV
df_alt.to_csv("Schichtplan/schedule_ALT.csv", index=False)
df_wie.to_csv("Schichtplan/schedule_WIE.csv", index=False)

# Show the parsed DataFrame
printall(df[['new_name', 'Start Time', 'End Time']])


# Extract names from person_info
person_info_names = [name for name, _, _ in person_info]

# Names in unique_names but not in person_info
names_not_in_person_info = set(unique_names) - set(person_info_names)

# Names in person_info but not in unique_names
names_not_in_unique_names = set(person_info_names) - set(unique_names)

print("Names in unique_names but not in person_info:")
print(names_not_in_person_info)

print("\nNames in person_info but not in unique_names:")
print(names_not_in_unique_names)