import os
from pathlib import Path

import streamlit as st
from dotenv import dotenv_values, load_dotenv

from src.app_paths import (
    BUCHHALTUNG_DIR,
    DATA_DIR,
    SCHICHTPLAN_DATA_DIR,
    TRINKGELD_DATA_DIR,
)
from src.logging_config import logger

BASE_DIR = Path(__file__).resolve().parent


def _load_environment() -> None:
    repo_env = BASE_DIR / ".env"
    if not repo_env.exists():
        return
    load_dotenv(dotenv_path=repo_env, override=True)
    # Ensure variables are available even if the process started without them.
    for key, value in dotenv_values(repo_env).items():
        if value is not None:
            os.environ.setdefault(key, value)


_load_environment()

# Configuration
st.set_page_config(
    page_title="Neckar Wave Management",
    page_icon="📊",
    layout="wide",
)

FEATURE_PAGES = [
    {
        "id": "buchhaltung",
        "path": "pages/1_Buchhaltung.py",
        "title": "Buchhaltung",
        "description": "Accounting and financial analysis tools",
        "icon": "📊",
    },
    {
        "id": "schichtplan_management",
        "path": "pages/2_Schichtplan_Management.py",
        "title": "Schichtplan Management",
        "description": "Employee and shift planning tools",
        "icon": "👥",
    },
    {
        "id": "trinkgeld_management",
        "path": "pages/3_Trinkgeld_Management.py",
        "title": "Trinkgeld Management",
        "description": "Tip distribution and calculation tools",
        "icon": "💰",
    },
    {
        "id": "quartal_eval",
        "path": "pages/4_Quartal_Eval.py",
        "title": "Quartal Eval",
        "description": "Quarterly evaluation and Gutschein analysis tools",
        "icon": "📈",
    },
    {
        "id": "order_erfassung",
        "path": "pages/5_Order_Erfassung.py",
        "title": "Order Erfassung",
        "description": "Order intake and processing tools",
        "icon": "🧾",
    },
    {
        "id": "shopify_qdrant",
        "path": "pages/6_Shopify_Qdrant.py",
        "title": "Shopify & Qdrant",
        "description": "Shopify order analysis and Qdrant demo tools",
        "icon": "🛒",
    },
]

NAV_PAGES = [
    {
        "id": "home",
        "path": "pages/home.py",
        "title": "Home",
        "description": "Overview and quick actions",
        "icon": "🏠",
    },
    *FEATURE_PAGES,
    {
        "id": "system_info",
        "path": "pages/system_info.py",
        "title": "System Info",
        "description": "Environment diagnostics",
        "icon": "🔧",
    },
]


@st.cache_resource
def get_app_settings() -> dict:
    """Cache application settings."""
    pages_by_id = {
        page["id"]: {
            "title": page["title"],
            "description": page["description"],
            "path": page["path"],
            "icon": page["icon"],
        }
        for page in FEATURE_PAGES
    }
    return {
        "app_name": "Neckar Wave Management",
        "version": "1.0.0",
        "pages": pages_by_id,
        "pages_list": list(pages_by_id.values()),
    }


def _init_session_state() -> None:
    if "app_settings" not in st.session_state:
        st.session_state.app_settings = get_app_settings()
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = True


def _build_navigation_pages() -> list[st.Page]:
    return [
        st.Page(page["path"], title=page["title"], icon=page["icon"])
        for page in NAV_PAGES
    ]


def validate_environment() -> list[str]:
    """Validate that the application environment is properly set up."""
    issues: list[str] = []

    required_dirs = [BUCHHALTUNG_DIR, SCHICHTPLAN_DATA_DIR, TRINKGELD_DATA_DIR]
    for dir_name in required_dirs:
        if not os.path.exists(dir_name):
            issues.append(f"Missing directory: {dir_name}")

    for page_info in NAV_PAGES:
        page_path = BASE_DIR / page_info["path"]
        if not page_path.exists():
            issues.append(f"Missing page file: {page_info['path']}")

    if issues:
        for issue in issues:
            logger.warning("Environment issue: %s", issue)
    else:
        logger.info("Environment validation passed.")

    return issues


def main() -> None:
    """Main application function with Streamlit navigation."""
    _init_session_state()
    app_settings = st.session_state.app_settings

    logger.info("Starting app: %s v%s", app_settings["app_name"], app_settings["version"])

    issues = validate_environment()
    if issues:
        logger.error("Environment issues detected: %s", issues)
        st.error("⚠️ Environment Issues Detected. See terminal logs for details.")
        st.warning("Please ensure all required files and directories are present.")
        return

    nav_pages = _build_navigation_pages()
    if not nav_pages:
        st.sidebar.error("No pages registered for navigation.")
        return
    nav = st.navigation(nav_pages, position="sidebar", expanded=True)
    nav.run()


if __name__ == "__main__":
    main()
