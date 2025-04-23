import requests
import pandas as pd
from datetime import datetime, timedelta
import os 
from dotenv import load_dotenv

# Load the .env file (by default from current directory)
load_dotenv(".env")

# Access a specific variable
ACCESS_TOKEN = os.getenv("SHOPIFY_KEY")
SHOP_NAME = "suedseitecoffee"  # e.g., 'my-store'
# Shopify API version
API_VERSION = "2024-01"

# Endpoint URL
url = f"https://{SHOP_NAME}.myshopify.com/admin/api/{API_VERSION}/orders.json"

# # Optional: Add parameters (you can adjust status, date, etc.)
# params = {
#     "status": "any",  # "open", "closed", or "any"
#     "limit": 250      # max per request
# }

# headers = {
#     "X-Shopify-Access-Token": ACCESS_TOKEN,
#     "Content-Type": "application/json"
# }

# # Make request
# response = requests.get(url, headers=headers, params=params)

# if response.status_code == 200:
#     orders = response.json().get("orders", [])
#     total_revenue = sum(float(order["total_price"]) for order in orders)
#     print(f"Total Orders: {len(orders)}")
#     print(f"Total Revenue: ${total_revenue:.2f}")
# else:
#     print("Error:", response.status_code, response.text)

def get_last_6_days_orders():
    url = f"https://{SHOP_NAME}.myshopify.com/admin/api/{API_VERSION}/orders.json"
    
    # Date 6 days ago
    six_days_ago = (datetime.utcnow() - timedelta(days=6)).isoformat()

    params = {
        "status": "any",
        "created_at_min": six_days_ago,
        "limit": 250
    }

    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        raise Exception(f"Error fetching orders: {response.status_code} - {response.text}")

    orders = response.json().get("orders", [])
    
    # Extract relevant fields for the DataFrame
    data = []
    for order in orders:
        data.append({
            "Order ID": order.get("id"),
            "Customer": f"{order.get('customer', {}).get('first_name', '')} {order.get('customer', {}).get('last_name', '')}",
            "Created At": order.get("created_at"),
            "Total Price": float(order.get("total_price", 0.0)),
            "Financial Status": order.get("financial_status"),
            "Fulfillment Status": order.get("fulfillment_status"),
        })

    return pd.DataFrame(data)

def get_last_6_days_orders_with_variants():
    url = f"https://{SHOP_NAME}.myshopify.com/admin/api/{API_VERSION}/orders.json"
    
    six_days_ago = (datetime.utcnow() - timedelta(days=6)).isoformat()

    params = {
        "status": "any",
        "created_at_min": six_days_ago,
        "limit": 250
    }

    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        raise Exception(f"Error fetching orders: {response.status_code} - {response.text}")

    orders = response.json().get("orders", [])

    data = []
    for order in orders:
        customer_name = f"{order.get('customer', {}).get('first_name', '')} {order.get('customer', {}).get('last_name', '')}".strip()
        for item in order.get("line_items", []):
            data.append({
                "Order ID": order.get("id"),
                "Customer": customer_name,
                "Created At": order.get("created_at"),
                "Product Title": item.get("title"),
                "Variant Title": item.get("variant_title"),
                "Variant ID": item.get("variant_id"),
                "SKU": item.get("sku"),
                "Quantity": item.get("quantity"),
                "Price Per Item": float(item.get("price")),
                "Total Order Price": float(order.get("total_price", 0.0)),
                "Financial Status": order.get("financial_status"),
                "Fulfillment Status": order.get("fulfillment_status"),
            })

    return pd.DataFrame(data)

# Example usage
df = get_last_6_days_orders_with_variants()
print(df.head())
df.to_excel("shopify_orders.xlsx", index=False)


# Filter for specific product titles
filtered_df = df[df["Product Title"].str.contains("Gutes Brot|Brote für Solawi|Unsere Brote", na=False, regex=True)]
aggregated_df = filtered_df.groupby(["Product Title", "Variant Title"])["Quantity"].sum().reset_index()
aggregated_df = aggregated_df.sort_values(by="Quantity", ascending=False)
print(aggregated_df)
