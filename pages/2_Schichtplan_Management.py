import datetime
import io
import logging
import os
import re
import shutil
from urllib.parse import parse_qs, urlparse

import pandas as pd
import streamlit as st

from src.app_paths import SCHICHTPLAN_DATA_DIR
from src.notion_access import NotionRequestError, flatten_properties, notion_request
from src.schichtplan_utils import generate_schichtplan

# Page title
st.title("👥 Schichtplan Management")
logger = logging.getLogger(__name__)

PERSON_INFO_CSV_PATH = SCHICHTPLAN_DATA_DIR / "mitarbeiter_info.csv"
PERSON_INFO_EXCEL_PATH = SCHICHTPLAN_DATA_DIR / "mitarbeiter_info.xlsx"
PERSON_INFO_BACKUP_DIR = SCHICHTPLAN_DATA_DIR / "backups"
PERSON_INFO_COLUMNS = ["Name", "Location", "Task"]

# Fixed schedules configuration (weekly recurring shifts)
FIXED_SCHEDULES = {
    "Jaime": {
        "days": ["Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
        "start_time": datetime.time(9, 0),
        "end_time": datetime.time(17, 0),
    },
    "Ula": {
        "days": ["Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
        "start_time": datetime.time(9, 0),
        "end_time": datetime.time(17, 0),
    },
    "Lennard": {
        "days": ["Monday", "Tuesday", "Wednesday", "Friday"],
        "start_time": datetime.time(9, 0),
        "end_time": datetime.time(17, 0),
    },
    "Kathi": {
        "days": [ "Thursday"],
        "start_time": datetime.time(9, 0),
        "end_time": datetime.time(17, 0),
    },
}


def _normalize_person_info_df(df: pd.DataFrame) -> pd.DataFrame:
    for col in PERSON_INFO_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[PERSON_INFO_COLUMNS].copy()

    for col in PERSON_INFO_COLUMNS:
        df[col] = df[col].fillna("").astype(str).str.strip()

    return df[(df["Name"] != "") | (df["Location"] != "") | (df["Task"] != "")]


def _bootstrap_person_info_csv_from_excel_if_needed() -> bool:
    """One-time migration path: create CSV from existing Excel if CSV is missing."""
    if PERSON_INFO_CSV_PATH.exists() or not PERSON_INFO_EXCEL_PATH.exists():
        return False

    try:
        df = pd.read_excel(PERSON_INFO_EXCEL_PATH)
        if not set(PERSON_INFO_COLUMNS).issubset(df.columns):
            logger.warning(
                "Excel bootstrap skipped: missing required columns. found=%s required=%s",
                list(df.columns),
                PERSON_INFO_COLUMNS,
            )
            return False

        normalized = _normalize_person_info_df(df)
        normalized.to_csv(PERSON_INFO_CSV_PATH, index=False)
        logger.info(
            "Bootstrapped person info CSV from Excel: %s -> %s rows=%d",
            PERSON_INFO_EXCEL_PATH,
            PERSON_INFO_CSV_PATH,
            len(normalized),
        )
        st.info(
            f"ℹ️ Mitarbeiter-Info wurde einmalig von `{PERSON_INFO_EXCEL_PATH}` nach "
            f"`{PERSON_INFO_CSV_PATH}` migriert."
        )
        return True
    except Exception:
        logger.exception("Failed bootstrapping CSV from Excel")
        return False


def ensure_session_person_info_backup():
    """Create one timestamped backup once per Streamlit session."""
    backup_state_key = "person_info_backup_path"
    if backup_state_key in st.session_state:
        return

    st.session_state[backup_state_key] = ""
    if not PERSON_INFO_CSV_PATH.exists():
        return

    try:
        os.makedirs(PERSON_INFO_BACKUP_DIR, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = PERSON_INFO_BACKUP_DIR / f"mitarbeiter_info.backup-{timestamp}.csv"
        shutil.copyfile(PERSON_INFO_CSV_PATH, backup_path)
        st.session_state[backup_state_key] = str(backup_path)
        logger.info("Created session backup for person info: %s", backup_path)
    except Exception:
        logger.exception("Failed to create session backup for person info")


def load_person_info_from_csv():
    """Load person info from CSV single source of truth."""
    _bootstrap_person_info_csv_from_excel_if_needed()
    ensure_session_person_info_backup()

    if not PERSON_INFO_CSV_PATH.exists():
        logger.warning("Person info CSV missing: %s", PERSON_INFO_CSV_PATH)
        st.warning(
            f"Die Datei `{PERSON_INFO_CSV_PATH}` wurde nicht gefunden. "
            "Lege sie mit den Spalten `Name`, `Location`, `Task` an oder speichere im Editor."
        )
        return []

    try:
        logger.info("Loading Mitarbeiter-Info from CSV: %s", PERSON_INFO_CSV_PATH)
        df = pd.read_csv(PERSON_INFO_CSV_PATH, dtype=str).fillna("")
        normalized = _normalize_person_info_df(df)
        logger.info("Loaded %d Mitarbeiter-Info rows from CSV", len(normalized))
        return normalized.to_dict(orient="records")
    except Exception as e:
        logger.exception("Failed to load Mitarbeiter-Info from CSV")
        st.error(
            f"Fehler beim Laden der Mitarbeiter-Info aus `{PERSON_INFO_CSV_PATH}`: {e}. "
            "Bitte prüfe die Datei und lade die Seite neu."
        )
        return []


def save_person_info_to_csv(person_rows) -> tuple[bool, str]:
    """Validate and save editor rows to the CSV single source of truth."""
    if isinstance(person_rows, pd.DataFrame):
        rows = person_rows.to_dict(orient="records")
    elif isinstance(person_rows, list):
        rows = person_rows
    else:
        return False, "Ungültiges Datenformat im Editor."

    cleaned_rows = []
    seen_names = set()
    duplicate_names = set()
    incomplete_rows = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        location = str(row.get("Location") or "").strip()
        task = str(row.get("Task") or "").strip()

        # Ignore fully empty editor rows.
        if not name and not location and not task:
            continue

        if not name or not location or not task:
            incomplete_rows += 1
            continue

        key = name.casefold()
        if key in seen_names:
            duplicate_names.add(name)
            continue
        seen_names.add(key)

        cleaned_rows.append({"Name": name, "Location": location, "Task": task})

    if incomplete_rows > 0:
        return False, "Es gibt unvollständige Zeilen. Bitte Name, Location und Task überall ausfüllen."
    if duplicate_names:
        duplicates = ", ".join(sorted(duplicate_names))
        return False, f"Doppelte Namen gefunden: {duplicates}. Bitte bereinigen."

    try:
        os.makedirs(SCHICHTPLAN_DATA_DIR, exist_ok=True)
        pd.DataFrame(cleaned_rows, columns=PERSON_INFO_COLUMNS).to_csv(PERSON_INFO_CSV_PATH, index=False)
        logger.info("Saved %d person info rows to CSV: %s", len(cleaned_rows), PERSON_INFO_CSV_PATH)
        return True, f"Mitarbeiter-Info gespeichert ({len(cleaned_rows)} Zeilen)."
    except Exception as e:
        logger.exception("Failed to save person info CSV")
        return False, f"Fehler beim Speichern: {e}"


def extract_notion_database_id(database_ref: str) -> str | None:
    """Extract a Notion database ID from either a raw ID or a Notion URL."""
    raw = (database_ref or "").strip()
    if not raw:
        return None

    compact = raw.replace("-", "")
    if re.fullmatch(r"[0-9a-fA-F]{32}", compact):
        return compact.lower()

    # Match either 32-char notion ids or UUID-style ids.
    id_pattern = r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})"
    direct_match = re.search(id_pattern, raw)
    if direct_match:
        return direct_match.group(1).replace("-", "").lower()

    try:
        parsed = urlparse(raw)
    except Exception:
        logger.exception("Could not parse Notion reference URL: %r", raw)
        return None

    # Fallback to path/query scan in case encoded URLs bypass the direct scan.
    path_match = re.search(id_pattern, parsed.path or "")
    if path_match:
        return path_match.group(1).replace("-", "").lower()

    for key in ("v", "p", "id"):
        params = parse_qs(parsed.query).get(key, [])
        if params:
            query_match = re.search(id_pattern, params[0] or "")
            if query_match:
                return query_match.group(1).replace("-", "").lower()

    logger.error("Could not extract Notion database ID from input: %r", raw)

    return None


def _format_notion_date_value_for_wann(value: str | None) -> str | None:
    """Format a Notion date value to match local CSV-style 'Wann?' strings."""
    raw = (value or "").strip()
    if not raw:
        return None

    has_time_component = "T" in raw
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        # Keep unknown inputs as-is so downstream parsing still has a chance.
        return raw

    # Keep wall-clock times from Notion and avoid mixing aware/naive datetimes later.
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)

    if has_time_component:
        return parsed.strftime("%B %d, %Y %H:%M")
    return parsed.strftime("%B %d, %Y")


