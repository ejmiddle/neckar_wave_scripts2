import pandas as pd

# Excel-Datei laden
df = pd.read_excel("brotmengen_solawi.xlsx")  # Pfad zur Datei anpassen

# Template definieren
template = """Hello hello,

heute folgt endlich die Abrechnung für die Brote und Zimtschnecken für die Liste in die ihr eingetragen habt. Entschuldigt, dass es etwas gedauert hat.

Seit Anfang Mai bis zum 26.6. hattet ihr folgendes abgeholt: 
{brot_liste} + {zimt} Zimtschnecken

In Summe wären das {gesamtpreis} €

Bitte überweist den Betrag auf folgendes Konto DE5867250020000936

Falls es Unklarheiten oder Unstimmigkeiten gibt gerne melden.

Und auch sonst immer gerne Feedback so dass wir uns verbessern können. 

Besten Dank und viele Grüße
Euer Südseite Team
"""

# Spaltennamen
brot_sorten = ["Classico", "Rustico", "Sesam", "Vollkorn", "Roggen"]
zimt_spalte = "Zimt"
gesamtpreis_spalte = "Gesamtpreis"

# Für jede Zeile eine Mail erstellen
with open("mails_output.txt", "w", encoding="utf-8") as f:
    for index, row in df.iterrows():
        empfaenger = row["Mail"]
        brot_liste = ", ".join(
            [f"{int(row[brot])}x {brot}" for brot in brot_sorten if row[brot] > 0]
        )
        zimt = int(row[zimt_spalte])
        gesamtpreis = f"{row[gesamtpreis_spalte]:.2f}"

        mail_text = template.format(
            brot_liste=brot_liste,
            zimt=zimt,
            gesamtpreis=gesamtpreis
        )

        f.write(f"--- Mail an: {empfaenger} ---\n{mail_text}\n\n")
