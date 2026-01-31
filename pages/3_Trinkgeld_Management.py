import streamlit as st
import pandas as pd
import os
from io import BytesIO

# Page title
st.title("💰 Trinkgeld Management")

def convert_robust_number(value):
    """Robustly convert various number formats to float.
    
    Handles:
    - European format: 1.234,56 (dot as thousands, comma as decimal)
    - American format: 1,234.56 (comma as thousands, dot as decimal)  
    - Simple comma decimal: 41,60
    - Simple dot decimal: 41.60
    - Numbers with spaces: " 41,60 "
    - Pure integers: 1234
    """
    if pd.isna(value):
        return value
    
    if isinstance(value, (int, float)):
        return float(value)
    
    if isinstance(value, str):
        # Remove leading/trailing whitespace
        value = str(value).strip()
        
        # Handle empty strings
        if not value:
            return pd.NA
            
        # Remove any spaces within the number
        value = value.replace(' ', '')
        
        # Count commas and dots to determine format
        comma_count = value.count(',')
        dot_count = value.count('.')
        
        try:
            # Case 1: No separators - just a number
            if comma_count == 0 and dot_count == 0:
                return float(value)
            
            # Case 2: Only comma - assume European decimal format (41,60)
            elif comma_count == 1 and dot_count == 0:
                return float(value.replace(',', '.'))
            
            # Case 3: Only dot - assume American decimal format (41.60)
            elif comma_count == 0 and dot_count == 1:
                return float(value)
            
            # Case 4: Both comma and dot present - determine which is decimal separator
            elif comma_count > 0 and dot_count > 0:
                # Find positions of last comma and last dot
                last_comma_pos = value.rfind(',')
                last_dot_pos = value.rfind('.')
                
                # The separator that comes last is likely the decimal separator
                if last_comma_pos > last_dot_pos:
                    # European format: 1.234,56
                    # Remove dots (thousands separator) and replace comma with dot
                    cleaned = value.replace('.', '').replace(',', '.')
                    return float(cleaned)
                else:
                    # American format: 1,234.56
                    # Remove commas (thousands separator)
                    cleaned = value.replace(',', '')
                    return float(cleaned)
            
            # Case 5: Multiple commas or dots - try to handle as thousands separators
            elif comma_count > 1 or dot_count > 1:
                # Try American format first (remove commas)
                try:
                    if comma_count > 1 and dot_count <= 1:
                        cleaned = value.replace(',', '')
                        return float(cleaned)
                except ValueError:
                    pass
                
                # Try European format (remove dots, replace comma with dot)
                try:
                    if dot_count > 1 and comma_count <= 1:
                        cleaned = value.replace('.', '').replace(',', '.')
                        return float(cleaned)
                except ValueError:
                    pass
            
            # If all else fails, try direct conversion
            return float(value)
            
        except (ValueError, TypeError):
            return pd.NA
    
    return pd.NA

def validate_excel_file(df):
    """Validate that uploaded Excel file has required columns."""
    required_columns = ['Karte', 'Bar', 'Trinkgeld_sum', 'Person1', 'Person2', 'Person3', 'Person4', 'Person5', 'Person6']
    
    missing_columns = []
    for col in required_columns:
        if col not in df.columns:
            missing_columns.append(col)
    
    if missing_columns:
        st.error(f"❌ Missing required columns: {', '.join(missing_columns)}")
        return False
    
    st.success("✅ Excel file has all required columns!")
    return True

