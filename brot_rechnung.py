import pandas as pd

# Excel-Datei laden
df = pd.read_excel("brotmengen_solawi.xlsx")  # Pfad zur Datei anpassen

# Template definieren
template = """Hallo zusammen,

hier die zweite Abrechnung für Brote und Zimtschnecken bei der Solawi.

Seit Anfang Anfang Juli bis Ende August. hattet ihr folgendes abgeholt: 
{brot_liste} + {zimt} Zimtschnecken

In Summe wären das {gesamtpreis} €

Bitte überweist den Betrag auf folgendes Konto DE58 6725 0020 0009 3657 70

Falls es Unklarheiten oder Unstimmigkeiten gibt gerne melden.

Aktuell bieten wir nur noch Barzahlung oder paypal an, wollen den Modus aber gerne mit euch auf ein einfacheres Modell umstellen. Dazu mehr bald :)

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