def _notion_date_to_wann_span(date_prop: dict | None) -> str | None:
    """Convert Notion date payload into a 'start → end' timespan string."""
    if not isinstance(date_prop, dict):
        return None

    start = _format_notion_date_value_for_wann(date_prop.get("start"))
    if not start:
        return None

    end = _format_notion_date_value_for_wann(date_prop.get("end"))
    return f"{start} → {end}" if end else start


def fetch_person_info_from_notion(database_ref: str):
    """Fetch person info rows from Notion and return normalized and raw rows."""
    logger.info("Fetching Mitarbeiter-Info from Notion")
    token = os.getenv("NOTION_TOKEN")
    if not token:
        logger.error("NOTION_TOKEN missing while fetching Notion person data")
        raise RuntimeError("Missing NOTION_TOKEN in environment or .env")

    database_id = extract_notion_database_id(database_ref) or os.getenv("NOTION_DATABASE_ID", "").strip()
    if not database_id:
        logger.error("Notion DB extraction failed for input: %r", database_ref)
        raise ValueError(
            "Could not extract Notion database ID from input and NOTION_DATABASE_ID is not set."
        )

    payload = {"page_size": 100}
    rows = []
    page_count = 0
    while True:
        data = notion_request("POST", f"/databases/{database_id}/query", token, payload)
        rows.extend(data.get("results", []))
        page_count += 1
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")

    people = []
    raw_rows = []
    for row in rows:
        properties = row.get("properties", {})
        flat = flatten_properties(properties)

        # Normalize Notion date properties to the same span format as local CSV exports.
        for key, prop in properties.items():
            if isinstance(prop, dict) and prop.get("type") == "date":
                span = _notion_date_to_wann_span(prop.get("date"))
                if span:
                    flat[key] = span

        raw_rows.append(flat)

        name_candidates = [
            "Name",
            "Mitarbeiter",
            "Employee",
            "Person",
            "Titel",
        ]
        location_candidates = ["Location", "Standort", "Ort"]
        task_candidates = ["Task", "Aufgabe", "Rolle", "Role"]

        name = next((str(flat.get(k)).strip() for k in name_candidates if flat.get(k)), "")
        location = next((str(flat.get(k)).strip() for k in location_candidates if flat.get(k)), "")
        task = next((str(flat.get(k)).strip() for k in task_candidates if flat.get(k)), "")

        if name or location or task:
            people.append({"Name": name, "Location": location, "Task": task})

    logger.info(
        "Notion fetch complete. pages=%d raw_rows=%d normalized_rows=%d",
        page_count,
        len(raw_rows),
        len(people),
    )
    return people, raw_rows

