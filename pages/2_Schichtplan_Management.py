import streamlit as st
import pandas as pd
import os
import datetime
from app_files.schichtplan_utils import generate_schichtplan
from app_paths import SCHICHTPLAN_DATA_DIR

# Page title
st.title("👥 Schichtplan Management")

PERSON_INFO_EXCEL_PATH = SCHICHTPLAN_DATA_DIR / "mitarbeiter_info.xlsx"

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


def load_person_info_from_excel():
    """Load person info from Excel file. No internal defaults are used."""
    if os.path.exists(PERSON_INFO_EXCEL_PATH):
        try:
            df = pd.read_excel(PERSON_INFO_EXCEL_PATH)
            required_cols = ["Name", "Location", "Task"]
            if not set(required_cols).issubset(df.columns):
                st.warning(
                    f"Die Datei `{PERSON_INFO_EXCEL_PATH}` enthält nicht die erwarteten Spalten "
                    f"`{', '.join(required_cols)}`. Bitte passe die Excel-Datei entsprechend an."
                )
                return []

            # Keep only the relevant columns and drop completely empty rows
            df = df[required_cols].dropna(how="all")
            return df.to_dict(orient="records")
        except Exception as e:
            st.error(
                f"Fehler beim Laden der Mitarbeiter-Info aus `{PERSON_INFO_EXCEL_PATH}`: {e}. "
                "Bitte prüfe die Datei und lade die Seite neu."
            )
            return []
    else:
        st.error(
            f"Die Datei für die Mitarbeiter-Info `{PERSON_INFO_EXCEL_PATH}` wurde nicht gefunden. "
            "Bitte lege diese Excel-Datei mit den Spalten `Name`, `Location`, `Task` an "
            "und lade die Seite anschließend neu."
        )
        return []

def display_name_evaluation(evaluation):
    """Display comprehensive name evaluation results."""
    if not evaluation:
        return
    
    st.subheader("📊 Name Evaluation Results")
    
    # Create metrics columns
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Names in Upload", evaluation.get("total_original_names", 0))
    with col2:
        st.metric("Successfully Matched", evaluation.get("total_matched", 0))
    with col3:
        st.metric("Person Info Names", evaluation.get("total_person_info_names", 0))
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
    
    # Successfully matched names
    if evaluation.get("successfully_matched_names"):
        with st.expander("✅ Successfully Matched Names", expanded=True):
            for name in evaluation["successfully_matched_names"]:
                st.success(f"• `{name}`")
    
    # Names in CSV but not in person info (potential new employees or typos)
    if evaluation.get("names_in_csv_not_in_person_info"):
        with st.expander("⚠️ Names in Upload Not in Person Info", expanded=False):
            st.warning("These names appear in the uploaded file but are not in your person info list. They might be new employees or contain typos:")
            for name in evaluation["names_in_csv_not_in_person_info"]:
                st.write(f"• `{name}`")
    
    # Names that couldn't be matched (too different)
    if evaluation.get("unmatched_names"):
        with st.expander("❌ Unmatched Names", expanded=False):
            st.error("These names couldn't be matched due to significant differences:")
            for name in evaluation["unmatched_names"]:
                st.write(f"• `{name}`")
    
    # Names in person info but not in CSV
    if evaluation.get("names_in_person_info_not_in_csv"):
        with st.expander("ℹ️ Employees Not in This Upload", expanded=False):
            st.info("These employees are in your person info but not in this upload:")
            for name in evaluation["names_in_person_info_not_in_csv"]:
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

def get_available_schichtplan_files():
    """Get list of available schichtplan files from the availabilities folder."""
    availabilities_dir = SCHICHTPLAN_DATA_DIR / "availabilities"
    if not os.path.exists(availabilities_dir):
        return []
    
    csv_files = [f for f in os.listdir(availabilities_dir) if f.endswith(".csv")]
    return sorted(csv_files)

