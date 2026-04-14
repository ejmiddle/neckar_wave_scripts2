from pathlib import Path

import streamlit as st

from src.app_paths import BUCHHALTUNG_DIR, SCHICHTPLAN_DATA_DIR, TRINKGELD_DATA_DIR
from src.logging_config import logger
from src.streamlit_apps.common import REPO_ROOT, load_environment


PRIMARY_PAGE = {
    "id": "shopify_qdrant",
    "path": str(REPO_ROOT / "pages/Shopify_Qdrant.py"),
    "title": "Shopify Bestellungen",
    "description": "Shopify order analysis and Qdrant demo tools",
    "icon": "🛒",
}

FEATURE_PAGES = [
    {
        "id": "buchhaltung",
        "path": str(REPO_ROOT / "pages/Buchhaltung.py"),
        "title": "Buchhaltung",
        "description": "Accounting and financial analysis tools",
        "icon": "📊",
    },
    {
        "id": "schichtplan_management",
        "path": str(REPO_ROOT / "pages/Schichtplan_Management.py"),
        "title": "Schichtplan Management",
        "description": "Employee and shift planning tools",
        "icon": "👥",
    },
    {
        "id": "trinkgeld_management",
        "path": str(REPO_ROOT / "pages/Trinkgeld_Management.py"),
        "title": "Trinkgeld Management",
        "description": "Tip distribution and calculation tools",
        "icon": "💰",
    },
    {
        "id": "quartal_eval",
        "path": str(REPO_ROOT / "pages/Quartal_Eval.py"),
        "title": "Quartal Eval",
        "description": "Quarterly evaluation and Gutschein analysis tools",
        "icon": "📈",
    },
    {
        "id": "order_erfassung",
        "path": str(REPO_ROOT / "pages/Order_Erfassung.py"),
        "title": "Order Erfassung",
        "description": "Order intake and processing tools",
        "icon": "🧾",
    },
    {
        "id": "api_image_tester",
        "path": str(REPO_ROOT / "pages/API_Image_Test.py"),
        "title": "API Image Test",
        "description": "Upload image and inspect API JSON response",
        "icon": "🧪",
    },
]

HOME_PAGE = {
    "id": "home",
    "path": str(REPO_ROOT / "pages/home.py"),
    "title": "Home",
    "description": "Overview and quick actions",
    "icon": "🏠",
}

SYSTEM_INFO_PAGE = {
    "id": "system_info",
    "path": str(REPO_ROOT / "pages/system_info.py"),
    "title": "System Info",
    "description": "Environment diagnostics",
    "icon": "🔧",
}

NAV_SECTIONS = [
    ("Shopify Bestellungen", [PRIMARY_PAGE]),
    ("Other Pages", [HOME_PAGE, *FEATURE_PAGES, SYSTEM_INFO_PAGE]),
]


def get_app_settings() -> dict:
    pages_by_id = {
        page["id"]: {
            "title": page["title"],
            "description": page["description"],
            "path": page["path"],
            "icon": page["icon"],
        }
        for page in [PRIMARY_PAGE, *FEATURE_PAGES]
    }
    return {
        "app_name": "Neckar Wave Operations",
        "version": "2.0.0",
        "pages": pages_by_id,
        "pages_list": list(pages_by_id.values()),
    }


def _init_session_state() -> None:
    st.session_state.app_settings = get_app_settings()
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = True


def _build_navigation_pages() -> dict[str, list[st.Page]]:
    return {
        section_title: [
            st.Page(page["path"], title=page["title"], icon=page["icon"])
            for page in section_pages
        ]
        for section_title, section_pages in NAV_SECTIONS
    }


def validate_environment() -> list[str]:
    issues: list[str] = []
    required_dirs = [BUCHHALTUNG_DIR, SCHICHTPLAN_DATA_DIR, TRINKGELD_DATA_DIR]

    for dir_name in required_dirs:
        if not dir_name.exists():
            issues.append(f"Missing directory: {dir_name}")

    for _, section_pages in NAV_SECTIONS:
        for page_info in section_pages:
            page_path = Path(page_info["path"])
            if not page_path.exists():
                issues.append(f"Missing page file: {page_info['path']}")

    if issues:
        for issue in issues:
            logger.warning("Environment issue: %s", issue)
    else:
        logger.info("Environment validation passed.")

    return issues


def main() -> None:
    load_environment()
    st.set_page_config(
        page_title="Neckar Wave Operations",
        page_icon="📊",
        layout="wide",
    )

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