def display_name_evaluation(evaluation):
    """Display comprehensive name evaluation results."""
    if not evaluation:
        return

    names_in_availability_not_in_person_info = evaluation.get(
        "names_in_availability_not_in_person_info",
        evaluation.get("names_in_csv_not_in_person_info", []),
    )
    names_in_person_info_not_in_availability = evaluation.get(
        "names_in_person_info_not_in_availability",
        evaluation.get("names_in_person_info_not_in_csv", []),
    )
    matched_names = evaluation.get("successfully_matched_names", [])
    unmatched_names = evaluation.get("unmatched_names", [])

    st.subheader("📊 Konsistenzprüfung")

    # Create metrics columns
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Names in availability_data", evaluation.get("total_original_names", 0))
    with col2:
        st.metric("Matched to person_info", evaluation.get("total_matched", 0))
    with col3:
        st.metric("Names in person_info", evaluation.get("total_person_info_names", 0))
    with col4:
        match_rate = 0
        if evaluation.get("total_original_names", 0) > 0:
            match_rate = round(evaluation.get("total_matched", 0) / evaluation.get("total_original_names", 0) * 100, 1)
        st.metric("Match Rate", f"{match_rate}%")
    
    # Display format validation results
    if "format_validation" in evaluation:
        format_check = evaluation["format_validation"]
        if format_check['valid']:
            st.success(f"✅ **Format Check:** {format_check['message']}")
        else:
            st.error(f"❌ **Format Check:** {format_check['message']}")
        
        if 'details' in format_check:
            st.caption(f"ℹ️ {format_check['details']}")

    with st.expander("✅ Matched employees", expanded=True):
        if matched_names:
            for name in matched_names:
                st.success(f"• `{name}`")
        else:
            st.info("Keine Matches gefunden.")

    with st.expander("⚠️ Employees in availability_data but not in person_info", expanded=True):
        if names_in_availability_not_in_person_info:
            st.warning(
                "Diese Namen stehen in `availability_data`, aber nicht in `person_info`."
            )
            for name in names_in_availability_not_in_person_info:
                st.write(f"• `{name}`")
        else:
            st.success("Keine fehlenden Mitarbeitenden in person_info.")

    with st.expander("ℹ️ Employees in person_info but not in availability_data", expanded=False):
        if names_in_person_info_not_in_availability:
            st.info(
                "Diese Mitarbeitenden stehen in `person_info`, aber nicht in `availability_data`."
            )
            for name in names_in_person_info_not_in_availability:
                st.write(f"• `{name}`")
        else:
            st.success("Keine fehlenden Mitarbeitenden in availability_data.")

    if unmatched_names:
        with st.expander("❌ Unmatched names (fuzzy matching)", expanded=False):
            st.error("Diese Namen konnten auch per Fuzzy-Matching nicht zugeordnet werden:")
            for name in unmatched_names:
                st.write(f"• `{name}`")

