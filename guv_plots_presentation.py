import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime

# Stelle sicher, dass die Plots auch ohne GUI-Backend funktionieren
plt.style.use('default')
plt.rcParams.update({'font.size': 12, 'font.family': 'sans-serif'})

# Erstelle strukturierte Daten aus den CSV-Inhalten
data = {
    'Quartal': ['Q4-24', 'Q1-25', 'Q2-25'],
    'Zeitraum': ['01.10.2024 - 31.12.2024', '01.01.2025 - 31.03.2025', '01.04.2025 - 30.06.2025'],
    'Gewinn': [-22129.74, 3343.92, 7658.41],
    'Betriebseinnahmen': [124661.59, 138051.44, 164559.73],
    'Betriebsausgaben': [146791.33, 134707.52, 156901.32],
    'Direkte_Kosten': [109856.47, 98887.69, 125155.01],
    'Indirekte_Kosten': [31998.89, 30890.43, 26035.06],
    'Abschreibungen': [4935.98, 4929.40, 5711.25]
}

df = pd.DataFrame(data)

# Berechne zusätzliche Kennzahlen
df['Gewinnmarge_%'] = (df['Gewinn'] / df['Betriebseinnahmen']) * 100
df['Direkte_Kosten_%'] = (df['Direkte_Kosten'] / df['Betriebseinnahmen']) * 100
df['Indirekte_Kosten_%'] = (df['Indirekte_Kosten'] / df['Betriebseinnahmen']) * 100

# Plot 1: Gewinnentwicklung
plt.figure(figsize=(10, 6))
colors = ['#d62728' if x < 0 else '#2ca02c' for x in df['Gewinn']]
bars = plt.bar(df['Quartal'], df['Gewinn'], color=colors, alpha=0.8, edgecolor='black', linewidth=1)
plt.title('Gewinn/Verlust Entwicklung', fontsize=16, fontweight='bold', pad=20)
plt.ylabel('Euro (€)', fontsize=12)
plt.axhline(y=0, color='black', linestyle='-', linewidth=1)
plt.grid(True, alpha=0.3, axis='y')

# Werte über den Balken anzeigen
for i, v in enumerate(df['Gewinn']):
    plt.text(i, v + (1000 if v >= 0 else -1500), f'{v:,.0f}€', 
             ha='center', va='bottom' if v >= 0 else 'top', fontweight='bold', fontsize=11)

plt.tight_layout()
plt.savefig('plot1_gewinn_entwicklung.png', dpi=300, bbox_inches='tight')
plt.close()

# Plot 2: Umsatzentwicklung
plt.figure(figsize=(10, 6))
plt.plot(df['Quartal'], df['Betriebseinnahmen'], marker='o', linewidth=3, 
         markersize=10, color='#1f77b4', markerfacecolor='white', markeredgewidth=2)
plt.title('Betriebseinnahmen Entwicklung', fontsize=16, fontweight='bold', pad=20)
plt.ylabel('Euro (€)', fontsize=12)
plt.grid(True, alpha=0.3)

# Werte über den Punkten anzeigen
for i, v in enumerate(df['Betriebseinnahmen']):
    plt.text(i, v + 3000, f'{v:,.0f}€', ha='center', va='bottom', fontweight='bold', fontsize=11)

# Wachstumsraten hinzufügen
for i in range(1, len(df)):
    wachstum = ((df.iloc[i]['Betriebseinnahmen'] - df.iloc[i-1]['Betriebseinnahmen']) / 
                df.iloc[i-1]['Betriebseinnahmen']) * 100
    plt.annotate(f'+{wachstum:.1f}%', 
                xy=(i-0.5, (df.iloc[i]['Betriebseinnahmen'] + df.iloc[i-1]['Betriebseinnahmen'])/2),
                ha='center', va='center', fontsize=10, 
                bbox=dict(boxstyle="round,pad=0.3", facecolor='lightblue', alpha=0.7))

plt.tight_layout()
plt.savefig('plot2_umsatz_entwicklung.png', dpi=300, bbox_inches='tight')
plt.close()

# Plot 3: Kostenstruktur
plt.figure(figsize=(12, 6))
x = np.arange(len(df['Quartal']))
width = 0.35

bars1 = plt.bar(x - width/2, df['Direkte_Kosten'], width, label='Direkte Kosten', 
                color='#ff7f0e', alpha=0.8, edgecolor='black', linewidth=1)
bars2 = plt.bar(x + width/2, df['Indirekte_Kosten'], width, label='Indirekte Kosten', 
                color='#d62728', alpha=0.8, edgecolor='black', linewidth=1)

