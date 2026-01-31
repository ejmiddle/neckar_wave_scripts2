import pandas as pd
import os
import matplotlib.pyplot as plt

filename = "report_WIE_Jan/Bericht-Aufträge-Produkte-2025-01-01_2025-01-31.csv" 

if os.path.exists(filename):
    print("File exists")
else:
    print("File does not exist")

# CSV-Datei einlesen mit spezifischen Datentypen
df = pd.read_csv(
    filename,
    sep=";",
    dtype={"Produkt": str},
    parse_dates=["Eröffnungsdatum"]
)

df["Stückpreis inkl. MwSt"] = df["Stückpreis inkl. MwSt"].str.replace(",", ".").astype(float)
df["Umsatz inkl. Steuer"] = df["Umsatz inkl. Steuer"].str.replace(",", ".").astype(float)
df["day"] = df["Eröffnungsdatum"].dt.date  # Extracts date without time
df["hour"] = df["Eröffnungsdatum"].dt.hour  # Extracts hour as an integer

df.rename(columns={"Stückpreis inkl. MwSt": "Preis_unit"}, inplace=True)
df.rename(columns={"Umsatz inkl. Steuer": "Umsatz"}, inplace=True)

# DataFrame anzeigen
print(df.info())  # Zeigt die Datentypen an
print(df.head())  # Zeigt die ersten 5 Zeilen an


# # Compute Umsatz per day and hour
# umsatz_per_day_hour = df.groupby(["day", "hour"])["Umsatz"].sum().reset_index()
# # Group by day to get total Umsatz per day
# umsatz_per_day = umsatz_per_day_hour.groupby("day")["Umsatz"].sum()

# # Plot
# plt.figure(figsize=(10, 5))
# plt.plot(umsatz_per_day.index, umsatz_per_day.values, marker="o", linestyle="-")
# plt.xlabel("Day")
# plt.ylabel("Total Umsatz (€)")
# plt.title("Umsatz per Day")
# plt.xticks(rotation=45)
# plt.grid()
# plt.show()


# # Ensure proper week grouping (Monday–Sunday)
# df["Week_Start"] = df["Eröffnungsdatum"] - pd.to_timedelta(df["Eröffnungsdatum"].dt.weekday, unit="D")

# # Compute weekly Umsatz average
# weekly_umsatz = df.groupby("Week_Start")["Umsatz"].mean().reset_index()

# # Plot weekly Umsatz
# plt.figure(figsize=(10, 5))
# plt.plot(weekly_umsatz["Week_Start"], weekly_umsatz["Umsatz"], marker="o", linestyle="-", color="b")

# # Labels and Titles
# plt.xlabel("Week Start Date (Monday)")
# plt.ylabel("Average Weekly Umsatz (€)")
# plt.title("Average Weekly Umsatz Over Time")
# plt.xticks(rotation=45)
# plt.grid()

# # Show Plot
# plt.show()

# # Print unique product names
# unique_products = df["Produkt"].unique()
# # Display the list
# print(unique_products)

# import spacy

# # Load a language model (make sure spaCy is installed: pip install spacy)
# nlp = spacy.load("de_core_news_md")  # or use 'de_core_news_md' for German

# # Define reference words for each category
# category_vectors = {
#     "Brot": nlp("Rustico Sesam/ Walnuss Feige Dinkelkasten Saaten/ Dinkel - halb Ciabatta Classico Gross Classico S Saaten Kürbiskern"),
#     "Pastry": nlp("Kardamomknoten Christmas Knoten Dress T-Shirt Zimtknoten"),
#     "Snacks": nlp("Sandwich Vegan Sandwich Bergkäse/Ziebel Sandwich Camembert/Feige Focaccia gedeck Brot & Butter Oliven Focaccia Belag"),
#     "Espresso Drinks": nlp("Flatwhite Latte Chai Latte Americano Cortado Espresso Pour Over"),
#     "Other Drinks": nlp("Babyccino Limonaden HOT choc VIVA CON AGUA Tee Matcha"),
#     "Coffee Beans": nlp("Ayele Begshaw Yemiru 250g Jonathan Gasca 250g ALMEIDA BARRETO (250G) Yenni Esperanza – No Caf 250g MIO (250G)"),
#     "Other": nlp("Gutschein - Einlösen Gutschein"),
# }
# #  '' '' '' '' ''
# #  '' '' ''
# #  '' '' 'Classico groß 1/2' 'Brötchen' 'Extra Shot'
# #  'Schnecke' 'Buchtel ohne' 'Roggebox' 'Las Flores (200g)' 'MIO (1KG)'
# #  'Kleimer Shot' 'Roggebox - halb' 'Varianten' 'Classico' 'Kasten'
# #  'Knotten' 'Sandwich' 'Fobatta' 'Testback' 'Testbrot' 'Croissant' 'Pain'
# #  'Pain Chocolat' 'Test' 'Testgeback' 'Test Gutschein' 'Ana Luiza 250g'
# #  'Dirty Chai' 'Zitrone Kuchen' 'Pastel de Nata' 'Brot Butter Käse'
# #  'Buchtel' 'Dirty choc' 'Choco Brötchen' 'Pain au chocolat']

# # Function to find the best category match based on similarity
# def categorize_product_spacy(product):
#     product_vector = nlp(product)
#     best_category = max(category_vectors, key=lambda cat: product_vector.similarity(category_vectors[cat]))
#     return best_category

# # Apply categorization
# df["Produkt_Gruppe"] = df["Produkt"].apply(categorize_product_spacy)
# # Display result
# print(df[["Produkt", "Produkt_Gruppe"]])

# Load mapping.csv
mapping_df = pd.read_csv(
    "mapping.csv",
    sep=";",
)  # Replace with the actual file path
mapping_df = mapping_df[["Produkt", "Produkttyp"]]

print(mapping_df)

# Perform the join (merge) on the "Produkt" column
df = df.merge(mapping_df, on="Produkt", how="left")

# Group by 'day' and 'Produkttyp' and calculate total Umsatz
umsatz_per_day_typ = df.groupby(["day", "Produkttyp"])["Umsatz"].sum().reset_index()

# Create a pivot table for easier plotting (day as index, Produkttyp as columns)
pivot_table = umsatz_per_day_typ.pivot(index="day", columns="Produkttyp", values="Umsatz")

# Plot a grouped bar chart
pivot_table.plot(kind="bar", figsize=(12, 6))

# Add labels and title
plt.xlabel("Day")
plt.ylabel("Total Umsatz (€)")
plt.title("Umsatz per Day and Produkttyp")
plt.legend(title="Produkttyp")
plt.xticks(rotation=45)
plt.grid(axis="y")

# Show the plot
plt.tight_layout()
plt.show()