def get_next_month_dates():
    """Calculate the first and last day of next month."""
    import datetime
    
    today = datetime.date.today()
    
    # Calculate first and last day of next month
    if today.month == 12:
        first_day_next_month = datetime.date(today.year + 1, 1, 1)
    else:
        first_day_next_month = datetime.date(today.year, today.month + 1, 1)
    
    # Last day: get first day of month after next, then subtract one day
    if first_day_next_month.month == 12:
        first_day_month_after_next = datetime.date(first_day_next_month.year + 1, 1, 1)
    else:
        first_day_month_after_next = datetime.date(first_day_next_month.year, first_day_next_month.month + 1, 1)
    last_day_next_month = first_day_month_after_next - datetime.timedelta(days=1)
    
    return first_day_next_month, last_day_next_month

def save_uploaded_file(uploaded_file):
    """Save uploaded file to availabilities folder with its original name."""
    if uploaded_file is None:
        return None
    
    # Create availabilities directory if it doesn't exist
    availabilities_dir = SCHICHTPLAN_DATA_DIR / "availabilities"
    os.makedirs(availabilities_dir, exist_ok=True)
    
    # Save file with original name
    file_path = availabilities_dir / uploaded_file.name
    logger.info("Saving availability upload: name=%s path=%s", uploaded_file.name, file_path)
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    
    logger.info("Saved availability upload successfully: %s", file_path)
    return file_path

def quick_format_validation(csv_file_path):
    """Quick validation of timespan format compatibility."""
    try:
        df = pd.read_csv(csv_file_path)
        return quick_format_validation_from_dataframe(df)
    except Exception as e:
        return {'valid': False, 'message': f"Format validation error: {str(e)}"}


def quick_format_validation_from_dataframe(df: pd.DataFrame):
    """Quick validation of timespan format compatibility for an in-memory dataframe."""
    try:
        from src.schichtplan_utils import parse_wann
        has_start_end = "Start Time" in df.columns and "End Time" in df.columns
        tested_count = 0
        parse_errors = 0
        source_col = ""

        if has_start_end:
            source_col = "Start Time/End Time"
            start_samples = df["Start Time"].dropna().head(3)
            tested_count = len(start_samples)
            if tested_count == 0:
                return {'valid': False, 'message': "No start time data found"}
            parsed_start = pd.to_datetime(start_samples, dayfirst=True, errors="coerce")
            parse_errors = int(parsed_start.isna().sum())
        else:
            span_candidates = ["Wann?", "Wann", "Date", "Zeitraum", "Zeitspanne"]
            span_col = None
            best_count = -1
            for col in span_candidates:
                if col not in df.columns:
                    continue
                count = int((df[col].dropna().astype(str).str.strip() != "").sum())
                if count > best_count:
                    span_col = col
                    best_count = count

            if not span_col:
                return {'valid': False, 'message': "Missing timespan data column (e.g. Wann? or Date)"}

            source_col = span_col
            samples = df[span_col].dropna().astype(str).str.strip()
            samples = samples[samples != ""].unique()[:3]
            tested_count = len(samples)
            if tested_count == 0:
                return {'valid': False, 'message': f"No timespan data found in '{span_col}'"}

            for span in samples:
                try:
                    start_time, _ = parse_wann(span)
                    if pd.isna(start_time):
                        parse_errors += 1
                except Exception:
                    parse_errors += 1

        success_rate = ((tested_count - parse_errors) / tested_count) * 100

        return {
            'valid': success_rate >= 80,
            'message': f"Timespan format validation ({source_col}): {success_rate:.0f}% success rate",
            'details': f"Tested {tested_count} format(s), {parse_errors} errors"
        }
    except Exception as e:
        return {'valid': False, 'message': f"Format validation error: {str(e)}"}


def extract_name_values(df: pd.DataFrame):
    """Return cleaned name values from the strict `Name` column only."""
    if "Name" not in df.columns:
        return None, pd.Series(dtype="object")

    values = df["Name"].dropna().astype(str).str.strip()
    values = values[values != ""]
    return "Name", values


