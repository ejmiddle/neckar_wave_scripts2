import streamlit as st

from src.accounting.page import render_accounting_app
from src.logging_config import logger
from src.streamlit_apps.common import load_environment


def main() -> None:
    load_environment()
    st.set_page_config(
        page_title="Neckar Wave Accounting",
        page_icon="🧮",
        layout="wide",
    )

    logger.info("Starting app: Neckar Wave Accounting")
    render_accounting_app()
