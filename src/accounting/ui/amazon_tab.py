import streamlit as st

from src.accounting.ui.amazon_sections import (
    render_amazon_setup_section,
    render_booking_selection_section,
    render_processing_results_section,
)
from src.accounting.ui.displays import show_amazon_payments

def render_amazon_tab() -> None:
    st.divider()
    amazon_rows = render_amazon_setup_section()
    if amazon_rows is None:
        show_amazon_payments(None)
        return
    if not amazon_rows:
        show_amazon_payments([])
        return

    st.success(
        f"Loaded {len(amazon_rows)} matching Sparkasse bookings. "
        "Bookings with identical Amazon order numbers are grouped for joint processing."
    )
    selected_booking_rows = render_booking_selection_section(amazon_rows)

    render_processing_results_section(selected_booking_rows)