def save_uploaded_file(uploaded_file):
    """Save uploaded file to availabilities folder with its original name."""
    if uploaded_file is None:
        return None
    
    # Create availabilities directory if it doesn't exist
    availabilities_dir = SCHICHTPLAN_DATA_DIR / "availabilities"
    os.makedirs(availabilities_dir, exist_ok=True)
    
    # Save file with original name
    file_path = availabilities_dir / uploaded_file.name
    with open(file_path, "wb") as f:
        f.write(uploaded_file.read())
    
    return file_path

def quick_format_validation(csv_file_path):
    """Quick validation of timespan format compatibility."""
    try:
        df = pd.read_csv(csv_file_path)
        if 'Wann?' not in df.columns:
            return {'valid': False, 'message': "Missing 'Wann?' column"}
        
        # Test parse 3 unique timespan formats
        from app_files.schichtplan_utils import parse_wann
        unique_spans = df['Wann?'].dropna().unique()[:3]
        
        if len(unique_spans) == 0:
            return {'valid': False, 'message': "No timespan data found"}
        
        parse_errors = 0
        for span in unique_spans:
            try:
                start_time, _ = parse_wann(span)
                if pd.isna(start_time):
                    parse_errors += 1
            except:
                parse_errors += 1
        
        success_rate = ((len(unique_spans) - parse_errors) / len(unique_spans)) * 100
        
        return {
            'valid': success_rate >= 80,
            'message': f"Timespan format validation: {success_rate:.0f}% success rate",
            'details': f"Tested {len(unique_spans)} format(s), {parse_errors} errors"
        }
    except Exception as e:
        return {'valid': False, 'message': f"Format validation error: {str(e)}"}

def perform_upload_evaluation(csv_file_path, person_info):
    """Perform name evaluation analysis on uploaded file."""
    try:
        # Read the uploaded CSV
        df = pd.read_csv(csv_file_path)
        
        if 'Name' not in df.columns:
            return {"error": "CSV file must contain a 'Name' column"}
        
        # Quick format validation
        format_check = quick_format_validation(csv_file_path)
        
        # Get unique names from the uploaded file
        original_unique_names = df['Name'].dropna().str.strip().unique()
        
        # Create name lists from person info
        name_list = [name for name, _, _ in person_info if name]
        person_info_names = set(name_list)
        
        # Import the matching function from schichtplan_utils
        from app_files.schichtplan_utils import match_name
        
        # Perform name matching analysis similar to generate_schichtplan
        unique_names_set = set(original_unique_names)
        
        # Names in uploaded CSV but not in person_info
        names_not_in_person_info = unique_names_set - person_info_names
        
        # Names in person_info but not in uploaded CSV 
        names_not_in_csv = person_info_names - unique_names_set
        
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
            'names_in_csv_not_in_person_info': sorted(list(names_not_in_person_info)),
            'names_in_person_info_not_in_csv': sorted(list(names_not_in_csv)),
            'successfully_matched_names': sorted(list(matched_names)),
            'unmatched_names': sorted(list(unmatched_names)),
            'total_original_names': len(original_unique_names),
            'total_person_info_names': len(person_info_names),
            'total_matched': len(matched_names),
            'format_validation': format_check
        }
        
        return evaluation
        
    except Exception as e:
        return {"error": f"Error analyzing file: {str(e)}"}


# Get next month dates
first_day_next_month, last_day_next_month = get_next_month_dates()

# Load person info from Excel (with fallback)
person_info_default = load_person_info_from_excel()

# Person info editor
st.subheader("📝 Mitarbeiter-Info Editor")
st.markdown(
    "Bearbeite die Liste der Mitarbeiter:innen, deren Location und Aufgabe. "
    "Die Stammdaten werden aus der Excel-Datei "
    f"`{PERSON_INFO_EXCEL_PATH}` eingelesen. "
    "**Dauerhafte Anpassungen solltest du direkt in dieser Excel-Datei vornehmen "
    "und anschließend die Seite neu laden.**"
)