def perform_availability_evaluation_from_dataframe(df, person_info, source_label: str = "availability_data"):
    """Perform name evaluation analysis on availability_data dataframe."""
    try:
        logger.info(
            "Starting availability evaluation: source=%s rows=%d person_info_entries=%d",
            source_label,
            len(df),
            len(person_info),
        )

        name_col, name_values = extract_name_values(df)
        if not name_col:
            logger.warning("Evaluation aborted: no supported name column in %s", source_label)
            return {"error": "Data must contain the column: Name"}
        logger.info(
            "Using name column '%s' for evaluation source=%s non_empty_names=%d",
            name_col,
            source_label,
            len(name_values),
        )

        # Quick format validation (optional depending on available columns)
        format_check = quick_format_validation_from_dataframe(df)

        # Get unique names from the availability data
        original_unique_names = name_values.unique()

        # Create name lists from person info
        name_list = [name for name, _, _ in person_info if name]
        person_info_names = set(name_list)

        # Import the matching function from schichtplan_utils
        from src.schichtplan_utils import match_name

        # Perform name matching analysis similar to generate_schichtplan
        unique_names_set = set(original_unique_names)

        # Names in availability data but not in person_info
        names_not_in_person_info = unique_names_set - person_info_names

        # Names in person_info but not in availability data
        names_not_in_availability = person_info_names - unique_names_set

        # Names that can be successfully matched
        matched_names = set()
        unmatched_names = set()

        for name in original_unique_names:
            matched = match_name(name, name_list)
            if matched:
                matched_names.add(matched)
            else:
                unmatched_names.add(name)

        evaluation = {
            'names_in_availability_not_in_person_info': sorted(list(names_not_in_person_info)),
            'names_in_person_info_not_in_availability': sorted(list(names_not_in_availability)),
            'successfully_matched_names': sorted(list(matched_names)),
            'unmatched_names': sorted(list(unmatched_names)),
            'total_original_names': len(original_unique_names),
            'total_person_info_names': len(person_info_names),
            'total_matched': len(matched_names),
            'format_validation': format_check
        }
        # Backward-compatible aliases for older UI/state consumers.
        evaluation["names_in_csv_not_in_person_info"] = evaluation["names_in_availability_not_in_person_info"]
        evaluation["names_in_person_info_not_in_csv"] = evaluation["names_in_person_info_not_in_availability"]

        logger.info(
            "Evaluation complete: source=%s total_names=%d matched=%d unmatched=%d",
            source_label,
            evaluation["total_original_names"],
            evaluation["total_matched"],
            len(evaluation["unmatched_names"]),
        )
        return evaluation

    except Exception:
        logger.exception("Evaluation failed for source=%s", source_label)
        return {"error": "Error analyzing data source"}

def perform_upload_evaluation(csv_file_path, person_info):
    """Perform name evaluation analysis on uploaded file."""
    try:
        # Read the uploaded CSV
        df = pd.read_csv(csv_file_path)
        evaluation = perform_availability_evaluation_from_dataframe(
            df,
            person_info,
            source_label=str(csv_file_path),
        )
        return evaluation
        
    except Exception as e:
        logger.exception("Evaluation failed for file=%s", csv_file_path)
        return {"error": f"Error analyzing file: {str(e)}"}


def to_person_info_tuples(person_rows, require_full: bool = True):
    """Convert row dicts into (Name, Location, Task) tuples."""
    if isinstance(person_rows, pd.DataFrame):
        person_rows = person_rows.to_dict(orient="records")
    elif not isinstance(person_rows, list):
        return []

    rows = []
    for person in person_rows:
        name = (person.get("Name") or "").strip() if isinstance(person.get("Name"), str) else person.get("Name")
        location = person.get("Location") or ""
        task = person.get("Task") or ""
        if require_full:
            if name and location and task:
                rows.append((name, location, task))
        else:
            if name:
                rows.append((name, location, task))
    return rows


# Get next month dates
first_day_next_month, last_day_next_month = get_next_month_dates()

if "availability_source" not in st.session_state:
    st.session_state["availability_source"] = "Lokal (CSV)"
if "notion_availability_data" not in st.session_state:
    st.session_state["notion_availability_data"] = []
if "notion_availability_raw_data" not in st.session_state:
    st.session_state["notion_availability_raw_data"] = []
if "notion_availability_url" not in st.session_state:
    st.session_state["notion_availability_url"] = ""
if "last_saved_upload_signature" not in st.session_state:
    st.session_state["last_saved_upload_signature"] = ""


st.subheader("1) Availability-Datenquelle")
source_col1, source_col2 = st.columns([1, 2])
uploaded_file = None
with source_col1:
    selected_source = st.radio(
        "Datenquelle",
        options=["Lokal (CSV)", "Notion"],
        key="availability_source",
    )
with source_col2:
    if selected_source == "Notion":
        st.text_input(
            "Notion Availability-Datenbank URL oder ID",
            key="notion_availability_url",
            placeholder="https://www.notion.so/... oder 32-stellige DB-ID",
        )
        if st.button("📥 Availability aus Notion laden", key="load_availability_from_notion"):
            logger.info("User triggered Notion availability data load")
            try:
                with st.spinner("Lade Availability-Daten aus Notion ..."):
                    notion_data, notion_raw_data = fetch_person_info_from_notion(
                        st.session_state.get("notion_availability_url", "")
                    )
                st.session_state["notion_availability_data"] = notion_data
                st.session_state["notion_availability_raw_data"] = notion_raw_data
                logger.info("Notion availability data stored in session. rows=%d", len(notion_raw_data))
                st.success(f"✅ {len(notion_raw_data)} Availability-Einträge aus Notion geladen.")
            except NotionRequestError as e:
                logger.exception("Notion API error while loading availability data.")
                st.error(f"❌ Notion API Fehler ({e.status_code}): {e}")
            except Exception as e:
                logger.exception("Unexpected error while loading availability data from Notion.")
                st.error(f"❌ Fehler beim Laden aus Notion: {e}")
    else:
        st.caption("CSV mit Verfügbarkeiten hochladen.")
        uploaded_file = st.file_uploader(
            "📁 Upload CSV with Availability",
            type=["csv"],
            help="CSV file with columns 'Name' and 'Wann?'",
            key="availability_uploader",
        )

