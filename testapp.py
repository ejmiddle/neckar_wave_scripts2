import io
import os
from datetime import UTC, datetime, timedelta

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


# Access a specific variable
ACCESS_TOKEN = os.getenv("SHOPIFY_KEY")
SHOP_NAME = "suedseitecoffee"  # e.g., 'my-store'
# Shopify API version
API_VERSION = "2024-01"
# st.write(ACCESS_TOKEN)

# Endpoint URL
url = f"https://{SHOP_NAME}.myshopify.com/admin/api/{API_VERSION}/orders.json"

def get_last_friday_4pm():
    """Calculate the timestamp for last Friday at 4 PM."""
    now = datetime.now(UTC)
    days_since_friday = (now.weekday() - 4) % 7
    last_friday = now - timedelta(days=days_since_friday)
    last_friday = last_friday.replace(hour=16, minute=0, second=0, microsecond=0)

    # If we're on Friday and it's before 4 PM, get the previous Friday
    if now.weekday() == 4 and now.hour < 16:
        last_friday = last_friday - timedelta(days=7)

    return last_friday.isoformat()


def get_last_6_days_orders():
    url = f"https://{SHOP_NAME}.myshopify.com/admin/api/{API_VERSION}/orders.json"

    # Get orders from last Friday 4 PM
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

    # Extract relevant fields for the DataFrame
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
                "Created At": formatted_date,
                "Total Price": float(order.get("total_price", 0.0)),
                "Financial Status": order.get("financial_status"),
                "Fulfillment Status": order.get("fulfillment_status"),
            }
        )

    return pd.DataFrame(data)