with st.expander("📝 Mitarbeiter-Info Editor", expanded=False):
    st.caption(
        "Hinweis: Änderungen in der Tabelle gelten nur für die aktuelle Sitzung. "
        "Die eigentlichen Stammdaten kommen aus der Excel-Datei "
        f"`{PERSON_INFO_EXCEL_PATH}`."
    )
    person_info_data = st.data_editor(
        person_info_default,
        num_rows="dynamic",
        use_container_width=True,
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

# Schichtplan generator
st.subheader("📅 Schichtplan Generator")
st.markdown("Upload and manage availability files, then generate shift plan exports.")

# File upload section
st.markdown("#### Upload New Availability File")
uploaded_file = st.file_uploader(
    "📁 Upload CSV with Availability", 
    type=["csv"],
    help="CSV file with columns 'Name' and 'Wann?'",
    key="availability_uploader"
)

# Save uploaded file immediately and perform evaluation
if uploaded_file is not None:
    if f"saved_{uploaded_file.name}" not in st.session_state:
        saved_path = save_uploaded_file(uploaded_file)
        if saved_path:
            st.success(f"✅ File '{uploaded_file.name}' saved successfully!")
            st.session_state[f"saved_{uploaded_file.name}"] = True
            
            # Perform automatic evaluation
            st.info("🔍 Analyzing names in uploaded file...")
            
            # Convert person_info_data to the expected format for evaluation
            person_info = []
            for person in person_info_data:
                if person.get("Name") and person.get("Location") and person.get("Task"):
                    person_info.append((
                        person["Name"], 
                        person["Location"], 
                        person["Task"]
                    ))
            
            # Perform evaluation
            evaluation = perform_upload_evaluation(saved_path, person_info)
            
            # Store evaluation in session state
            st.session_state[f"evaluation_{uploaded_file.name}"] = evaluation
            
            # Force refresh of available files by clearing any cache
            if 'available_files' in st.session_state:
                del st.session_state['available_files']

# Display evaluation results if available for uploaded file
if uploaded_file is not None and f"evaluation_{uploaded_file.name}" in st.session_state:
    evaluation = st.session_state[f"evaluation_{uploaded_file.name}"]
    
    if "error" in evaluation:
        st.error(f"❌ Evaluation Error: {evaluation['error']}")
    else:
        display_name_evaluation(evaluation)
        
        # Show recommendations based on evaluation
        if evaluation.get("unmatched_names") or evaluation.get("names_in_csv_not_in_person_info"):
            st.warning("💡 **Recommendations:**")
            if evaluation.get("unmatched_names"):
                st.write("• Check for typos in unmatched names or add them to the Person Info list")
            if evaluation.get("names_in_csv_not_in_person_info"):
                st.write("• Consider adding new employees to the Person Info list if they are valid")
                
        # Show success message if match rate is high
        match_rate = 0
        if evaluation.get("total_original_names", 0) > 0:
            match_rate = evaluation.get("total_matched", 0) / evaluation.get("total_original_names", 0) * 100
        
        if match_rate >= 90:
            st.success("🎉 Great! High name matching rate. This file should work well for generating schedules.")
        elif match_rate >= 70:
            st.info("ℹ️ Good name matching rate. You may want to review unmatched names before generating schedules.")
        else:
            st.warning("⚠️ Low name matching rate. Please review and fix name mismatches before generating schedules.")

st.divider()

# File selection and generation section
st.markdown("#### Select File and Generate Schichtplan")

# Get available files
available_files = get_available_schichtplan_files()

col1, col2, col3 = st.columns(3)

with col1:
    if available_files:
        selected_file = st.selectbox(
            "📂 Select Availability File",
            options=available_files,
            help="Choose from available CSV files with availability data"
        )
        
        # Show evaluation info for selected file if available
        if selected_file and f"evaluation_{selected_file}" in st.session_state:
            eval_data = st.session_state[f"evaluation_{selected_file}"]
            if "error" not in eval_data:
                match_rate = 0
                if eval_data.get("total_original_names", 0) > 0:
                    match_rate = eval_data.get("total_matched", 0) / eval_data.get("total_original_names", 0) * 100
                
                if match_rate >= 90:
                    st.success(f"✅ {match_rate:.1f}% match rate - Ready to generate!")
                elif match_rate >= 70:
                    st.info(f"ℹ️ {match_rate:.1f}% match rate - Good to go")
                else:
                    st.warning(f"⚠️ {match_rate:.1f}% match rate - Review recommended")
    else:
        st.warning("No availability files found. Please upload a CSV file first.")
        selected_file = None

with col2:
    schichtplan_start_date = st.date_input(
        "📅 Start Date", 
        value=first_day_next_month, 
        key="schichtplan_start"
    )

with col3:
    schichtplan_end_date = st.date_input(
        "📅 End Date", 
        value=last_day_next_month, 
        key="schichtplan_end"
    )

# Generate unique session key for this generation
generation_key = f"{selected_file}_{schichtplan_start_date}_{schichtplan_end_date}" if selected_file else None

# Generate schichtplan
if selected_file and schichtplan_start_date and schichtplan_end_date:
    if st.button("🔄 Generate Schichtplan Export", use_container_width=True):
        try:
            # Create temp directory for outputs
            temp_dir = SCHICHTPLAN_DATA_DIR / "exports"
            os.makedirs(temp_dir, exist_ok=True)
            
            # Use the selected file from availabilities folder
            selected_file_path = SCHICHTPLAN_DATA_DIR / "availabilities" / selected_file
            
            # Convert person_info_data to the expected format
            person_info = []
            for person in person_info_data:
                if person.get("Name") and person.get("Location") and person.get("Task"):
                    person_info.append((
                        person["Name"], 
                        person["Location"], 
                        person["Task"]
                    ))
            
            if not person_info:
                st.error("❌ No valid person information found. Please check the Mitarbeiter-Info table.")
            
            # Generate schichtplan - now returns tuple (output_files, evaluation)
            with st.spinner("Generating Schichtplan..."):
                output_files, evaluation = generate_schichtplan(
                    selected_file_path,
                    schichtplan_start_date.strftime("%Y-%m-%d"),
                    schichtplan_end_date.strftime("%Y-%m-%d"),
                    person_info,
                    fixed_schedules=FIXED_SCHEDULES,
                    output_dir=temp_dir,
                )
            
            # Store results in session state with unique key
            st.session_state[f"generated_files_{generation_key}"] = output_files
            st.session_state[f"generation_evaluation_{generation_key}"] = evaluation
            st.session_state["last_generation_key"] = generation_key
            
            st.success("✅ Schichtplan export generated successfully!")
            st.rerun()  # Rerun to show the persistent results
                
        except Exception as e:
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
        # Extract info from display_key to show what generation this is from
        key_parts = display_key.split('_')
        if len(key_parts) >= 3:
            prev_file = key_parts[0]
            prev_start = key_parts[1]  
            prev_end = key_parts[2]
            st.info(f"ℹ️ Showing results from previous generation (File: {prev_file}, Dates: {prev_start} to {prev_end}). Generate again to update with current settings.")
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
                        use_container_width=True,
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
                    st.dataframe(df, use_container_width=True)
            except Exception as e:
                st.error(f"❌ Could not preview {label}: {e}")
    
    # Option to clear results
    if st.button("🗑️ Clear Results", use_container_width=True):
        # Clear all related session state keys
        keys_to_remove = []
        for key in st.session_state.keys():
            if key.startswith(f"generated_files_{display_key}") or key.startswith(f"generation_evaluation_{display_key}"):
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del st.session_state[key]
        
        if "last_generation_key" in st.session_state:
            del st.session_state["last_generation_key"]
        
        st.rerun() 
