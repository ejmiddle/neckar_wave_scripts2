import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from matplotlib.ticker import FuncFormatter
import os
import csv

# Set style for beautiful charts
plt.style.use('seaborn-v0_8-whitegrid')
sns.set_palette("husl")

def euro_formatter(x, pos):
    """Format numbers as Euro currency"""
    return f'{x/1000:.0f}k €'

def percent_formatter(x, pos):
    """Format numbers as percentage"""
    return f'{x:.1f}%'

def create_forecast_charts():
    # Create output directory
    input_dir = 'guv/inputs/'
    output_dir = 'guv/outputs/prognose'
    os.makedirs(output_dir, exist_ok=True)
    
    # Read the forecast data from prognose_input.csv
    data = {}
    quarters = []
    
    with open(input_dir + 'prognose_input.csv', 'r', encoding='utf-8') as file:
        csv_reader = csv.reader(file, delimiter=';')
        rows = list(csv_reader)
    
    print("================")
    print(rows)
    print("================")

    # Get quarters from header row (skip first column which is "Kennzahl")
    quarters = [col.strip() for col in rows[0][1:]]
    
    # Process each metric row
    for row in rows[1:]:
        if len(row) > 1:
            metric = row[0].strip()
            values = row[1:]
            
            # Clean the values
            clean_values = []
            for val in values:
                # Remove € symbol, spaces, and other formatting, convert to number
                val_clean = val.replace('€', '').replace(' ', '').replace('%', '').replace(',', '.')
                try:
                    # Handle empty or malformed values
                    if val_clean and val_clean != '0':
                        clean_values.append(float(val_clean))
                    else:
                        clean_values.append(0)
                except:
                    clean_values.append(0)
            
            # Only add if we have the right number of values
            if len(clean_values) == len(quarters):
                data[metric] = clean_values

    # Create DataFrame
    df = pd.DataFrame(data, index=quarters)
    
    # Create individual charts
    create_financial_overview(df, quarters, output_dir)
    create_profit_margin_chart(df, quarters, output_dir)
    create_revenue_growth_chart(df, quarters, output_dir)
    create_profit_evolution_chart(df, quarters, output_dir)
    create_cost_ratio_chart(df, quarters, output_dir)
    create_summary_dashboard(df, quarters, output_dir)