def distribute_trinkgeld(df):
    """Distribute tips (Trinkgeld) among persons based on the original logic."""
    
    # Convert all numeric columns using European number format handling
    numeric_columns = ['Karte', 'Bar', 'Trinkgeld_sum']
    
    for col in numeric_columns:
        if col in df.columns:
            # Apply robust number conversion
            df[col] = df[col].apply(convert_robust_number)
            # Ensure column is consistently float type
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('float64')
    
    # Then filter out invalid entries (after conversion)
    # Filter out NaN values (which includes converted XXX, xxx, and other non-numeric values)
    df_filtered = df[df['Karte'].notna()]
    
    st.info(f"📊 Processing {len(df_filtered)} valid entries (filtered from {len(df)} total)")
    
    # Define the person columns
    person_columns = ['Person1', 'Person2', 'Person3', 'Person4', 'Person5', 'Person6']
    
    # Initialize an empty dictionary to hold the Trinkgeld for each person
    trinkgeld_per_person = {}
    
    # Track processing details
    processed_entries = 0
    total_trinkgeld_distributed = 0
    
    # Iterate over each row to distribute Trinkgeld
    for _, row in df_filtered.iterrows():
        # Get the Trinkgeld value
        trinkgeld = row['Trinkgeld_sum']
        if trinkgeld > 0:
            processed_entries += 1
            total_trinkgeld_distributed += trinkgeld
            
            # Get the list of people who worked (non-NaN values in person columns)
            persons = row[person_columns].dropna().values
            
            if len(persons) > 0:
                # Calculate the amount of Trinkgeld per person
                trinkgeld_per_capita = trinkgeld / len(persons)

                # Distribute the Trinkgeld to each person
                for person in persons:
                    person_name = str(person).strip()  # Clean up any whitespace
                    if person_name in trinkgeld_per_person:
                        trinkgeld_per_person[person_name] += trinkgeld_per_capita
                    else:
                        trinkgeld_per_person[person_name] = trinkgeld_per_capita

    # Convert the result to a DataFrame for better readability
    result_df = pd.DataFrame(list(trinkgeld_per_person.items()), columns=['Person', 'Total Trinkgeld'])
    result_df = result_df.sort_values('Total Trinkgeld', ascending=False)
    
    # Verification check
    total_trinkgeld_sum = df_filtered['Trinkgeld_sum'].sum()
    total_trinkgeld_per_person = result_df['Total Trinkgeld'].sum()
    total_karte_sum = df_filtered['Karte'].sum()
    total_bar_sum = df_filtered['Bar'].sum()
    
    verification_data = {
        'total_entries': len(df),
        'filtered_entries': len(df_filtered), 
        'processed_entries': processed_entries,
        'total_trinkgeld_sum': total_trinkgeld_sum,
        'total_distributed': total_trinkgeld_per_person,
        'difference': abs(total_trinkgeld_sum - total_trinkgeld_per_person),
        'total_karte_sum': total_karte_sum,
        'total_bar_sum': total_bar_sum
    }
    
    return result_df, verification_data

def display_verification_results(verification_data):
    """Display verification results in a clear format."""
    st.subheader("🔍 Verification Results")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Total Entries", verification_data['total_entries'])
        st.metric("Filtered Entries", verification_data['filtered_entries'])
        st.metric("Processed Entries", verification_data['processed_entries'])
    
    with col2:
        st.metric("Total Trinkgeld Sum", f"€{verification_data['total_trinkgeld_sum']:.2f}")
        st.metric("Total Distributed", f"€{verification_data['total_distributed']:.2f}")
        st.metric("Difference", f"€{verification_data['difference']:.2f}")
    
    with col3:
        st.metric("Total Karte Sum", f"€{verification_data['total_karte_sum']:.2f}")
        st.metric("Total Bar Sum", f"€{verification_data['total_bar_sum']:.2f}")
        st.metric("Karte + Bar Total", f"€{verification_data['total_karte_sum'] + verification_data['total_bar_sum']:.2f}")
    
    # Verification status
    if verification_data['difference'] < 0.01:
        st.success("✅ SUCCESS: Sums are equal! Distribution is correct.")
    else:
        st.error("❌ ERROR: Sums are NOT equal! There may be an issue with the distribution.")

def get_available_trinkgeld_files():
    """Get list of available Trinkgeld Excel files."""
    trinkgeld_dir = "Trinkgeld_Tabellen"
    if not os.path.exists(trinkgeld_dir):
        return []
    
    excel_files = [f for f in os.listdir(trinkgeld_dir) 
                   if f.endswith(('.xlsx', '.xls'))]
    return sorted(excel_files)

def create_excel_download(result_df, verification_data):
    """Create Excel file for download with results and verification info."""
    output = BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Write main results
        result_df.to_excel(writer, sheet_name='Trinkgeld_Distribution', index=False)
        
        # Write verification data
        verification_df = pd.DataFrame([verification_data])
        verification_df.to_excel(writer, sheet_name='Verification', index=False)
    
    output.seek(0)
    return output.getvalue()

