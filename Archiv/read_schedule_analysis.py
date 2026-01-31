#!/usr/bin/env python3
"""
Script to analyze work schedule data including hours breakdown by weekend/weekday
"""

import pandas as pd
import re
from datetime import datetime

def parse_date_time(date_str):
    """Parse the date string to extract date, start time, end time, and calculate duration"""
    if pd.isna(date_str):
        return None, None, None, 0, False
    
    # Extract date and time components using regex
    # Format: "September 11, 2025 9:00 AM (GMT+2) → 5:00 PM"
    pattern = r'([A-Za-z]+ \d{1,2}, \d{4}) (\d{1,2}:\d{2} [AP]M) \(GMT\+2\) → (\d{1,2}:\d{2} [AP]M)'
    match = re.match(pattern, date_str)
    
    if not match:
        return None, None, None, 0, False
    
    date_part, start_time, end_time = match.groups()
    
    try:
        # Parse the date to get day of week
        date_obj = datetime.strptime(date_part, '%B %d, %Y')
        
        # Parse start and end times
        start_dt = datetime.strptime(f"{date_part} {start_time}", '%B %d, %Y %I:%M %p')
        end_dt = datetime.strptime(f"{date_part} {end_time}", '%B %d, %Y %I:%M %p')
        
        # Calculate duration in hours
        duration = (end_dt - start_dt).total_seconds() / 3600
        
        # Check if it's weekend (Saturday=5, Sunday=6)
        is_weekend = date_obj.weekday() >= 5
        
        return date_obj, start_dt, end_dt, duration, is_weekend
        
    except ValueError:
        return None, None, None, 0, False

def analyze_work_hours(df, location):
    """Analyze work hours for each employee"""
    results = []
    
    for _, row in df.iterrows():
        employee = row['Employee']
        if pd.isna(employee):
            continue
            
        date_obj, start_dt, end_dt, duration, is_weekend = parse_date_time(row['Date'])
        
        if duration > 0:
            results.append({
                'Employee': employee,
                'Location': location,
                'Date': date_obj,
                'Duration': duration,
                'IsWeekend': is_weekend,
                'Task': row['Task']
            })
    
    return pd.DataFrame(results)

def main():
    # File paths
    alt_file = "Schichtplan/analysen/ALT September 25e4e28bdf9e80e5a0b9ed027e2cf045.csv"
    wie_file = "Schichtplan/analysen/WIE September 25e4e28bdf9e81c3a6e5db1dc878bc36.csv"
    
    # Set pandas display options for better formatting
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_rows', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', 50)
    
    try:
        # Read the CSV files
        print("Reading ALT location data...")
        df_alt = pd.read_csv(alt_file)
        
        print("Reading WIE location data...")
        df_wie = pd.read_csv(wie_file)
        
        # Print basic info about the DataFrames
        print("\n" + "="*60)
        print("ORIGINAL DATA OVERVIEW")
        print("="*60)
        print(f"ALT - Shape: {df_alt.shape}, Columns: {list(df_alt.columns)}")
        print(f"WIE - Shape: {df_wie.shape}, Columns: {list(df_wie.columns)}")
        
        # Analyze work hours for both locations
        print("\nAnalyzing work hours...")
        hours_alt = analyze_work_hours(df_alt, 'ALT')
        hours_wie = analyze_work_hours(df_wie, 'WIE')
        
        # Combine both datasets
        combined_hours = pd.concat([hours_alt, hours_wie], ignore_index=True)
        
        print(f"\nParsed {len(combined_hours)} valid shift records")
        
        # Calculate summary statistics per employee
        summary_stats = []
        
        for employee in combined_hours['Employee'].unique():
            emp_data = combined_hours[combined_hours['Employee'] == employee]
            
            total_hours = emp_data['Duration'].sum()
            weekend_hours = emp_data[emp_data['IsWeekend'] == True]['Duration'].sum()
            weekday_hours = emp_data[emp_data['IsWeekend'] == False]['Duration'].sum()
            
            weekend_percentage = (weekend_hours / total_hours * 100) if total_hours > 0 else 0
            weekday_percentage = (weekday_hours / total_hours * 100) if total_hours > 0 else 0
            
            # Get locations where this employee works
            locations = ', '.join(emp_data['Location'].unique())
            tasks = ', '.join(emp_data['Task'].unique())
            
            summary_stats.append({
                'Employee': employee,
                'Locations': locations,
                'Tasks': tasks,
                'Total_Hours': round(total_hours, 1),
                'Weekend_Hours': round(weekend_hours, 1),
                'Weekday_Hours': round(weekday_hours, 1),
                'Weekend_Percentage': round(weekend_percentage, 1),
                'Weekday_Percentage': round(weekday_percentage, 1),
                'Total_Shifts': len(emp_data)
            })
        
        # Create summary DataFrame
        summary_df = pd.DataFrame(summary_stats).sort_values('Total_Hours', ascending=False)
        
        print("\n" + "="*80)
        print("WORK HOURS ANALYSIS BY EMPLOYEE")
        print("="*80)
        print(summary_df.to_string(index=False))
        
        # Show weekend vs weekday breakdown
        print("\n" + "="*80)
        print("WEEKEND/WEEKDAY BREAKDOWN SUMMARY")
        print("="*80)
        total_weekend = combined_hours[combined_hours['IsWeekend'] == True]['Duration'].sum()
        total_weekday = combined_hours[combined_hours['IsWeekend'] == False]['Duration'].sum()
        total_all = total_weekend + total_weekday
        
        print(f"Total Weekend Hours: {total_weekend:.1f} ({total_weekend/total_all*100:.1f}%)")
        print(f"Total Weekday Hours: {total_weekday:.1f} ({total_weekday/total_all*100:.1f}%)")
        print(f"Total Hours: {total_all:.1f}")
        
        # Top weekend workers
        top_weekend = summary_df.nlargest(5, 'Weekend_Hours')[['Employee', 'Weekend_Hours', 'Weekend_Percentage']]
        print(f"\nTop 5 Weekend Workers:")
        print(top_weekend.to_string(index=False))
        
        # Most balanced workers (closest to 50/50 split)
        summary_df_copy = summary_df[summary_df['Total_Hours'] > 10].copy()  # Only consider employees with substantial hours
        summary_df_copy['Balance_Score'] = abs(summary_df_copy['Weekend_Percentage'] - 50)
        most_balanced = summary_df_copy.nsmallest(5, 'Balance_Score')[['Employee', 'Weekend_Percentage', 'Weekday_Percentage', 'Total_Hours']]
        print(f"\nMost Balanced Weekend/Weekday Workers:")
        print(most_balanced.to_string(index=False))
        
    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
    except pd.errors.EmptyDataError:
        print("Error: One of the CSV files is empty")
    except Exception as e:
        print(f"Error reading files: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
