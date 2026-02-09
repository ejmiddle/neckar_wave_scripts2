import io
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv
from requests.utils import parse_header_links

load_dotenv()

ACCESS_TOKEN = os.getenv("SHOPIFY_KEY")
SHOP_NAME = "suedseitecoffee"  # e.g., 'my-store'
API_VERSION = "2024-01"


def get_last_friday_4pm() -> str:
    """Calculate the timestamp for last Friday at 4 PM."""
    now = datetime.now(UTC)
    days_since_friday = (now.weekday() - 4) % 7
    last_friday = now - timedelta(days=days_since_friday)
    last_friday = last_friday.replace(hour=16, minute=0, second=0, microsecond=0)

    if now.weekday() == 4 and now.hour < 16:
        last_friday = last_friday - timedelta(days=7)

    return last_friday.isoformat()


def get_last_6_days_orders() -> pd.DataFrame:
    url = f"https://{SHOP_NAME}.myshopify.com/admin/api/{API_VERSION}/orders.json"
    last_friday_4pm = get_last_friday_4pm()

    params = {"status": "any", "created_at_min": last_friday_4pm, "limit": 250}

    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        raise Exception(
            f"Error fetching orders: {response.status_code} - {response.text}"
        )

    orders = response.json().get("orders", [])

    data = []
    for order in orders:
        created_at = datetime.fromisoformat(
            order.get("created_at").replace("Z", "+00:00")
        )
        formatted_date = created_at.strftime("%d.%m.%Y %H:%M")
        data.append(
            {
                "Order ID": order.get("id"),
                "Customer": f"{order.get('customer', {}).get('first_name', '')} {order.get('customer', {}).get('last_name', '')}",
                "Wann bestellt": formatted_date,
                "Total Price": float(order.get("total_price", 0.0)),
                "Financial Status": order.get("financial_status"),
                "Fulfillment Status": order.get("fulfillment_status"),
            }
        )

    return pd.DataFrame(data)


def get_last_6_days_orders_with_variants(
    start_date: datetime | None = None, end_date: datetime | None = None
) -> pd.DataFrame:
    url = f"https://{SHOP_NAME}.myshopify.com/admin/api/{API_VERSION}/orders.json"

    if start_date is None:
        start_date_str = get_last_friday_4pm()
    else:
        start_date_str = start_date.isoformat()

    params = {"status": "any", "created_at_min": start_date_str, "limit": 250}

    if end_date is not None:
        end_date_str = end_date.isoformat()
        params["created_at_max"] = end_date_str

    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
    }

    orders = []
    next_page_info: str | None = None
    while True:
        response = requests.get(url, headers=headers, params=params)

        if response.status_code != 200:
            raise Exception(
                f"Error fetching orders: {response.status_code} - {response.text}"
            )

        orders.extend(response.json().get("orders", []))

        link_header = response.headers.get("Link")
        if not link_header:
            break
        parsed_links = parse_header_links(link_header.rstrip(">").replace(">,", ">, "))
        next_link = next(
            (link for link in parsed_links if link.get("rel") == "next"), None
        )
        if not next_link or not next_link.get("url"):
            break
        query = parse_qs(urlparse(next_link["url"]).query)
        next_page_info = query.get("page_info", [None])[0]
        if not next_page_info:
            break
        params = {"limit": 250, "page_info": next_page_info}

    data = []
    for order in orders:
        created_at = datetime.fromisoformat(
            order.get("created_at").replace("Z", "+00:00")
        )
        formatted_date = created_at.strftime("%d.%m.%Y %H:%M")
        customer_name = f"{order.get('customer', {}).get('first_name', '')} {order.get('customer', {}).get('last_name', '')}".strip()
        for item in order.get("line_items", []):
            data.append(
                {
                    "Order ID": order.get("id"),
                    "Customer": customer_name,
                    "Wann bestellt": formatted_date,
                    "Product Title": item.get("title"),
                    "Variant Title": item.get("variant_title"),
                    "Variant ID": item.get("variant_id"),
                    "SKU": item.get("sku"),
                    "Quantity": item.get("quantity"),
                    "Price Per Item": float(item.get("price")),
                    "Total Order Price": float(order.get("total_price", 0.0)),
                    "Financial Status": order.get("financial_status"),
                    "Fulfillment Status": order.get("fulfillment_status"),
                }
            )

    return pd.DataFrame(data)


def get_heidelberg_weather() -> dict:
    """
    Fetch current weather data for Heidelberg using OpenWeatherMap's free API.
    """
    lat = 49.4122
    lon = 8.7100

    api_key = os.getenv("OPENWEATHER_API_KEY", "")

    if not api_key:
        return {
            "error": "OpenWeatherMap API key not found in environment variables. Set OPENWEATHER_API_KEY."
        }

    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric"

    try:
        response = requests.get(url)

        if response.status_code == 200:
            data = response.json()
            weather_data = {
                "temperature": data["main"]["temp"],
                "feels_like": data["main"]["feels_like"],
                "humidity": data["main"]["humidity"],
                "description": data["weather"][0]["description"],
                "wind_speed": data["wind"]["speed"],
                "city": data["name"],
                "icon": data["weather"][0]["icon"],
                "timestamp": datetime.fromtimestamp(data["dt"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
            return weather_data

        return {"error": f"API error: {response.status_code} - {response.text}"}
    except Exception as exc:
        return {"error": f"Request failed: {str(exc)}"}


def create_excel_download_button(
    df: pd.DataFrame, filename: str, button_label: str
) -> None:
    """
    Create a download button for a DataFrame as an Excel file.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buffer.seek(0)

    st.download_button(
        label=button_label,
        data=buffer,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
