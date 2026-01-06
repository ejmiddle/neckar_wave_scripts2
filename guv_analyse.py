import pandas as pd
import numpy as np
from datetime import datetime
import os
import matplotlib.pyplot as plt
import seaborn as sns

def read_guv_data(file_path):
    """Liest GuV-Daten aus einer CSV-Datei ein und extrahiert relevante Kennzahlen"""
    try:
        df = pd.read_csv(file_path, delimiter=';', header=None, names=['Kennzahl', 'Wert'], encoding='utf-8')
        
        def convert_german_number(value):
            if pd.isna(value) or value == '':
                return 0.0
            try:
                return float(str(value).replace(',', '.'))
            except:
                return 0.0
        
        # Rohdaten extrahieren
        raw_data = {}
        raw_data['Zeitraum'] = df.iloc[2]['Wert'] if len(df) > 2 else ""
        
        for idx, row in df.iterrows():
            kennzahl = str(row['Kennzahl']).strip().lower()
            wert = convert_german_number(row['Wert'])
            raw_data[kennzahl] = wert
            
        return raw_data
        
    except Exception as e:
        print(f"❌ Fehler beim Lesen der Datei {file_path}: {e}")
        return None

def transform_guv_data(raw_data):
    """Transformiert Rohdaten in strukturierte GuV-Kennzahlen"""
    if not raw_data:
        return None
    
    # Hauptkennzahlen
    data = {
        'Zeitraum': raw_data.get('Zeitraum', ''),
        'Gewinn': raw_data.get('gewinn', 0),
        'Betriebseinnahmen': raw_data.get('summe betriebseinnahmen', 0),
        'Betriebsausgaben': raw_data.get('summe betriebsausgaben', 0),
        'Direkte_Kosten': raw_data.get('direkte kosten', 0),
        'Indirekte_Kosten': raw_data.get('indirekte kosten', 0),
        'Abschreibungen': raw_data.get('abschreibungen', 0)
    }
    
    # Personalkosten zusammenrechnen
    data['Personalkosten'] = sum([
        raw_data.get('lohn / gehalt', 0),
        raw_data.get('gehälter', 0),
        raw_data.get('krankenkasse', 0),
        raw_data.get('pauschale steuer für aushilfen', 0),
        raw_data.get('fortbildung / weiterbildung', 0)
    ])
    
    # Waren & Material
    data['Waren_Material'] = sum([
        raw_data.get('wareneinkauf', 0),
        raw_data.get('materialeinkauf', 0)
    ])
    
    # Sonstige Kategorien
    data['Sonstige_Direkte'] = raw_data.get('verrechnungskonto gutscheine', 0)
    data['Miete_Pacht'] = raw_data.get('miete / pacht', 0)
    data['Sonstige_Indirekte'] = data['Indirekte_Kosten'] - data['Miete_Pacht']
    
    return data

def load_all_quarters():
    """Lädt alle Quartalsdaten"""
    quartale_dateien = {
        'Q4-24': 'guv/Q4-24_Gewinn_und_Verlust_Übersicht.csv',
        'Q1-25': 'guv/Q1-25_Gewinn_und_Verlust_Übersicht.csv',
        'Q2-25': 'guv/Q2-25_Gewinn_und_Verlust_Übersicht.csv'
    }
    
    quartals_daten = {}
    
    print("📂 SCHRITT 1: DATEN EINLESEN")
    print("-" * 40)
    
    for quartal, datei in quartale_dateien.items():
        if os.path.exists(datei):
            raw_data = read_guv_data(datei)
            if raw_data:
                quartals_daten[quartal] = raw_data
                print(f"✅ {quartal}: {len(raw_data)} Kennzahlen geladen")
            else:
                print(f"❌ {quartal}: Fehler beim Laden")
        else:
            print(f"❌ {quartal}: Datei nicht gefunden")
    
    print(f"\n📊 Ergebnis: {len(quartals_daten)} Quartale erfolgreich geladen\n")
    return quartals_daten