if uploaded_file is not None:
    upload_signature = f"{uploaded_file.name}:{uploaded_file.size}"
    if st.session_state.get("last_saved_upload_signature") != upload_signature:
        saved_path = save_uploaded_file(uploaded_file)
        if saved_path:
            st.session_state["last_saved_upload_signature"] = upload_signature
            st.success(f"✅ Datei '{uploaded_file.name}' wurde gespeichert.")

# Central availability_data used for consistency checks.
availability_data = None
availability_data_source = ""
if selected_source == "Notion":
    raw_notion_data = st.session_state.get("notion_availability_raw_data", [])
    normalized_notion_data = st.session_state.get("notion_availability_data", [])
    if raw_notion_data:
        availability_data = pd.DataFrame(raw_notion_data)
        availability_data_source = "Notion"
    elif normalized_notion_data:
        availability_data = pd.DataFrame(normalized_notion_data)
        availability_data_source = "Notion (normalisiert)"
else:
    if uploaded_file is not None:
        try:
            availability_data = pd.read_csv(io.BytesIO(uploaded_file.getvalue()))
            availability_data_source = f"Lokal/Upload: {uploaded_file.name}"
        except Exception:
            logger.exception("Failed reading uploaded availability file: %s", uploaded_file.name)
            st.error(f"❌ Fehler beim Laden der hochgeladenen Verfügbarkeitsdatei: {uploaded_file.name}")

has_availability_data = availability_data is not None and not availability_data.empty

if selected_source == "Notion" and not has_availability_data:
    st.info("Noch keine Notion-Availability geladen. URL eintragen und auf 'Availability aus Notion laden' klicken.")

with st.expander("👀 Availability-Datenvorschau", expanded=True):
    if has_availability_data:
        st.caption(f"{len(availability_data)} Zeilen availability_data geladen ({availability_data_source})")
        st.dataframe(availability_data, width="stretch", hide_index=True)
    else:
        st.caption("Noch keine availability_data geladen.")

st.subheader("2) Mitarbeiter-Info (CSV Single Source)")
person_info_default = load_person_info_from_csv()
person_info_default_df = pd.DataFrame(person_info_default, columns=PERSON_INFO_COLUMNS)
with st.expander("📝 Mitarbeiter-Info Editor", expanded=True):
    st.markdown(
        "Bearbeite die Liste der Mitarbeiter:innen, deren Location und Aufgabe. "
        "Die Stammdaten werden aus der CSV-Datei "
        f"`{PERSON_INFO_CSV_PATH}` geladen."
    )
    backup_path = st.session_state.get("person_info_backup_path")
    if backup_path:
        st.caption(f"Session-Backup erstellt: `{backup_path}`")
    st.caption(
        "Änderungen im Editor werden erst mit 'Mitarbeiter-Info speichern' dauerhaft in der CSV gespeichert."
    )
    person_info_data = st.data_editor(
        person_info_default_df,
        num_rows="dynamic",
        width="stretch",
        key="person_info_editor",
        column_config={
            "Name": st.column_config.TextColumn("Name", required=True),
            "Location": st.column_config.SelectboxColumn(
                "Location", 
                options=["ALT", "WIE", "BAK"], 
                required=True
            ),
            "Task": st.column_config.SelectboxColumn(
                "Task", 
                options=["Barista", "Service", "Bakery"], 
                required=True
            ),
        }
    )
    st.caption(f"Aktuelle person_info Zeilen: {len(person_info_data)}")
    action_col1, action_col2 = st.columns(2)
    with action_col1:
        if st.button("💾 Mitarbeiter-Info speichern", width="stretch", key="save_person_info_csv"):
            success, message = save_person_info_to_csv(person_info_data)
            if success:
                st.success(f"✅ {message}")
                st.rerun()
            else:
                st.error(f"❌ {message}")
    with action_col2:
        if st.button("🔄 Aus CSV neu laden", width="stretch", key="reload_person_info_csv"):
            if "person_info_editor" in st.session_state:
                del st.session_state["person_info_editor"]
            st.rerun()

person_info = to_person_info_tuples(person_info_data, require_full=False)
person_info_for_generation = to_person_info_tuples(person_info_data, require_full=True)
has_person_info = bool(person_info)
has_person_info_for_generation = bool(person_info_for_generation)

