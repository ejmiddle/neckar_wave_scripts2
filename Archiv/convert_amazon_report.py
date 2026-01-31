import pandas as pd

# Step 1: Read the CSV file
csv_file_path = 'amazon/bestellungen_von_der_20241101_zu_20241130_20241206_1026.csv'  # Replace with your CSV file path
df = pd.read_csv(csv_file_path, decimal=',')

# Step 2: Select specific columns
selected_columns = ['Bestelldatum', 
                    'Bestellnummer',
                    'Bestellmenge', 
                    'Zahlungsreferenznummer',
                    'Zwischensumme',
                    'Versandkosten für Artikel',
                    'Zahlungsdatum',
                    'Zahlungsbetrag',
                    'Versandkosten für Artikel',
                    'Umsatzsteuersatz für die Artikelzwischensumme',
                    'Titel',
                    'Amazon-interne Produktkategorie',
                    'Segment',
                    ]  # Replace with your column names
df['Umsatzsteuersatz für die Artikelzwischensumme'] = df['Umsatzsteuersatz für die Artikelzwischensumme'].str.replace('%', '', regex=False).astype(float) / 100

df_selected = df[selected_columns]
df_zahlungen = df_selected.drop_duplicates(subset='Zahlungsreferenznummer', keep='first')

# Step 3: Export to an Excel file
excel_file_path = 'amazon/output_file.xlsx'  # Replace with your desired Excel file path
df_selected.to_excel(excel_file_path, index=False)

excel_file_path = 'amazon/output_file_zahlungen.xlsx'  # Replace with your desired Excel file path
df_zahlungen.to_excel(excel_file_path, index=False)

print(f"Data successfully exported to {excel_file_path}")
