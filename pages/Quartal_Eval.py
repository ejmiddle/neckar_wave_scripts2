import streamlit as st
import pandas as pd
import os

def analyze_gift_cards(uploaded_files):
    """Analyze gift card entries and redemptions from uploaded files."""
    total_gift_cards = 0
    total_redemptions = 0
    file_summaries = []
    
    for uploaded_file in uploaded_files:
        try:
            # Read the Excel file - try both sheet engines
            try:
                df = pd.read_excel(uploaded_file, engine='openpyxl')
            except:
                df = pd.read_excel(uploaded_file, engine='xlrd')
            
            # Initialize counters for this file
            file_gift_cards = 0
            file_redemptions = 0
                        
            # Look for the expected structure from the existing analysis
            if 'Produkt' in df.columns:
                # Filter for relevant product names (like in buchhaltung_auswertung.py)
                relevant_products = ["Gift card", "Gift card - Redeem", "Gutschein", "Gutschein - Einlösen"]
                df_filtered = df[df["Produkt"].isin(relevant_products)].copy()
                                
                if len(df_filtered) > 0:
                    # Look for value column
                    value_col = None
                    possible_value_cols = ["Umsatz inkl. Steuer", "Wert", "Value", "Betrag", "Amount", "Preis", "Price"]
                    
                    for col in possible_value_cols:
                        if col in df.columns:
                            value_col = col
                            break
                    
                    if value_col:                        
                        # Convert values to float (handle German decimal format)
                        if df_filtered[value_col].dtype == 'object':
                            df_filtered[value_col] = df_filtered[value_col].astype(str).str.replace(",", ".").astype(float)
                        
                        # Separate gift cards from redemptions
                        gift_card_entries = df_filtered[df_filtered["Produkt"].isin(["Gift card", "Gutschein"])]
                        redemption_entries = df_filtered[df_filtered["Produkt"].isin(["Gift card - Redeem", "Gutschein - Einlösen"])]
                        
                        file_gift_cards = gift_card_entries[value_col].sum()
                        file_redemptions = redemption_entries[value_col].sum()
                        
                        st.write(f"Gift cards: {file_gift_cards:.2f}, Redemptions: {file_redemptions:.2f}")
                    else:
                        st.warning(f"No value column found in {uploaded_file.name}")
                else:
                    st.info(f"No gift card entries found in {uploaded_file.name}")
            else:
                st.warning(f"'Produkt' column not found in {uploaded_file.name}. Available columns: {list(df.columns)}")
            
            # Store file summary
            file_summaries.append({
                'file_name': uploaded_file.name,
                'gift_cards': file_gift_cards,
                'redemptions': file_redemptions
            })
            
            # Add to totals
            total_gift_cards += file_gift_cards
            total_redemptions += file_redemptions
            
        except Exception as e:
            st.error(f"❌ Error processing file {uploaded_file.name}: {e}")
            file_summaries.append({
                'file_name': uploaded_file.name,
                'gift_cards': 0,
                'redemptions': 0,
                'error': str(e)
            })
    
    return total_gift_cards, total_redemptions, file_summaries

# Page title
st.title("📈 Quartal Eval")

# Gutschein section
st.header("🎁 Gutschein")

st.markdown("Upload multiple xlsx files for Gutschein evaluation.")

# File upload section for multiple files
uploaded_files = st.file_uploader(
    "📁 Upload XLSX Files", 
    type=["xlsx"],
    accept_multiple_files=True,
    help="Select multiple XLSX files containing Gutschein data",
    key="gutschein_uploader"
)

# Display uploaded files and analysis
if uploaded_files:
    st.subheader(f"📋 Uploaded Files ({len(uploaded_files)})")
    
    # Perform gift card analysis
    with st.spinner("🔍 Analyzing gift card data..."):
        total_gift_cards, total_redemptions, file_summaries = analyze_gift_cards(uploaded_files)
    
    # Display summary results
    st.subheader("📊 Gift Card Analysis Summary")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("🎁 Total Gift Cards", f"{total_gift_cards:.2f} €")
    with col2:
        st.metric("💳 Total Redemptions", f"{total_redemptions:.2f} €")
    with col3:
        net_value = total_gift_cards - total_redemptions
        st.metric("📈 Net Value", f"{net_value:.2f} €")
    
    # Display breakdown by file
    st.subheader("📋 Breakdown by File")
    
    breakdown_data = []
    for summary in file_summaries:
        if 'error' not in summary:
            breakdown_data.append({
                'File': summary['file_name'],
                'Gift Cards (€)': f"{summary['gift_cards']:.2f}",
                'Redemptions (€)': f"{summary['redemptions']:.2f}",
                'Net (€)': f"{summary['gift_cards'] - summary['redemptions']:.2f}"
            })
        else:
            breakdown_data.append({
                'File': summary['file_name'],
                'Gift Cards (€)': 'Error',
                'Redemptions (€)': 'Error',
                'Net (€)': 'Error'
            })
    
    if breakdown_data:
        breakdown_df = pd.DataFrame(breakdown_data)
        st.dataframe(breakdown_df, width="stretch")
    
else:
    st.info("ℹ️ No files uploaded yet. Select XLSX files to begin gift card analysis.")