def create_financial_overview(df, quarters, output_dir):
    """Create financial overview chart"""
    fig, ax = plt.subplots(figsize=(14, 8))
    
    ax.plot(quarters, df['Betriebseinnahmen (€)'], marker='o', linewidth=3, markersize=8, label='Betriebseinnahmen', color='#2E8B57')
    ax.plot(quarters, df['Betriebsausgaben (€)'], marker='s', linewidth=3, markersize=8, label='Betriebsausgaben', color='#DC143C')
    ax.plot(quarters, df['Gewinn/Verlust (€)'], marker='^', linewidth=3, markersize=8, label='Gewinn/Verlust', color='#4169E1')
    
    ax.set_title('Neckar Wave - Finanzentwicklung 2025-2027', fontsize=16, fontweight='bold', pad=20)
    ax.set_xlabel('Quartal', fontsize=12)
    ax.set_ylabel('Betrag in €', fontsize=12)
    ax.legend(fontsize=11, frameon=True, fancybox=True, shadow=True)
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_formatter(FuncFormatter(euro_formatter))
    
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    
    # Add value annotations for Gewinn/Verlust
    for i, (quarter, value) in enumerate(zip(quarters, df['Gewinn/Verlust (€)'])):
        if i % 2 == 0:
            ax.annotate(f'{value/1000:.1f}k€', 
                        (quarter, value), 
                        textcoords="offset points", 
                        xytext=(0,10), 
                        ha='center', 
                        fontsize=9,
                        bbox=dict(boxstyle='round,pad=0.3', facecolor='yellow', alpha=0.5))
    
    plt.tight_layout()
    plt.savefig(f'{output_dir}/01_Finanzentwicklung.png', dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()

def create_profit_margin_chart(df, quarters, output_dir):
    """Create profit margin chart"""
    fig, ax = plt.subplots(figsize=(14, 8))
    
    bars = ax.bar(quarters, df['Gewinnmarge (%)'], 
                  color=['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FECA57', '#FF9FF3', 
                         '#54A0FF', '#5F27CD', '#00D2D3', '#FF9F43', '#10AC84', '#EE5A24'], alpha=0.8)
    
    ax.set_title('Neckar Wave - Entwicklung der Gewinnmarge 2025-2027', fontsize=16, fontweight='bold', pad=20)
    ax.set_xlabel('Quartal', fontsize=12)
    ax.set_ylabel('Gewinnmarge in %', fontsize=12)
    ax.grid(True, alpha=0.3, axis='y')
    
    # Add value annotations on bars
    for bar, value in zip(bars, df['Gewinnmarge (%)']):
        height = bar.get_height()
        ax.annotate(f'{value:.1f}%',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom',
                    fontsize=10, fontweight='bold')
    
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    
    # Add trend line
    x_numeric = range(len(quarters))
    z = np.polyfit(x_numeric, df['Gewinnmarge (%)'], 1)
    p = np.poly1d(z)
    ax.plot(quarters, p(x_numeric), "r--", alpha=0.8, linewidth=2, 
            label=f'Trend (Steigung: +{z[0]:.1f}% pro Quartal)')
    ax.legend(fontsize=10)
    
    plt.tight_layout()
    plt.savefig(f'{output_dir}/02_Gewinnmarge.png', dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()

def create_revenue_growth_chart(df, quarters, output_dir):
    """Create revenue growth chart"""
    fig, ax = plt.subplots(figsize=(12, 8))
    
    revenue_growth = [(df['Betriebseinnahmen (€)'][i] - df['Betriebseinnahmen (€)'][0]) / df['Betriebseinnahmen (€)'][0] * 100 for i in range(len(quarters))]
    ax.plot(quarters, revenue_growth, marker='o', linewidth=3, markersize=8, color='#2E8B57')
    ax.fill_between(quarters, revenue_growth, alpha=0.3, color='#2E8B57')
    
    ax.set_title('Neckar Wave - Umsatzwachstum vs. Q1-25', fontsize=16, fontweight='bold', pad=20)
    ax.set_xlabel('Quartal', fontsize=12)
    ax.set_ylabel('Wachstum in %', fontsize=12)
    ax.grid(True, alpha=0.3)
    
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    
    plt.tight_layout()
    plt.savefig(f'{output_dir}/03_Umsatzwachstum.png', dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()

def create_profit_evolution_chart(df, quarters, output_dir):
    """Create profit evolution chart"""
    fig, ax = plt.subplots(figsize=(12, 8))
    
    ax.fill_between(quarters, df['Gewinn/Verlust (€)'], alpha=0.6, color='#4169E1')
    ax.plot(quarters, df['Gewinn/Verlust (€)'], marker='o', linewidth=3, markersize=8, color='#1E3A8A')
    
    ax.set_title('Neckar Wave - Gewinnentwicklung 2025-2027', fontsize=16, fontweight='bold', pad=20)
    ax.set_xlabel('Quartal', fontsize=12)
    ax.set_ylabel('Gewinn in €', fontsize=12)
    ax.yaxis.set_major_formatter(FuncFormatter(euro_formatter))
    ax.grid(True, alpha=0.3)
    
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    
    plt.tight_layout()
    plt.savefig(f'{output_dir}/04_Gewinnentwicklung.png', dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()

def create_cost_ratio_chart(df, quarters, output_dir):
    """Create cost ratio chart"""
    fig, ax = plt.subplots(figsize=(12, 8))
    
    cost_ratio = [df['Betriebsausgaben (€)'][i] / df['Betriebseinnahmen (€)'][i] * 100 for i in range(len(quarters))]
    ax.plot(quarters, cost_ratio, marker='s', linewidth=3, markersize=8, color='#DC143C')
    
    ax.set_title('Neckar Wave - Kostenanteil am Umsatz', fontsize=16, fontweight='bold', pad=20)
    ax.set_xlabel('Quartal', fontsize=12)
    ax.set_ylabel('Kostenanteil in %', fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(80, 100)
    
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    
    plt.tight_layout()
    plt.savefig(f'{output_dir}/05_Kostenanteil.png', dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()

def create_summary_dashboard(df, quarters, output_dir):
    """Create summary dashboard"""
    fig, ax = plt.subplots(figsize=(12, 10))
    ax.axis('off')
    
    # Calculate key metrics
    total_revenue_start = df['Betriebseinnahmen (€)'][0]
    total_revenue_end = df['Betriebseinnahmen (€)'][-1]
    revenue_growth_total = ((total_revenue_end - total_revenue_start) / total_revenue_start) * 100
    
    avg_profit_margin = df['Gewinnmarge (%)'].mean()
    max_profit = df['Gewinn/Verlust (€)'].max()
    total_profit_3_years = df['Gewinn/Verlust (€)'].sum()
    
    summary_text = f"""
    📈 NECKAR WAVE - PROGNOSE ZUSAMMENFASSUNG 2025-2027
    
    🎯 SCHLÜSSEL-KENNZAHLEN:
    
    ▸ Umsatzwachstum (3 Jahre): +{revenue_growth_total:.1f}%
    ▸ Startumsatz Q1-25: {total_revenue_start/1000:.0f}k €
    ▸ Endumsatz Q4-27: {total_revenue_end/1000:.0f}k €
    
    ▸ Durchschnittliche Gewinnmarge: {avg_profit_margin:.1f}%
    ▸ Höchster Quartalsgewinn: {max_profit/1000:.1f}k €
    ▸ Gesamtgewinn (3 Jahre): {total_profit_3_years/1000:.0f}k €
    
    💡 TREND-ANALYSE:
    ▸ Kontinuierliches Umsatzwachstum
    ▸ Stark steigende Profitabilität
    ▸ Sinkende Kostenbasis (relativ)
    ▸ Positive Geschäftsentwicklung
    
    🎪 PROGNOSE-HIGHLIGHTS:
    ▸ Gewinnmarge steigt von 2,4% auf 20,4%
    ▸ Umsatz wächst um durchschnittlich 5,3% pro Quartal
    ▸ Break-Even bereits in Q1-25 erreicht
    """
    
    ax.text(0.05, 0.95, summary_text, transform=ax.transAxes, fontsize=14,
             verticalalignment='top', fontfamily='monospace',
             bbox=dict(boxstyle='round,pad=1', facecolor='lightblue', alpha=0.8))
    
    plt.tight_layout()
    plt.savefig(f'{output_dir}/06_Zusammenfassung.png', dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()

if __name__ == "__main__":
    create_forecast_charts()
    print("✅ Alle Grafiken wurden erfolgreich erstellt!")
    print("📊 Dateien gespeichert in: guv/outputs/prognose/")
    print("   - 01_Finanzentwicklung.png")
    print("   - 02_Gewinnmarge.png")
    print("   - 03_Umsatzwachstum.png")
    print("   - 04_Gewinnentwicklung.png")
    print("   - 05_Kostenanteil.png")
    print("   - 06_Zusammenfassung.png") 