import seaborn as sns
import matplotlib.pyplot as plt
import pandas as pd


# Load the CSV file into a DataFrame
df = pd.read_csv('roasts_231009.csv', sep=',')

# Show the first few rows of the DataFrame
print(df.head())
df['datetime'] = pd.to_datetime(df['date'] + ' ' + df['time'])
df['roastName'] = df['roastName'].astype(str)

df = df.sort_values(by='datetime')
print(df.head())


# Category-Keywords Mapping
category_keywords = {
    'ABE': ['Alex', 'Bermudez' , 'ABE'],
    'PTR': ['Paola', 'PT', 'PTR', 'Trujillo', 'trujillo'],
    'CTS': ['CTS', 'Andrade'],
    'MIO': ['CO2', 'lemon', 'MIO']
}

# Custom Function to Assign Categories
def assign_category(row):
    for category, keywords in category_keywords.items():
        if any(keyword in row['roastName'] for keyword in keywords):
            return category
    return 'Other'

df['beanName'] = df.apply(assign_category, axis=1)

df.drop(['date', 'time', 'beanId'], axis=1, inplace=True)

df.to_excel('roasts_sorted.xlsx', index=False)

df['month'] = df['datetime'].dt.month
# aggregated_df = df.groupby('beanName').sum('weightGreen')
# # print(df)
aggregated_df = df.groupby(['beanName', 'month']).agg({'weightGreen':'sum'}).reset_index()
aggregated_df['weightGreen'] = aggregated_df['weightGreen'] / 1000
print(aggregated_df)

aggregated_df = df.groupby(['beanName', 'month']).agg({'weightRoasted':'sum'}).reset_index()
aggregated_df['weightRoasted'] = aggregated_df['weightRoasted'] / 1000
print(aggregated_df)

# data = {
#     'beanName': ['ABE', 'ABE', 'ABE', 'ABE', 'CTS', 'CTS', 'CTS', 'CTS', 'MIO', 'MIO', 'MIO', 'MIO', 'MIO', 'Other', 'Other', 'Other', 'Other'],
#     'month': [5, 7, 8, 9, 6, 7, 9, 10, 6, 7, 8, 9, 10, 5, 6, 7, 8],
#     'weightRoasted': [0.00000, 11.51270, 7.88550, 4.24380, 0.81200, 0.27750, 9.01420, 6.00280, 12.57185, 9.31040, 22.92940, 26.80000, 5.59500, 3.33580, 1.37010, 1.66660, 1.86960]
# }

# df = pd.DataFrame(data)
pivot_df = aggregated_df.pivot_table(index="beanName", columns="month", values="weightRoasted", aggfunc='sum')

plt.figure(figsize=(10, 6))
sns.heatmap(pivot_df, annot=True, cmap='YlGnBu', cbar_kws={'label': 'weightRoasted'})
plt.title("Bean WeightRoasted by Month")
plt.savefig("heatmap_output.png", dpi=300)
plt.show()

aggregated_df = df.groupby(['beanName']).agg({'weightRoasted':'sum'}).reset_index()
aggregated_df['weightRoasted'] = aggregated_df['weightRoasted'] / 1000
print(aggregated_df)
