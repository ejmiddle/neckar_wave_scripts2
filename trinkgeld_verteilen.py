import pandas as pd

# Load the Excel file
df = pd.read_excel('Trinkgeld_Tabellen/Trinkgelder_Verteilung_Februar.xlsx', sheet_name='Tabelle1')

df = df[df['Karte'] != 'XXX']
print(df)
# Define the person columns
person_columns = ['Person1', 'Person2', 'Person3', 'Person4', 'Person5']

# Initialize an empty dictionary to hold the Trinkgeld for each person
trinkgeld_per_person = {}

# Iterate over each row to distribute Trinkgeld
for _, row in df.iterrows():
    # Get the Trinkgeld value
    trinkgeld = row['Trinkgeld_sum']
    
    # Get the list of people who worked (non-NaN values in person columns)
    persons = row[person_columns].dropna().values
    # Remove all blank spaces from each element
    persons = [str(person).replace(" ", "") for person in persons]
    
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

result_df.to_excel('Trinkgeld_output.xlsx', index=False)  # index=False prevents the index from being written


print(result_df)
total_sum = result_df['Total Trinkgeld'].sum()
print(total_sum)