st.subheader("3) Konsistenzprüfung")
if availability_data_source:
    st.caption(f"Aktive availability_data Quelle: `{availability_data_source}`")

if not has_availability_data:
    st.info("Keine availability_data verfügbar. Bitte im Schritt 1 Daten laden.")
if not has_person_info:
    st.info("Keine gültige Mitarbeiter-Info verfügbar. Bitte `mitarbeiter_info.csv` prüfen.")

current_evaluation_key = f"{availability_data_source}::{len(availability_data) if has_availability_data else 0}::{len(person_info)}"
if st.session_state.get("evaluation_cache_key") != current_evaluation_key:
    st.session_state["evaluation_cache_key"] = current_evaluation_key
    st.session_state["evaluation_for_selected"] = None

evaluation_for_selected = st.session_state.get("evaluation_for_selected")
can_run_consistency_check = has_availability_data and has_person_info

if st.button("🔍 Konsistenzprüfung ausführen", key="run_consistency_check", disabled=not can_run_consistency_check):
    with st.spinner("Prüfe Datenkonsistenz ..."):
        evaluation_for_selected = perform_availability_evaluation_from_dataframe(
            availability_data,
            person_info,
            source_label=availability_data_source or "availability_data",
        )
    st.session_state["evaluation_for_selected"] = evaluation_for_selected

    if "error" in evaluation_for_selected:
        logger.error(
            "Consistency check failed: source=%s error=%s",
            availability_data_source,
            evaluation_for_selected["error"],
        )
    else:
        logger.info(
            "Consistency check completed: source=%s match_rate=%.1f",
            availability_data_source,
            (
                (evaluation_for_selected.get("total_matched", 0) / evaluation_for_selected.get("total_original_names", 1)) * 100
                if evaluation_for_selected.get("total_original_names", 0) > 0
                else 0
            ),
        )

if evaluation_for_selected is None:
    st.caption("Klicke auf `Konsistenzprüfung ausführen`, um das Prüfergebnis zu berechnen.")

if evaluation_for_selected:
    st.markdown("#### Prüfergebnis")
    if "error" in evaluation_for_selected:
        st.error(f"❌ Evaluation Error: {evaluation_for_selected['error']}")
    else:
        display_name_evaluation(evaluation_for_selected)

        employees_in_availability_not_in_person_info = evaluation_for_selected.get(
            "names_in_availability_not_in_person_info",
            evaluation_for_selected.get("names_in_csv_not_in_person_info", []),
        )
        if evaluation_for_selected.get("unmatched_names") or employees_in_availability_not_in_person_info:
            st.warning("💡 **Recommendations:**")
            if evaluation_for_selected.get("unmatched_names"):
                st.write("• Prüfe Tippfehler bei nicht zugeordneten Namen oder ergänze sie in person_info.")
            if employees_in_availability_not_in_person_info:
                st.write("• Prüfe Mitarbeitende in availability_data, die noch nicht in person_info stehen.")

        match_rate = 0
        if evaluation_for_selected.get("total_original_names", 0) > 0:
            match_rate = evaluation_for_selected.get("total_matched", 0) / evaluation_for_selected.get("total_original_names", 0) * 100

        if match_rate >= 90:
            st.success("🎉 Great! High name matching rate. This file should work well for generating schedules.")
        elif match_rate >= 70:
            st.info("ℹ️ Good name matching rate. You may want to review unmatched names before generating schedules.")
        else:
            st.warning("⚠️ Low name matching rate. Please review and fix name mismatches before generating schedules.")

st.divider()

st.subheader("4) Schichtplan Generator")
st.markdown("Dieser Schritt nutzt `availability_data` + `person_info` sowie den gewählten Zeitraum.")

can_generate = bool(
    has_availability_data
    and evaluation_for_selected
    and "error" not in evaluation_for_selected
    and has_person_info_for_generation
)
if not can_generate:
    if not has_availability_data:
        st.info("Bitte zuerst availability_data laden.")
    elif evaluation_for_selected and "error" in evaluation_for_selected:
        st.warning("Prüfung fehlgeschlagen. Bitte korrigiere die Daten und prüfe erneut.")
    elif not has_person_info_for_generation:
        st.info("Für die Generierung werden vollständige `person_info`-Zeilen mit Name, Location und Task benötigt.")
    else:
        st.info("Bitte zuerst die Konsistenzprüfung abschließen.")

col1, col2 = st.columns(2)
with col1:
    schichtplan_start_date = st.date_input(
        "📅 Start Date", 
        value=first_day_next_month, 
        key="schichtplan_start",
        disabled=not can_generate,
    )
with col2:
    schichtplan_end_date = st.date_input(
        "📅 End Date", 
        value=last_day_next_month, 
        key="schichtplan_end",
        disabled=not can_generate,
    )

# Generate unique session key for this generation
generation_source_key = availability_data_source or selected_source
generation_key = f"{generation_source_key}::{schichtplan_start_date}::{schichtplan_end_date}"