plt.title('Kostenstruktur pro Quartal', fontsize=16, fontweight='bold', pad=20)
plt.ylabel('Euro (€)', fontsize=12)
plt.xlabel('Quartal', fontsize=12)
plt.xticks(x, df['Quartal'])
plt.legend(fontsize=11)
plt.grid(True, alpha=0.3, axis='y')

# Werte über den Balken anzeigen
for i, v in enumerate(df['Direkte_Kosten']):
    plt.text(i - width/2, v + 1000, f'{v:,.0f}€', ha='center', va='bottom', fontsize=9, rotation=0)
for i, v in enumerate(df['Indirekte_Kosten']):
    plt.text(i + width/2, v + 1000, f'{v:,.0f}€', ha='center', va='bottom', fontsize=9, rotation=0)

plt.tight_layout()
plt.savefig('plot3_kostenstruktur.png', dpi=300, bbox_inches='tight')
plt.close()

# Plot 4: Gewinnmarge Entwicklung
plt.figure(figsize=(10, 6))
plt.plot(df['Quartal'], df['Gewinnmarge_%'], marker='s', linewidth=3, 
         markersize=10, color='#9467bd', markerfacecolor='white', markeredgewidth=2)
plt.title('Gewinnmarge Entwicklung', fontsize=16, fontweight='bold', pad=20)
plt.ylabel('Gewinnmarge (%)', fontsize=12)
plt.axhline(y=0, color='red', linestyle='--', alpha=0.7, linewidth=2)
plt.grid(True, alpha=0.3)

# Werte über den Punkten anzeigen
for i, v in enumerate(df['Gewinnmarge_%']):
    plt.text(i, v + 1, f'{v:.1f}%', ha='center', va='bottom', fontweight='bold', fontsize=11)

# Farbige Hintergründe für positive/negative Bereiche
plt.axhspan(-25, 0, alpha=0.1, color='red', label='Verlustbereich')
plt.axhspan(0, 10, alpha=0.1, color='green', label='Gewinnbereich')

plt.tight_layout()
plt.savefig('plot4_gewinnmarge.png', dpi=300, bbox_inches='tight')
plt.close()

print("✅ Alle vier Plots wurden erfolgreich erstellt und gespeichert:")
print("   📊 plot1_gewinn_entwicklung.png")
print("   📈 plot2_umsatz_entwicklung.png") 
print("   💰 plot3_kostenstruktur.png")
print("   📉 plot4_gewinnmarge.png")
print()
print("Die Plots sind hochauflösend (300 DPI) und bereit für Präsentationen!")

# Analyseergebnisse als Text ausgeben
print("\n" + "="*60)
print("ZUSAMMENFASSUNG DER QUARTALSERGEBNISSE")
print("="*60)

print("\n📈 KERNKENNZAHLEN:")
for i, row in df.iterrows():
    print(f"\n{row['Quartal']} ({row['Zeitraum']}):")
    print(f"  Betriebseinnahmen: {row['Betriebseinnahmen']:>12,.0f} €")
    print(f"  Betriebsausgaben:  {row['Betriebsausgaben']:>12,.0f} €")
    print(f"  Gewinn/Verlust:    {row['Gewinn']:>12,.0f} €")
    print(f"  Gewinnmarge:       {row['Gewinnmarge_%']:>12.1f} %")

print(f"\n✨ HIGHLIGHTS:")
print(f"   🎯 Turnaround: Von {df.loc[0, 'Gewinn']:,.0f}€ Verlust zu {df.loc[2, 'Gewinn']:,.0f}€ Gewinn")
print(f"   📊 Umsatzwachstum: +{((df.loc[2, 'Betriebseinnahmen']/df.loc[0, 'Betriebseinnahmen']-1)*100):.1f}% über 2 Quartale")
print(f"   💹 Gewinnmarge: Von {df.loc[0, 'Gewinnmarge_%']:.1f}% auf {df.loc[2, 'Gewinnmarge_%']:.1f}%")

umsatz_wachstum_q1 = ((df.loc[1, 'Betriebseinnahmen'] - df.loc[0, 'Betriebseinnahmen']) / df.loc[0, 'Betriebseinnahmen']) * 100
umsatz_wachstum_q2 = ((df.loc[2, 'Betriebseinnahmen'] - df.loc[1, 'Betriebseinnahmen']) / df.loc[1, 'Betriebseinnahmen']) * 100

print(f"\n📈 WACHSTUMSRATEN:")
print(f"   Q4-24 → Q1-25: {umsatz_wachstum_q1:+.1f}% Umsatzwachstum")
print(f"   Q1-25 → Q2-25: {umsatz_wachstum_q2:+.1f}% Umsatzwachstum") 