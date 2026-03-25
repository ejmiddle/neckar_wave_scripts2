import streamlit as st

from src.accounting.common import base_url, ensure_token, report_error
from src.accounting.master_data import load_stored_check_accounts
from src.accounting.ui.displays import show_transactions, show_vouchers
from src.sevdesk.api import fetch_latest_transactions_for_check_account, request_vouchers


def render_browse_tab() -> None:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Latest Belege")
        with st.form("sevdesk_latest_belege_form"):
            latest_limit = st.number_input("Voucher limit", min_value=1, max_value=100, value=10, step=1)
            latest_submit = st.form_submit_button("Load latest Belege", width="stretch")

        if latest_submit:
            token = ensure_token()
            if token:
                try:
                    st.session_state["sevdesk_latest_belege_rows"] = request_vouchers(
                        base_url(),
                        token,
                        int(latest_limit),
                    )
                except Exception as exc:
                    report_error(
                        f"Failed to load latest Belege: {exc}",
                        log_message="Failed to load latest Belege",
                        exc_info=True,
                    )

        show_vouchers(st.session_state.get("sevdesk_latest_belege_rows"))

    with col2:
        st.subheader("Bookings by Check Account")
        check_accounts_for_selection = st.session_state.get("sevdesk_check_accounts_rows")
        if check_accounts_for_selection is None:
            check_accounts_for_selection = load_stored_check_accounts()

        if check_accounts_for_selection:
            account_options = {
                f"{row.get('name', 'Unnamed')} ({row.get('id', '-')})": str(row.get("id", ""))
                for row in check_accounts_for_selection
            }
            selected_account_label = st.selectbox(
                "Check account",
                options=list(account_options.keys()),
            )
            transactions_limit = st.slider("Number of bookings", min_value=1, max_value=200, value=25)
            if st.button("Load latest bookings", width="stretch"):
                token = ensure_token()
                if token:
                    try:
                        st.session_state["sevdesk_check_account_transactions_rows"] = (
                            fetch_latest_transactions_for_check_account(
                                base_url(),
                                token,
                                account_options[selected_account_label],
                                transactions_limit,
                            )
                        )
                    except Exception as exc:
                        report_error(
                            f"Failed to load bookings: {exc}",
                            log_message="Failed to load bookings",
                            exc_info=True,
                        )
        else:
            st.info(
                "Fetch check accounts in the Accounting Backend page first so you can choose one here."
            )

        show_transactions(st.session_state.get("sevdesk_check_account_transactions_rows"))
