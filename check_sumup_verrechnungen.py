import pandas as pd

# CSV-Dateien laden
file_a = "verrechnungskonto.csv"
file_b = "spaka_konto.csv"

df_a = pd.read_csv(file_a, sep=';', dtype=str)
df_b = pd.read_csv(file_b, sep=';', dtype=str)

print(df_a)
print(df_b)
# Beträge und Datumswerte bereinigen (z.B. Dezimaltrennzeichen ändern)
df_a["Betrag"] = df_a["Betrag"].str.replace('.', '').str.replace(',', '.').astype(float)
df_a["Betrag"] = df_a["Betrag"]*(-1)
df_b["Betrag"] = df_b["Betrag"].str.replace('.', '').str.replace(',', '.').astype(float)

# Identische Zahlungen finden
merged = df_a.merge(df_b, on=["Bezahldatum", "Betrag"], how='left', suffixes=('', '_b'))

# Falls es mehrere Treffer gibt, markiere sie
duplicates = merged.duplicated(subset=["Bezahldatum", "Betrag"], keep=False)
merged["Mehrfachübereinstimmung"] = duplicates.map({True: "Ja", False: "Nein"})

# Name und Beschreibung aus Tabelle B übernehmen
merged["Name"] = merged["Name_b"].combine_first(merged["Name"])
merged["Beschreibung"] = merged["Beschreibung_b"].combine_first(merged["Beschreibung"])

# Nicht benötigte Spalten entfernen
merged.drop(columns=["Name_b", "Beschreibung_b"], inplace=True)

# Ergebnis speichern
df_output = "tabelle_a_erweitert.csv"
merged.to_csv(df_output, sep=';', index=False)

print(merged)