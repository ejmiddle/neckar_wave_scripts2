import streamlit as st

from src.sevdesk.api import load_env_fallback
from src.streamlit_apps.common import REPO_ROOT

load_env_fallback()
st.switch_page(str(REPO_ROOT / "pages/accounting_md.py"))