# Generate schichtplan
if can_generate and schichtplan_start_date and schichtplan_end_date:
    if st.button("🔄 Generate Schichtplan Export", width="stretch", key="generate_schichtplan"):
        logger.info(
            "User triggered schichtplan generation: source=%s start=%s end=%s",
            generation_source_key,
            schichtplan_start_date,
            schichtplan_end_date,
        )
        try:
            # Create temp directory for outputs
            temp_dir = SCHICHTPLAN_DATA_DIR / "exports"
            os.makedirs(temp_dir, exist_ok=True)
            
            if availability_data is None or availability_data.empty:
                logger.error("Generation aborted: no availability_data in session")
                st.error("❌ Keine availability_data verfügbar. Bitte Schritt 1 erneut laden.")
            else:
                # Generate schichtplan - now returns tuple (output_files, evaluation)
                with st.spinner("Generating Schichtplan..."):
                    output_files, evaluation = generate_schichtplan(
                        availability_data,
                        schichtplan_start_date.strftime("%Y-%m-%d"),
                        schichtplan_end_date.strftime("%Y-%m-%d"),
                        person_info_for_generation,
                        fixed_schedules=FIXED_SCHEDULES,
                        output_dir=temp_dir,
                    )
                
                # Store results in session state with unique key
                st.session_state[f"generated_files_{generation_key}"] = output_files
                st.session_state[f"generation_evaluation_{generation_key}"] = evaluation
                st.session_state["last_generation_key"] = generation_key
                logger.info(
                    "Schichtplan generation completed: generation_key=%s outputs=%s",
                    generation_key,
                    list(output_files.keys()),
                )
                
                st.success("✅ Schichtplan export generated successfully!")
                st.rerun()  # Rerun to show the persistent results
                
        except Exception as e:
            logger.exception("Schichtplan generation failed")
            st.error(f"❌ An error occurred while generating Schichtplan: {e}")
            st.exception(e)

# Display generated results if they exist in session state
# Check if we have results for current generation key OR the last generation
display_key = None
if generation_key and f"generated_files_{generation_key}" in st.session_state:
    display_key = generation_key
elif "last_generation_key" in st.session_state and f"generated_files_{st.session_state['last_generation_key']}" in st.session_state:
    display_key = st.session_state["last_generation_key"]

if display_key:
    output_files = st.session_state[f"generated_files_{display_key}"]
    evaluation = st.session_state[f"generation_evaluation_{display_key}"]
    
    # Show info about which generation is being displayed
    if display_key != generation_key and generation_key:
        # Extract info from display_key to show what generation this is from.
        if "::" in display_key:
            key_parts = display_key.split("::")
        else:
            key_parts = display_key.split("_")

        if len(key_parts) >= 3:
            prev_source = key_parts[0]
            prev_start = key_parts[1]
            prev_end = key_parts[2]
            st.info(f"ℹ️ Showing results from previous generation (Source: {prev_source}, Dates: {prev_start} to {prev_end}). Generate again to update with current settings.")
        else:
            st.info("ℹ️ Showing results from previous generation. Generate again to update with current settings.")
    elif display_key == generation_key:
        st.success("🎯 Showing results for current settings.")
    
    # Display evaluation results with better formatting
    st.divider()
    if "error" in evaluation:
        st.error(f"❌ Evaluation Error: {evaluation['error']}")
    else:
        display_name_evaluation(evaluation)
    
    # Download buttons
    st.subheader("💾 Download Files")
    download_cols = st.columns(len(output_files))
    for i, (label, path) in enumerate(output_files.items()):
        with download_cols[i]:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    st.download_button(
                        label=f"📥 {label}.csv",
                        data=f,
                        file_name=os.path.basename(path),
                        mime="text/csv",
                        width="stretch",
                        key=f"download_{label}_{display_key}"  # Unique key for each download button
                    )
            else:
                st.error(f"❌ File not found: {label}")
    
    # Show preview of generated files
    st.subheader("👀 File Previews")
    for label, path in output_files.items():
        if os.path.exists(path):
            try:
                df = pd.read_csv(path)
                with st.expander(f"📋 Preview: {label}.csv"):
                    st.dataframe(df, width="stretch")
            except Exception as e:
                st.error(f"❌ Could not preview {label}: {e}")
    
    # Option to clear results
    if st.button("🗑️ Clear Results", width="stretch"):
        logger.info("User triggered clear results for display_key=%s", display_key)
        # Clear all related session state keys
        keys_to_remove = []
        for key in st.session_state.keys():
            if key.startswith(f"generated_files_{display_key}") or key.startswith(f"generation_evaluation_{display_key}"):
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del st.session_state[key]
        
        if "last_generation_key" in st.session_state:
            del st.session_state["last_generation_key"]
        logger.info("Cleared generation result keys. removed=%d", len(keys_to_remove))
        
        st.rerun() 
