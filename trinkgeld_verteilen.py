import pandas as pd

# Load the Excel file
df = pd.read_excel('Trinkgeld_Tabellen/Trinkgelder_Verteilung_Juli.xlsx', sheet_name='Tabelle1')

df = df[(df['Karte'] != 'XXX') & (df['Karte'].notna()) & (df['Karte'] != '')]
print(df)
# Define the person columns
person_columns = ['Person1', 'Person2', 'Person3', 'Person4', 'Person5', 'Person6']

# Initialize an empty dictionary to hold the Trinkgeld for each person
trinkgeld_per_person = {}

# Iterate over each row to distribute Trinkgeld
for _, row in df.iterrows():
    # Get the Trinkgeld value
    trinkgeld = row['Trinkgeld_sum']
    if trinkgeld > 0:
        # Get the list of people who worked (non-NaN values in person columns)
        persons = row[person_columns].dropna().values
        print(trinkgeld)
        print(persons)
        # Remove all blank spaces from each element
        # persons = [str(person).replace(" ", "") for person in persons]
        # print(persons)
        
        # Calculate the amount of Trinkgeld per person
        trinkgeld_per_capita = trinkgeld / len(persons)

        # Distribute the Trinkgeld to each person
        for person in persons:
            if person in trinkgeld_per_person:
                trinkgeld_per_person[person] += trinkgeld_per_capita
            else:
                trinkgeld_per_person[person] = trinkgeld_per_capita

# Convert the result to a DataFrame for better readability
result_df = pd.DataFrame(list(trinkgeld_per_person.items()), columns=['Person', 'Total Trinkgeld'])

# Add verification check
total_trinkgeld_sum = df['Trinkgeld_sum'].sum()
total_trinkgeld_per_person = result_df['Total Trinkgeld'].sum()

print(f"\n=== VERIFICATION CHECK ===")
print(f"Sum of all Trinkgeld_sum: {total_trinkgeld_sum:.2f}")
print(f"Sum of trinkgeld per person: {total_trinkgeld_per_person:.2f}")
print(f"Difference: {abs(total_trinkgeld_sum - total_trinkgeld_per_person):.2f}")

if abs(total_trinkgeld_sum - total_trinkgeld_per_person) < 0.01:  # Using small tolerance for floating point comparison
    print("✅ SUCCESS: Sums are equal!")
else:
    print("❌ ERROR: Sums are NOT equal!")

result_df.to_excel('Trinkgeld_output.xlsx', index=False)  # index=False prevents the index from being written


print(result_df)
total_sum = result_df['Total Trinkgeld'].sum()
print(total_sum)
