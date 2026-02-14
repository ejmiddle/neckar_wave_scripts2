from pathlib import Path

import streamlit as st

from src.app_paths import DATA_DIR
from src.logging_config import logger


def _test_data_mount() -> tuple[bool, str]:
    test_file = Path(DATA_DIR) / ".mount_test"
    logger.info("Data path: %s", test_file)
    try:
        if not Path(DATA_DIR).exists():
            return False, f"Data directory missing: {DATA_DIR}"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return True, f"Data directory is writable: {DATA_DIR}"
    except Exception as exc:
        return False, f"Data directory not writable: {DATA_DIR} ({exc})"


app_settings = st.session_state.get("app_settings", {})
app_name = app_settings.get("app_name", "Neckar Wave Management")
pages = app_settings.get("pages_list", [])

st.title(f"📊 {app_name}")

st.markdown(
    """
## Welcome to Neckar Wave Management System

This application provides tools for managing Neckar Wave's business operations across different areas based on your user permissions.

### Available Features:
"""
)

for page_info in pages:
    st.write(f"**{page_info['icon']} {page_info['title']}**")
    st.write(f"*{page_info['description']}*")
    st.write("---")

st.divider()
st.subheader("🚀 Quick Actions")

if pages:
    chunk_size = 6
    for start in range(0, len(pages), chunk_size):
        chunk = pages[start : start + chunk_size]
        cols = st.columns(len(chunk))
        for col, page_info in zip(cols, chunk):
            label = f"{page_info['icon']} Go to {page_info['title']}"
            with col:
                if st.button(label, width="stretch"):
                    st.switch_page(page_info["path"])

st.divider()
if st.button("🧪 Test data mount", width="stretch"):
    ok, message = _test_data_mount()
    if ok:
        st.success(message)
    else:
        st.error(message)