def transform_all_quarters(quartals_raw_data):
    """Transformiert alle Quartalsdaten"""
    print("🔄 SCHRITT 2: DATEN TRANSFORMIEREN")
    print("-" * 40)
    
    data_rows = []
    
    for quartal in ['Q4-24', 'Q1-25', 'Q2-25']:
        if quartal in quartals_raw_data:
            transformed = transform_guv_data(quartals_raw_data[quartal])
            if transformed:
                row = {'Quartal': quartal, **transformed}
                data_rows.append(row)
                print(f"✅ {quartal}: Transformiert - Gewinn: {transformed['Gewinn']:,.0f}€")
            else:
                print(f"❌ {quartal}: Transformation fehlgeschlagen")
    
    print(f"\n📊 Ergebnis: {len(data_rows)} Quartale transformiert\n")
    return pd.DataFrame(data_rows)

"""Hauptfunktion"""
print("=" * 60)
print("🏢 NECKAR WAVE FOODS - GuV ANALYSE")
print("=" * 60)

# Schritt 1: Daten einlesen
quartals_raw_data = load_all_quarters()
# print(quartals_raw_data)
if not quartals_raw_data:
    print("❌ Keine Daten geladen. Beende...")

# Schritt 2: Daten transformieren
df = transform_all_quarters(quartals_raw_data)

# Schritt 3: Ergebnisse anzeigen
print("📈 SCHRITT 3: FINALE ERGEBNISSE")
print("-" * 40)