def get_last_6_days_orders_with_variants(
    start_date: datetime | None = None, end_date: datetime | None = None
):
    url = f"https://{SHOP_NAME}.myshopify.com/admin/api/{API_VERSION}/orders.json"

    # Use provided start_date or default to last Friday 4 PM
    if start_date is None:
        start_date_str = get_last_friday_4pm()
    else:
        start_date_str = start_date.isoformat()

    params = {"status": "any", "created_at_min": start_date_str, "limit": 250}

    # Add end_date if provided
    if end_date is not None:
        end_date_str = end_date.isoformat()
        params["created_at_max"] = end_date_str

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
        customer_name = f"{order.get('customer', {}).get('first_name', '')} {order.get('customer', {}).get('last_name', '')}".strip()
        for item in order.get("line_items", []):
            data.append(
                {
                    "Order ID": order.get("id"),
                    "Customer": customer_name,
                    "Created At": formatted_date,
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


def get_heidelberg_weather():
    """
    Fetch current weather data for Heidelberg using OpenWeatherMap's free API.
    """
    # Heidelberg coordinates
    lat = 49.4122
    lon = 8.7100

    # Using OpenWeatherMap's free API
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
        else:
            return {"error": f"API error: {response.status_code} - {response.text}"}

    except Exception as e:
        return {"error": f"Request failed: {str(e)}"}


def create_excel_download_button(df: pd.DataFrame, filename: str, button_label: str):
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


st.title("Shopify Bestellungen")

# Date pickers for selecting the date range
col1, col2 = st.columns(2)

with col1:
    default_start_date = datetime.now(UTC).date() - timedelta(days=7)
    selected_start_date = st.date_input(
        "Bestellungen ab Datum:",
        value=default_start_date,
        help="Wählen Sie das Startdatum für die Bestellungsabfrage. Standard: 1 Woche vor heute.",
    )

with col2:
    default_end_date = datetime.now(UTC).date()
    selected_end_date = st.date_input(
        "Bestellungen bis Datum:",
        value=default_end_date,
        help="Wählen Sie das Enddatum für die Bestellungsabfrage. Standard: Heute.",
    )

# Convert dates to datetime with UTC timezone
start_datetime = datetime.combine(selected_start_date, datetime.min.time()).replace(
    tzinfo=UTC
)
end_datetime = datetime.combine(selected_end_date, datetime.max.time()).replace(
    tzinfo=UTC
)

# Validate date range
if selected_start_date > selected_end_date:
    st.error("⚠️ Das Startdatum darf nicht nach dem Enddatum liegen!")
else:
    st.write(
        f"Diese Ansicht zeigt alle Bestellungen vom {selected_start_date.strftime('%d.%m.%Y')} bis {selected_end_date.strftime('%d.%m.%Y')}."
    )

if st.button("Bestellungen aktualisieren"):
    if selected_start_date > selected_end_date:
        st.error("Bitte korrigieren Sie den Datumsbereich!")
    else:
        df = get_last_6_days_orders_with_variants(start_datetime, end_datetime)
        st.session_state.bestellungen = df
        st.session_state.start_date = selected_start_date
        st.session_state.end_date = selected_end_date
        df.to_excel("shopify_orders.xlsx", index=False)

if "bestellungen" in st.session_state:
    df = st.session_state.bestellungen
    with st.expander("Alle Bestellungen sehen"):
        st.write(df)
        create_excel_download_button(
            df,
            f"alle_bestellungen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            "📥 Alle Bestellungen herunterladen",
        )

    # Get all unique product titles from the dataframe
    all_product_titles = sorted(df["Product Title"].unique().tolist())

    # Initialize allowed product titles in session state if not present
    if "allowed_titles" not in st.session_state:
        # Default allowed titles
        default_titles = [
            "Gutes Brot nach Ziegelhausen/Schlierbach",
            "Unsere Brote",
            "Brote für Solawi",
        ]
        # Only include defaults that actually exist in the data
        st.session_state.allowed_titles = [
            title for title in default_titles if title in all_product_titles
        ]

    # Add configuration section for allowed titles
    with st.expander("Produkttitel auswählen"):
        st.write("Wählen Sie die Produkttitel aus, die angezeigt werden sollen:")

        # Display all unique product titles with checkboxes
        for title in all_product_titles:
            is_selected = title in st.session_state.allowed_titles
            if st.checkbox(
                title,
                value=is_selected,
                key=f"checkbox_{title}",
            ):
                if title not in st.session_state.allowed_titles:
                    st.session_state.allowed_titles.append(title)
            else:
                if title in st.session_state.allowed_titles:
                    st.session_state.allowed_titles.remove(title)

    # Filter the DataFrame to only include rows with allowed titles
    filtered_df = df[df["Product Title"].isin(st.session_state.allowed_titles)]

    # Get unique values from filtered DataFrame
    unique_values = filtered_df["Product Title"].unique()

    categories = st.multiselect(
        "Wähle Bestellort(e) aus",
        options=list(unique_values),
        default=list(unique_values),
    )

    if categories:
        st.write("Brote in Summe")
        # Filter for specific product titles
        filtered_df = df[df["Product Title"].isin(categories)]
        aggregated_df_summe = (
            filtered_df.groupby(["Product Title", "Variant Title"])["Quantity"]
            .sum()
            .reset_index()
        )
        aggregated_df_summe = aggregated_df_summe.sort_values(
            by="Quantity", ascending=False
        )
        st.write(aggregated_df_summe)
        create_excel_download_button(
            aggregated_df_summe,
            f"brote_summe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            "📥 Brote in Summe herunterladen",
        )

        st.write("Brote nach Kunde")
        # Filter for specific product titles
        filtered_df = df[df["Product Title"].isin(categories)]
        aggregated_df_kunde = (
            filtered_df.groupby(
                ["Customer", "Product Title", "Variant Title", "Created At"]
            )["Quantity"]
            .sum()
            .reset_index()
        )
        aggregated_df_kunde = aggregated_df_kunde.sort_values(
            by="Quantity", ascending=False
        )
        st.write(aggregated_df_kunde)
        create_excel_download_button(
            aggregated_df_kunde,
            f"brote_nach_kunde_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            "📥 Brote nach Kunde herunterladen",
        )
    else:
        st.info("Bitte wählen Sie mindestens einen Bestellort aus.")

if "bestellungen" in st.session_state:
    df = st.session_state.bestellungen
    total_orders = len(df)
    total_items = df["Quantity"].sum()

    if "start_date" in st.session_state and "end_date" in st.session_state:
        st.info(
            f"📊 Daten vom {st.session_state.start_date.strftime('%d.%m.%Y')} bis {st.session_state.end_date.strftime('%d.%m.%Y')}"
        )
    elif "start_date" in st.session_state:
        st.info(f"📊 Daten ab: {st.session_state.start_date.strftime('%d.%m.%Y')}")

    st.write(f"Gesamtanzahl der Bestellungen: {total_orders}")
    st.write(f"Gesamtanzahl der bestellten Artikel: {total_items}")

# Add weather section to the sidebar
st.sidebar.title("Wetter in Heidelberg")
if st.sidebar.button("Aktuelles Wetter abrufen"):
    weather_data = get_heidelberg_weather()

    if "error" in weather_data:
        st.sidebar.error(weather_data["error"])
    else:
        st.sidebar.metric("Temperatur", f"{weather_data['temperature']}°C")
        st.sidebar.metric("Gefühlt wie", f"{weather_data['feels_like']}°C")
        st.sidebar.metric("Luftfeuchtigkeit", f"{weather_data['humidity']}%")
        st.sidebar.metric(
            "Windgeschwindigkeit", f"{weather_data['wind_speed']} m/s"
        )

        # Display weather icon if available
        icon_url = f"http://openweathermap.org/img/wn/{weather_data['icon']}@2x.png"
        st.sidebar.image(icon_url)

        st.sidebar.text(f"Beschreibung: {weather_data['description']}")
        st.sidebar.text(f"Letzte Aktualisierung: {weather_data['timestamp']}")
