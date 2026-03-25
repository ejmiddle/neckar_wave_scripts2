import streamlit as st

from src.accounting.ui.amazon_sections import (
    render_amazon_setup_section,
    render_booking_selection_section,
    render_extraction_results_section,
    render_pdf_matches_section,
    render_voucher_entries_section,
)
from src.accounting.ui.displays import show_amazon_payments


def _has_pdf_matches() -> bool:
    pdf_matches = st.session_state.get("sevdesk_sparkasse_amazon_pdf_matches")
    return isinstance(pdf_matches, list) and len(pdf_matches) > 0


def _has_extraction_result_for_selection(selected_booking_rows: list[dict]) -> bool:
    llm_result = st.session_state.get("sevdesk_sparkasse_amazon_llm_result")
    return bool(
        llm_result
        and len(selected_booking_rows) == 1
        and llm_result.get("bookingId") == str(selected_booking_rows[0].get("id", ""))
    )


def _has_voucher_entries_for_selection(selected_booking_rows: list[dict]) -> bool:
    voucher_payload_state = st.session_state.get("sevdesk_sparkasse_amazon_voucher_payload")
    return bool(
        voucher_payload_state
        and len(selected_booking_rows) == 1
        and voucher_payload_state.get("bookingId") == str(selected_booking_rows[0].get("id", ""))
    )


def render_amazon_tab() -> None:
    st.divider()
    llm_provider, extract_model, amazon_rows = render_amazon_setup_section()
    if amazon_rows is None:
        show_amazon_payments(None)
        return
    if not amazon_rows:
        show_amazon_payments([])
        return

    st.success(f"Loaded {len(amazon_rows)} matching Sparkasse bookings.")
    with st.expander("Bookings", expanded=True):
        selected_booking_rows = render_booking_selection_section(
            amazon_rows,
            llm_provider=llm_provider,
            extract_model=extract_model,
        )

    if _has_pdf_matches():
        with st.expander("PDF Matches", expanded=True):
            render_pdf_matches_section()

    if _has_extraction_result_for_selection(selected_booking_rows):
        with st.expander("Extraction", expanded=True):
            render_extraction_results_section(selected_booking_rows)

    if _has_voucher_entries_for_selection(selected_booking_rows):
        with st.expander("Voucher Creation", expanded=True):
            render_voucher_entries_section(selected_booking_rows)