# Display information about the tool
st.markdown("""
## 📖 How to Use

This tool helps distribute tips (Trinkgeld) fairly among employees based on shift data.

### Expected File Format

Your Excel file should contain the following columns:
- **Karte**: Card/Transaction ID (entries with 'XXX' or empty values will be filtered out) - *supports various number formats*
- **Bar**: Bar transactions - *supports various number formats (European: 1.234,56, American: 1,234.56, simple: 41,60 or 41.60)*
- **Trinkgeld_sum**: Total tip amount for the entry - *supports various number formats*
- **Person1, Person2, Person3, Person4, Person5, Person6**: Names of people who worked during that shift

### Process
1. Upload an Excel file or select from existing files
2. The tool will automatically distribute tips equally among all people listed for each entry
3. Review the results and verification
4. Download the processed results
""")

# File input section
st.header("📁 File Selection")

# Option 1: Upload new file
st.subheader("Upload New File")
uploaded_file = st.file_uploader(
    "📤 Upload Excel File", 
    type=['xlsx', 'xls'],
    help="Upload an Excel file with Trinkgeld distribution data"
)

# Option 2: Select from existing files  
st.subheader("Or Select Existing File")
available_files = get_available_trinkgeld_files()

if available_files:
    selected_file = st.selectbox(
        "📂 Select from Trinkgeld_Tabellen",
        options=[None] + available_files,
        format_func=lambda x: "Choose a file..." if x is None else x
    )
else:
    st.info("No Excel files found in Trinkgeld_Tabellen directory.")
    selected_file = None

# Processing section
if uploaded_file is not None or selected_file is not None:
    st.header("⚙️ Processing")
    
    try:
        # Load the data
        if uploaded_file is not None:
            df = pd.read_excel(uploaded_file, sheet_name='Tabelle1')
            file_name = uploaded_file.name
            st.info(f"📄 Processing uploaded file: {file_name}")
        else:
            file_path = os.path.join("Trinkgeld_Tabellen", selected_file)
            df = pd.read_excel(file_path, sheet_name='Tabelle1')
            file_name = selected_file
            st.info(f"📄 Processing selected file: {file_name}")
        
        # Display basic file info
        st.write(f"**File contains:** {len(df)} rows × {len(df.columns)} columns")
        
        # Validate file format
        if validate_excel_file(df):
            
            # Show data preview with proper data type conversion
            with st.expander("👀 Preview Raw Data", expanded=False):
                # Create a copy for preview and convert problematic columns
                preview_df = df.copy()
                st.dataframe(preview_df, width='stretch')
            
            # Process the data
            if st.button("🚀 Distribute Trinkgeld", width='stretch'):
                with st.spinner("Distributing tips..."):
                    result_df, verification_data = distribute_trinkgeld(df)
                
                # Store results in session state
                st.session_state['trinkgeld_results'] = result_df
                st.session_state['trinkgeld_verification'] = verification_data
                st.session_state['processed_file'] = file_name
                
                st.success("✅ Trinkgeld distribution completed!")

    except Exception as e:
        st.error(f"❌ Error processing file: {str(e)}")
        st.exception(e)

# Results section
if 'trinkgeld_results' in st.session_state and 'trinkgeld_verification' in st.session_state:
    st.header("📊 Results")
    
    result_df = st.session_state['trinkgeld_results']
    verification_data = st.session_state['trinkgeld_verification']
    processed_file = st.session_state.get('processed_file', 'Unknown')
    
    st.write(f"**Results for:** {processed_file}")
    
    # Display verification
    display_verification_results(verification_data)
    
    # Display results table
    st.subheader("💰 Trinkgeld Distribution per Person")
    st.dataframe(
        result_df.style.format({'Total Trinkgeld': '€{:.2f}'}),
        width='stretch'
    )
    
    # Summary statistics
    st.subheader("📈 Summary Statistics")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Total People", len(result_df))
    with col2:
        st.metric("Average per Person", f"€{result_df['Total Trinkgeld'].mean():.2f}")
    with col3:
        st.metric("Highest Individual Total", f"€{result_df['Total Trinkgeld'].max():.2f}")
    
    # Download section
    st.subheader("💾 Download Results")
    
    # Create Excel file for download
    excel_data = create_excel_download(result_df, verification_data)
    
    st.download_button(
        label="📥 Download Excel Results",
        data=excel_data,
        file_name=f"Trinkgeld_Distribution_{processed_file.replace('.xlsx', '')}_Results.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width='stretch'
    )
    
    # Clear results button
    if st.button("🗑️ Clear Results", width='stretch'):
        for key in ['trinkgeld_results', 'trinkgeld_verification', 'processed_file']:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()