if not df.empty:
    print(f"✅ DataFrame erstellt mit {len(df)} Quartalen")
    
    # Spalten in hierarchischer Reihenfolge sortieren
    hierarchical_columns = [
        'Quartal',
        'Zeitraum', 
        # 1. Gewinn (Ergebnis)
        'Gewinn',
        # 2. Einnahmen  
        'Betriebseinnahmen',
        # 3. Ausgaben Gesamt
        'Betriebsausgaben',
        # 4. Aufschlüsselung Ausgaben
        'Direkte_Kosten',
        'Personalkosten',
        'Waren_Material', 
        'Sonstige_Direkte',
        'Indirekte_Kosten',
        'Miete_Pacht',
        'Sonstige_Indirekte',
        'Abschreibungen'
    ]
    
    # DataFrame mit hierarchischer Spaltenreihenfolge
    df_hierarchical = df[hierarchical_columns]
    
    # Berechne zusätzliche Kennzahlen
    if not df.empty and len(df) > 0:
        df['Gewinnmarge_%'] = (df['Gewinn'] / df['Betriebseinnahmen']) * 100
        df['Direkte_Kosten_%'] = (df['Direkte_Kosten'] / df['Betriebseinnahmen']) * 100
        df['Indirekte_Kosten_%'] = (df['Indirekte_Kosten'] / df['Betriebseinnahmen']) * 100
        df['Abschreibungen_%'] = (df['Abschreibungen'] / df['Betriebseinnahmen']) * 100
        df['Personalkosten_%'] = (df['Personalkosten'] / df['Betriebseinnahmen']) * 100
        df['Waren_Material_%'] = (df['Waren_Material'] / df['Betriebseinnahmen']) * 100
        df['Sonstige_Direkte_%'] = (df['Sonstige_Direkte'] / df['Betriebseinnahmen']) * 100
        df['Miete_Pacht_%'] = (df['Miete_Pacht'] / df['Betriebseinnahmen']) * 100
        df['Sonstige_Indirekte_%'] = (df['Sonstige_Indirekte'] / df['Betriebseinnahmen']) * 100

    # Excel Export vorbereiten
    filename = f"Neckar_Wave_GuV_Analyse_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

    # Haupttabelle: Quartale als Spalten, Kennzahlen als Zeilen
    haupttabelle_data = {
        'Kennzahl': [
            'Betriebseinnahmen (€)',
            'Betriebsausgaben (€)',
            'Gewinn/Verlust (€)',
            'Gewinnmarge (%)',
            '',  # Leerzeile
            'KOSTENAUFSCHLÜSSELUNG - ABSOLUT (€)',
            'Direkte Kosten gesamt',
            '  → Personalkosten (Lohn + Krankenkasse + Steuern + Fortbildung)',
            '  → Waren und Materialeinkauf',
            '  → Sonstige direkte Kosten (z.B. Gutscheine)',
            'Indirekte Kosten gesamt',
            '  → Miete und Pacht',
            '  → Sonstige indirekte Kosten',
            'Abschreibungen',
            '',  # Leerzeile
            'KOSTENAUFSCHLÜSSELUNG - PROZENT (%)',
            'Direkte Kosten (%)',
            '  → Personalkosten (%)',
            '  → Waren und Materialeinkauf (%)',
            '  → Sonstige direkte Kosten (%)',
            'Indirekte Kosten (%)',
            '  → Miete und Pacht (%)',
            '  → Sonstige indirekte Kosten (%)',
            'Abschreibungen (%)'
        ]
    }

    # Daten für jedes Quartal hinzufügen
    for i, row in df.iterrows():
        haupttabelle_data[f'{row["Quartal"]}'] = [
            row['Betriebseinnahmen'],
            row['Betriebsausgaben'],
            row['Gewinn'],
            round(row['Gewinnmarge_%'], 1),
            '',  # Leerzeile
            '',  # Überschrift
            row['Direkte_Kosten'],
            row['Personalkosten'],
            row['Waren_Material'],
            row['Sonstige_Direkte'],
            row['Indirekte_Kosten'],
            row['Miete_Pacht'],
            row['Sonstige_Indirekte'],
            row['Abschreibungen'],
            '',  # Leerzeile
            '',  # Überschrift
            round(row['Direkte_Kosten_%'], 1),
            round(row['Personalkosten_%'], 1),
            round(row['Waren_Material_%'], 1),
            round(row['Sonstige_Direkte_%'], 1),
            round(row['Indirekte_Kosten_%'], 1),
            round(row['Miete_Pacht_%'], 1),
            round(row['Sonstige_Indirekte_%'], 1),
            round(row['Abschreibungen_%'], 1)
        ]

    haupttabelle_df = pd.DataFrame(haupttabelle_data)

    # Entwicklungsanalyse: Quartale als Spalten (nur wenn genug Daten)
    entwicklung_data = {'Kennzahl': []}
    for i, row in df.iterrows():
        entwicklung_data[f'{row["Quartal"]}'] = []

    if len(df) >= 2:
        entwicklung_data['Kennzahl'].extend([
            'Umsatzwachstum zum Vorquartal (%)',
            'Gewinnveränderung zum Vorquartal (€)',
            'Gewinnmargenveränderung zum Vorquartal (%)',
            'Kostenquote Personalkosten (%)',
            'Kostenquote Waren/Material (%)',
            'Kostenquote Miete (%)',
            'Gesamtkosten pro Euro Umsatz (€)'
        ])
        
        for i, row in df.iterrows():
            quartal = row['Quartal']
            if i == 0:  # Erstes Quartal
                entwicklung_data[quartal].extend([
                    '-',  # Kein Vorquartal
                    '-',
                    '-',
                    round(row['Personalkosten_%'], 1),
                    round(row['Waren_Material_%'], 1),
                    round(row['Miete_Pacht_%'], 1),
                    round(row['Betriebsausgaben'] / row['Betriebseinnahmen'], 2)
                ])
            else:  # Folgende Quartale
                prev_row = df.iloc[i-1]
                umsatz_wachstum = ((row['Betriebseinnahmen'] - prev_row['Betriebseinnahmen']) / prev_row['Betriebseinnahmen']) * 100
                gewinn_veraenderung = row['Gewinn'] - prev_row['Gewinn']
                marge_veraenderung = row['Gewinnmarge_%'] - prev_row['Gewinnmarge_%']
                
                entwicklung_data[quartal].extend([
                    f"{umsatz_wachstum:+.1f}%",
                    f"{gewinn_veraenderung:+,.0f}€",
                    f"{marge_veraenderung:+.1f}%",
                    round(row['Personalkosten_%'], 1),
                    round(row['Waren_Material_%'], 1),
                    round(row['Miete_Pacht_%'], 1),
                    round(row['Betriebsausgaben'] / row['Betriebseinnahmen'], 2)
                ])

    entwicklung_df = pd.DataFrame(entwicklung_data)

    # Zusammenfassung erstellen
    zusammenfassung_data = {'Kategorie': [], 'Beschreibung': []}

    if len(df) >= 3:  # Nur wenn alle 3 Quartale verfügbar
        zusammenfassung_data['Kategorie'].extend(['Positive Entwicklungen'] * 4 + ['Aufmerksamkeitspunkte'] * 2)
        zusammenfassung_data['Beschreibung'].extend([
            f'Turnaround von {df.loc[0, "Gewinn"]:,.0f}€ Verlust zu {df.loc[2, "Gewinn"]:,.0f}€ Gewinn',
            f'Umsatzsteigerung von {df.loc[0, "Betriebseinnahmen"]:,.0f}€ auf {df.loc[2, "Betriebseinnahmen"]:,.0f}€ (+{((df.loc[2, "Betriebseinnahmen"]/df.loc[0, "Betriebseinnahmen"]-1)*100):.1f}%)',
            f'Gewinnmarge verbessert von {df.loc[0, "Gewinnmarge_%"]:.1f}% auf {df.loc[2, "Gewinnmarge_%"]:.1f}%',
            f'Personalkosten als % stabil gehalten ({df.loc[0, "Personalkosten_%"]:.1f}% → {df.loc[2, "Personalkosten_%"]:.1f}%)',
            f'Waren/Material-Kosten sind gestiegen ({df.loc[2, "Waren_Material_%"]:.1f}% der Einnahmen)',
            f'Absolute Betriebsausgaben sind in Q2-25 höher als in Q1-25'
        ])

    zusammenfassung_df = pd.DataFrame(zusammenfassung_data)

    # Spezielle Kostenkategorien-Tabelle
    kostenstruktur_data = {
        'Kostenkategorie': [
            'DIREKTE KOSTEN',
            'Personalkosten (Lohn + KK + Steuern + Fortbildung)',
            'Waren und Materialeinkauf',
            'Sonstige direkte Kosten (z.B. Gutscheine)',
            '',
            'INDIREKTE KOSTEN', 
            'Miete und Pacht',
            'Sonstige indirekte Kosten',
            '',
            'WEITERE POSITIONEN',
            'Abschreibungen'
        ]
    }

    for i, row in df.iterrows():
        quartal = row['Quartal']
        kostenstruktur_data[f'{quartal} (€)'] = [
            f'{row["Direkte_Kosten"]:,.2f}',
            f'{row["Personalkosten"]:,.2f}',
            f'{row["Waren_Material"]:,.2f}',
            f'{row["Sonstige_Direkte"]:,.2f}',
            '',
            f'{row["Indirekte_Kosten"]:,.2f}',
            f'{row["Miete_Pacht"]:,.2f}',
            f'{row["Sonstige_Indirekte"]:,.2f}',
            '',
            '',
            f'{row["Abschreibungen"]:,.2f}'
        ]
        
        kostenstruktur_data[f'{quartal} (%)'] = [
            f'{row["Direkte_Kosten_%"]:.1f}%',
            f'{row["Personalkosten_%"]:.1f}%',
            f'{row["Waren_Material_%"]:.1f}%',
            f'{row["Sonstige_Direkte_%"]:.1f}%',
            '',
            f'{row["Indirekte_Kosten_%"]:.1f}%',
            f'{row["Miete_Pacht_%"]:.1f}%',
            f'{row["Sonstige_Indirekte_%"]:.1f}%',
            '',
            '',
            f'{row["Abschreibungen_%"]:.1f}%'
        ]

    kostenstruktur_df = pd.DataFrame(kostenstruktur_data)

    # Output-Ordner erstellen
    output_dir = "guv/outputs"
    os.makedirs(output_dir, exist_ok=True)
    
    # Excel-Datei mit nur der Hauptübersicht erstellen
    filename = os.path.join(output_dir, f"Neckar_Wave_GuV_Analyse_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx")
    try:
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            # Nur Sheet 1: Hauptübersicht (Quartale als Spalten)
            haupttabelle_df.to_excel(writer, sheet_name='Hauptübersicht', index=False)

        print(f"✅ Excel-Datei erfolgreich erstellt: {filename}")
        
    except Exception as e:
        print(f"❌ Fehler beim Erstellen der Excel-Datei: {e}")

    # ZUSAMMENFASSUNG als Print-Output
    print("\n" + "="*80)
    print("📊 ZUSAMMENFASSUNG DER GUV-ANALYSE")
    print("="*80)
    
    if len(df) >= 3:  # Nur wenn alle 3 Quartale verfügbar
        print("\n🟢 POSITIVE ENTWICKLUNGEN:")
        print("-" * 50)
        print(f"   • Turnaround von {df.loc[0, 'Gewinn']:,.0f}€ Verlust zu {df.loc[2, 'Gewinn']:,.0f}€ Gewinn")
        print(f"   • Umsatzsteigerung von {df.loc[0, 'Betriebseinnahmen']:,.0f}€ auf {df.loc[2, 'Betriebseinnahmen']:,.0f}€ (+{((df.loc[2, 'Betriebseinnahmen']/df.loc[0, 'Betriebseinnahmen']-1)*100):.1f}%)")
        print(f"   • Gewinnmarge verbessert von {df.loc[0, 'Gewinnmarge_%']:.1f}% auf {df.loc[2, 'Gewinnmarge_%']:.1f}%")
        print(f"   • Personalkosten als % stabil gehalten ({df.loc[0, 'Personalkosten_%']:.1f}% → {df.loc[2, 'Personalkosten_%']:.1f}%)")
        
        print("\n🟡 AUFMERKSAMKEITSPUNKTE:")
        print("-" * 50)
        print(f"   • Waren/Material-Kosten sind gestiegen ({df.loc[2, 'Waren_Material_%']:.1f}% der Einnahmen)")
        print(f"   • Absolute Betriebsausgaben sind in Q2-25 höher als in Q1-25")

    # EINZELNE DIAGRAMME FÜR PRÄSENTATION ERSTELLEN
    print(f"\n🎨 Erstelle einzelne Diagramme für Präsentation...")
    
    # Stil für professionelle Diagramme setzen
    plt.style.use('default')
    sns.set_palette("Set2")
    
    chart_files = []
    
    # 1. UMSATZ UND GEWINN DIAGRAMM
    if len(df) >= 2:
        quarters = df['Quartal'].tolist()
        
        # Grafik 1: Umsatz und Gewinn
        fig, ax = plt.subplots(1, 1, figsize=(12, 8))
        
        x = np.arange(len(quarters))
        width = 0.35
        
        bars1 = ax.bar(x - width/2, df['Betriebseinnahmen'], width, alpha=0.8, color='skyblue', label='Betriebseinnahmen')
        bars2 = ax.bar(x + width/2, df['Gewinn'], width, alpha=0.8, color='lightgreen', label='Gewinn/Verlust')
        
        ax.set_title('Neckar Wave Foods - Umsatz vs. Gewinn/Verlust', fontweight='bold', fontsize=16, pad=20)
        ax.set_ylabel('Euro (€)', fontweight='bold', fontsize=12)
        ax.set_xlabel('Quartale', fontweight='bold', fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels(quarters)
        ax.legend(fontsize=12)
        ax.grid(True, alpha=0.3)
        
        # Werte auf Balken anzeigen
        for bar in bars1:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                   f'{height:,.0f}€', ha='center', va='bottom', fontweight='bold')
        
        for bar in bars2:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height + (height*0.01 if height > 0 else height*0.01),
                   f'{height:,.0f}€', ha='center', va='bottom' if height > 0 else 'top', fontweight='bold')
        
        plt.tight_layout()
        chart1_filename = os.path.join(output_dir, f"01_Umsatz_Gewinn_{datetime.now().strftime('%Y%m%d_%H%M')}.png")
        plt.savefig(chart1_filename, dpi=300, bbox_inches='tight', facecolor='white')
        plt.close()
        chart_files.append(chart1_filename)
        print(f"✅ Umsatz-Gewinn-Diagramm gespeichert: {chart1_filename}")

        # Grafik 2: Gewinnmarge Entwicklung
        fig, ax = plt.subplots(1, 1, figsize=(10, 6))
        ax.plot(quarters, df['Gewinnmarge_%'], marker='o', linewidth=4, markersize=10, color='darkgreen')
        ax.set_title('Neckar Wave Foods - Gewinnmargen-Entwicklung', fontweight='bold', fontsize=16, pad=20)
        ax.set_ylabel('Gewinnmarge (%)', fontweight='bold', fontsize=12)
        ax.set_xlabel('Quartale', fontweight='bold', fontsize=12)
        ax.grid(True, alpha=0.3)
        ax.axhline(y=0, color='red', linestyle='--', alpha=0.7)
        
        # Werte an Punkten anzeigen
        for i, (quarter, marge) in enumerate(zip(quarters, df['Gewinnmarge_%'])):
            ax.annotate(f'{marge:.1f}%', (i, marge), textcoords="offset points", 
                       xytext=(0,10), ha='center', fontweight='bold', fontsize=11)
        
        plt.tight_layout()
        chart2_filename = os.path.join(output_dir, f"02_Gewinnmarge_{datetime.now().strftime('%Y%m%d_%H%M')}.png")
        plt.savefig(chart2_filename, dpi=300, bbox_inches='tight', facecolor='white')
        plt.close()
        chart_files.append(chart2_filename)
        print(f"✅ Gewinnmarge-Diagramm gespeichert: {chart2_filename}")
        
        # Grafik 3: Kostenstruktur Entwicklung
        fig, ax = plt.subplots(1, 1, figsize=(12, 8))
        width = 0.25
        x = np.arange(len(quarters))
        
        bars1 = ax.bar(x - width, df['Personalkosten_%'], width, label='Personal', alpha=0.8, color='#FF6B6B')
        bars2 = ax.bar(x, df['Waren_Material_%'], width, label='Waren/Material', alpha=0.8, color='#4ECDC4')
        bars3 = ax.bar(x + width, df['Miete_Pacht_%'], width, label='Miete/Pacht', alpha=0.8, color='#45B7D1')
        
        ax.set_title('Neckar Wave Foods - Kostenstruktur-Entwicklung (% vom Umsatz)', fontweight='bold', fontsize=16, pad=20)
        ax.set_ylabel('Anteil am Umsatz (%)', fontweight='bold', fontsize=12)
        ax.set_xlabel('Quartale', fontweight='bold', fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels(quarters)
        ax.legend(fontsize=12)
        ax.grid(True, alpha=0.3)
        
        # Werte auf Balken anzeigen
        for bars in [bars1, bars2, bars3]:
            for bar in bars:
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height + 0.3,
                       f'{height:.1f}%', ha='center', va='bottom', fontweight='bold', fontsize=10)
        
        plt.tight_layout()
        chart3_filename = os.path.join(output_dir, f"03_Kostenstruktur_Entwicklung_{datetime.now().strftime('%Y%m%d_%H%M')}.png")
        plt.savefig(chart3_filename, dpi=300, bbox_inches='tight', facecolor='white')
        plt.close()
        chart_files.append(chart3_filename)
        print(f"✅ Kostenstruktur-Entwicklung-Diagramm gespeichert: {chart3_filename}")

        # Grafik 4: Absolute Kostenkategorien
        fig, ax = plt.subplots(1, 1, figsize=(10, 8))
        x = np.arange(len(quarters))
        width = 0.35
        
        bars1 = ax.bar(x - width/2, df['Direkte_Kosten'], width, alpha=0.8, color='salmon', label='Direkte Kosten')
        bars2 = ax.bar(x + width/2, df['Indirekte_Kosten'], width, alpha=0.8, color='orange', label='Indirekte Kosten')
        
        ax.set_title('Neckar Wave Foods - Direkte vs. Indirekte Kosten', fontweight='bold', fontsize=16, pad=20)
        ax.set_ylabel('Euro (€)', fontweight='bold', fontsize=12)
        ax.set_xlabel('Quartale', fontweight='bold', fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels(quarters)
        ax.legend(fontsize=12)
        ax.grid(True, alpha=0.3)
        
        # Werte auf Balken anzeigen
        for bars in [bars1, bars2]:
            for bar in bars:
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                       f'{height:,.0f}€', ha='center', va='bottom', fontweight='bold', fontsize=10)
        
        plt.tight_layout()
        chart4_filename = os.path.join(output_dir, f"04_Direkte_Indirekte_Kosten_{datetime.now().strftime('%Y%m%d_%H%M')}.png")
        plt.savefig(chart4_filename, dpi=300, bbox_inches='tight', facecolor='white')
        plt.close()
        chart_files.append(chart4_filename)
        print(f"✅ Direkte-Indirekte-Kosten-Diagramm gespeichert: {chart4_filename}")

    # 5. KOSTENSTRUKTUR PIE CHARTS (ein separates Diagramm pro Quartal)
    if len(df) >= 1:
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FECA57', '#FF9FF3', '#54A0FF']
        
        for i, (idx, row) in enumerate(df.iterrows()):
            fig, ax = plt.subplots(1, 1, figsize=(10, 8))
            
            # Daten für Pie Chart vorbereiten
            categories = ['Personalkosten', 'Waren/Material', 'Miete/Pacht', 'Sonstige Direkte', 'Sonstige Indirekte', 'Abschreibungen']
            values = [
                row['Personalkosten'],
                row['Waren_Material'], 
                row['Miete_Pacht'],
                row['Sonstige_Direkte'],
                row['Sonstige_Indirekte'],
                row['Abschreibungen']
            ]
            
            # Nur Kategorien mit Werten > 0 anzeigen
            non_zero_data = [(cat, val) for cat, val in zip(categories, values) if val > 0]
            if non_zero_data:
                labels, sizes = zip(*non_zero_data)
                
                wedges, texts, autotexts = ax.pie(sizes, labels=labels, autopct='%1.1f%%', 
                                                 colors=colors[:len(labels)], startangle=90,
                                                 textprops={'fontsize': 12})
                
                # Prozentangaben fett machen
                for autotext in autotexts:
                    autotext.set_color('white')
                    autotext.set_fontweight('bold')
                    autotext.set_fontsize(11)
                    
            ax.set_title(f'Neckar Wave Foods - Kostenstruktur {row["Quartal"]}\nGesamtkosten: {row["Betriebsausgaben"]:,.0f}€', 
                        fontweight='bold', fontsize=16, pad=20)
            
            plt.tight_layout()
            pie_filename = os.path.join(output_dir, f"05_Kostenstruktur_{row['Quartal']}_{datetime.now().strftime('%Y%m%d_%H%M')}.png")
            plt.savefig(pie_filename, dpi=300, bbox_inches='tight', facecolor='white')
            plt.close()
            chart_files.append(pie_filename)
            print(f"✅ Kostenstruktur {row['Quartal']} Pie-Chart gespeichert: {pie_filename}")

    print("\n" + "="*80)
    print("🎯 ANALYSE ABGESCHLOSSEN!")
    print("="*80)
    print(f"📁 Alle Dateien gespeichert in: {output_dir}/")
    print(f"📄 Excel-Haupttabelle: {os.path.basename(filename)}")
    for chart_file in chart_files:
        print(f"📊 Diagramm: {os.path.basename(chart_file)}")
    print(f"\n📈 Insgesamt {len(chart_files)} Diagramme erstellt")
    print("💡 Alle Diagramme sind optimiert für Präsentationen (300 DPI, weißer Hintergrund)")

else:
    print("❌ Keine Daten verfügbar für die Analyse.") 