import os
from datetime import datetime
from pathlib import Path

import streamlit as st

from src.app_paths import BUCHHALTUNG_DIR, SCHICHTPLAN_DATA_DIR, TRINKGELD_DATA_DIR
from src.logging_config import logger

app_settings = st.session_state.get("app_settings", {})
app_version = app_settings.get("version", "unknown")

st.title("🔧 System Information")

st.write(f"**App Version:** {app_version}")
st.write(f"**Current Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
st.write(f"**Working Directory:** {os.getcwd()}")

pages_dir = Path(__file__).resolve().parents[1] / "pages"

required_dirs = [
    BUCHHALTUNG_DIR,
    SCHICHTPLAN_DATA_DIR,
    TRINKGELD_DATA_DIR,
    "Schichtplan",
    str(pages_dir),
]

st.write("**Required Directories:**")
for dir_name in required_dirs:
    if os.path.exists(dir_name):
        st.write(f"✅ {dir_name}")
    else:
        st.write(f"❌ {dir_name}")

logger.debug("Session state: %s", dict(st.session_state))
st.caption("Session state logged to terminal.")
