import os

import pandas as pd
import streamlit as st

from buchhaltung_auswertung import eval_gutscheine_location, eval_location
from src.app_paths import BUCHHALTUNG_DIR

# Page title
st.title("📊 Buchhaltung")
base_path = BUCHHALTUNG_DIR


def validate_folder_structure(date_range: str, location: str) -> bool:
    """Validate that required folders exist for the given parameters."""
    detailed_folder = f"{base_path}/{location}/buchhaltungsbericht-detaillierter-{date_range}/"
    orders_folder = f"{base_path}/{location}/Auftragsbericht-{date_range}/"

    if not os.path.exists(detailed_folder):
        st.error(f"❌ Missing folder: {detailed_folder}")
        return False

    if not os.path.exists(orders_folder):
        st.error(f"❌ Missing folder: {orders_folder}")
        return False

    st.success(f"✅ Required folders exist for {location} - {date_range}")
    return True

def run_evaluation(eval_func, func_name: str, date_range: str, location: str):
    """Run evaluation function with proper error handling."""
    if not validate_folder_structure(date_range, location):
        return
    
    try:
        with st.spinner(f"Running {func_name}..."):
            eval_func(date_range, location)
        st.success(f"✅ {func_name} for '{location}' and '{date_range}' completed successfully!")
        
        # Show output file if it exists
        if func_name == "Location Evaluation":
            output_file = f"{base_path}/{location}/output_file_{location}_{date_range}.xlsx"
            if os.path.exists(output_file):
                with open(output_file, "rb") as f:
                    st.download_button(
                        label=f"📥 Download {location} Evaluation",
                        data=f,
                        file_name=f"output_file_{location}_{date_range}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
        elif func_name == "Gutscheine Evaluation":
            output_file = f"{base_path}/{location}/output_file_zahlungen_{location}_{date_range}.xlsx"
            if os.path.exists(output_file):
                with open(output_file, "rb") as f:
                    st.download_button(
                        label=f"📥 Download Gutscheine Evaluation",
                        data=f,
                        file_name=f"output_file_zahlungen_{location}_{date_range}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                    
    except Exception as e:
        st.error(f"❌ An error occurred during {func_name}: {e}")
        st.exception(e)

def get_last_month_date_range():
    """Calculate the date range for the last month relative to today."""
    import datetime
    
    today = datetime.date.today()
    
    # Calculate last month's date range
    if today.month == 1:
        last_month_year = today.year - 1
        last_month = 12
    else:
        last_month_year = today.year
        last_month = today.month - 1
    
    first_day_last_month = datetime.date(last_month_year, last_month, 1)
    if last_month == 12:
        first_day_next_month_after_last = datetime.date(last_month_year + 1, 1, 1)
    else:
        first_day_next_month_after_last = datetime.date(last_month_year, last_month + 1, 1)
    last_day_last_month = first_day_next_month_after_last - datetime.timedelta(days=1)
    
    # Format last month's date range as string
    return f"{first_day_last_month.strftime('%Y-%m-%d')}_{last_day_last_month.strftime('%Y-%m-%d')}"

# Get date ranges
last_month_date_range = get_last_month_date_range()

# Input section
st.header("🔧 Input Parameters")

col1, col2 = st.columns(2)

with col1:
    date_range = st.text_input(
        "📅 Date Range", 
        value=last_month_date_range,
        help="Format: YYYY-MM-DD_YYYY-MM-DD"
    )

with col2:
    location = st.selectbox(
        "📍 Location", 
        options=["WIE", "ALT"], 
        index=0,
        help="Select the location to analyze"
    )

# Actions section
st.header("🚀 Actions")

col1, col2 = st.columns(2)

with col1:
    if st.button("📊 Evaluate Location", use_container_width=True):
        run_evaluation(eval_location, "Location Evaluation", date_range, location)

with col2:
    if st.button("🎁 Evaluate Gutscheine", use_container_width=True):
        run_evaluation(eval_gutscheine_location, "Gutscheine Evaluation", date_range, location)

# Results Display Section
st.header("📊 Major Results")

# Check for existing results files
location_eval_file = f"{base_path}/umsatz_eval_{location}_{date_range}.xlsx"
gutscheine_eval_file = f"{base_path}/gutschein_eval_{location}_{date_range}.xlsx"

if os.path.exists(location_eval_file):
    st.subheader(f"📍 {location} - Location Evaluation Results")
    
    try:
        # Read the Excel file with all sheets
        with pd.ExcelFile(location_eval_file) as xls:
            # Display Umsatz MwSt
            if 'umsatz_mwst' in xls.sheet_names:
                df_umsatz = pd.read_excel(xls, sheet_name='umsatz_mwst')
                st.write("**Sales by VAT Rate:**")
                st.dataframe(df_umsatz, use_container_width=True)
            
            # Display Zahlungsart
            if 'zahlungsart' in xls.sheet_names:
                df_zahlung = pd.read_excel(xls, sheet_name='zahlungsart')
                st.write("**Payment Methods:**")
                st.dataframe(df_zahlung, use_container_width=True)
            
            # Display Trinkgeld
            if 'Trinkgeld' in xls.sheet_names:
                df_tips = pd.read_excel(xls, sheet_name='Trinkgeld')
                st.write("**Total Tips:**")
                st.dataframe(df_tips, use_container_width=True)
                
    except Exception as e:
        st.error(f"Error reading location evaluation file: {e}")

if os.path.exists(gutscheine_eval_file):
    st.subheader(f"🎁 {location} - Gift Card/Voucher Results")
    
    try:
        # Read the Excel file
        df_gutscheine = pd.read_excel(gutscheine_eval_file, sheet_name='gutscheine')
        st.write("**Gift Card/Voucher Summary:**")
        st.dataframe(df_gutscheine, use_container_width=True)
        
    except Exception as e:
        st.error(f"Error reading gutscheine evaluation file: {e}")

if not os.path.exists(location_eval_file) and not os.path.exists(gutscheine_eval_file):
    st.info("ℹ️ No results found for the selected date range and location. Run the evaluations above to generate results.")
