"""
Nutzung: 
- Download der buchhaltungsberichte und Auftragsbericht von den beiden Konten     
- In jeweiligne Ordner kopieren WIE und ALT
"""

import pandas as pd
import os

def eval_location(date_range, location):

    main_folder = "buchhaltungsberichte"
    # --- Load and process tax report ---
    tax_file_path = f"{main_folder}/{location}/buchhaltungsbericht-detaillierter-{date_range}/buchhaltungsbericht-detaillierter-tax-{date_range}.csv"
    df_tax = pd.read_csv(tax_file_path, sep=';')

    cols_to_convert = ["MwSt", "Umsatz inkl. Steuer", "Rückerstattet ( inkl. Steuer)"]
    for col in cols_to_convert:
        df_tax[col] = df_tax[col].str.replace(",", ".").astype(float)

    evaluation_df = df_tax.groupby("MwSt")[["Umsatz inkl. Steuer", "Rückerstattet ( inkl. Steuer)"]].sum().reset_index()
    evaluation_df["Netto Umsatz"] = evaluation_df["Umsatz inkl. Steuer"] - evaluation_df["Rückerstattet ( inkl. Steuer)"]

    # --- Load and process payments report ---
    payments_file_path = f"{main_folder}/{location}/buchhaltungsbericht-detaillierter-{date_range}/buchhaltungsbericht-detaillierter-payments-{date_range}.csv"
    df_payments = pd.read_csv(payments_file_path, sep=';')
    df_payments["Nettosumme"] = df_payments["Nettosumme"].str.replace(",", ".").astype(float)
    payments_summary = df_payments.groupby("Zahlungsart")["Nettosumme"].sum().reset_index()

    # --- Load and process tips report ---
    tips_file_path = f"{main_folder}/{location}/buchhaltungsbericht-detaillierter-{date_range}/buchhaltungsbericht-detaillierter-tips-{date_range}.csv"
    df_tips = pd.read_csv(tips_file_path, sep=';')
    df_tips["Trinkgeld-Betrag"] = df_tips["Trinkgeld-Betrag"].str.replace(",", ".").astype(float)
    tips_total = pd.DataFrame({
        "Gesamtes Trinkgeld": [df_tips["Trinkgeld-Betrag"].sum()]
    })

    # --- Save all evaluations to Excel ---
    output_file = f"{main_folder}/umsatz_eval_{location}_{date_range}.xlsx"
    with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
        evaluation_df.to_excel(writer, sheet_name="umsatz_mwst", index=False)
        payments_summary.to_excel(writer, sheet_name="zahlungsart", index=False)
        tips_total.to_excel(writer, sheet_name="Trinkgeld", index=False)

def eval_gutscheine_location(date_range, location):

    main_folder = "buchhaltungsberichte"
    # --- Evaluate gift cards / vouchers ---
    product_file_path = f"{main_folder}/{location}/Auftragsbericht-{date_range}/Bericht-Aufträge-Produkte-{date_range}.csv"
    df_products = pd.read_csv(product_file_path, sep=';')

    # Filter for relevant product names
    relevant_products = ["Gift card", "Gift card - Redeem", "Gutschein", "Gutschein - Einlösen"]
    df_filtered = df_products[df_products["Produkt"].isin(relevant_products)].copy()

    # Convert Umsatz inkl. Steuer to float
    df_filtered["Umsatz inkl. Steuer"] = df_filtered["Umsatz inkl. Steuer"].str.replace(",", ".").astype(float)

    # Group and sum by Produkt
    product_summary = df_filtered.groupby("Produkt")["Umsatz inkl. Steuer"].sum().reset_index()

    # Save to separate Excel file
    product_output_file = f"{main_folder}/gutschein_eval_{location}_{date_range}.xlsx"
    product_summary.to_excel(product_output_file, sheet_name="gutscheine", index=False)


# Define variables
# date_range = "2025-03-01_2025-03-31"
# date_range = "2025-01-01_2025-03-31" # Q1
# date_range = "2025-01-01_2025-01-31"
date_range = "2025-02-01_2025-02-28"

location = "WIE"
eval_location(date_range, location)
eval_gutscheine_location(date_range, location)

location = "ALT"
eval_location(date_range, location)
eval_gutscheine_location(date_range, location)

