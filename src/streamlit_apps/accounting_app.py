from pathlib import Path

import streamlit as st

from src.logging_config import logger
from src.streamlit_apps.common import REPO_ROOT, load_environment


ACCOUNTING_NAV_SECTIONS = [
    (
        "Accounting",
        [
            {
                "path": str(REPO_ROOT / "pages/accounting_overview.py"),
                "title": "Overview",
                "icon": "🧮",
            },
            {
                "path": str(REPO_ROOT / "pages/accounting_belege.py"),
                "title": "Belegverwaltung",
                "icon": "🧾",
            },
            {
                "path": str(REPO_ROOT / "pages/accounting_rechnungen.py"),
                "title": "Rechnungsverwaltung",
                "icon": "📄",
            },
            {
                "path": str(REPO_ROOT / "pages/accounting_lieferscheine.py"),
                "title": "Lieferscheine",
                "icon": "🧾",
            },
            {
                "path": str(REPO_ROOT / "pages/accounting_payments.py"),
                "title": "Zahlungsverwaltung",
                "icon": "💸",
            },
            {
                "path": str(REPO_ROOT / "pages/accounting_amazon.py"),
                "title": "Receipts & Amazon",
                "icon": "📦",
            },
            {
                "path": str(REPO_ROOT / "pages/accounting_monthly_umsatz.py"),
                "title": "Monthly Umsatz",
                "icon": "📈",
            },
            {
                "path": str(REPO_ROOT / "pages/accounting_lohn_belege.py"),
                "title": "Lohn Belege",
                "icon": "🗂️",
            },
            {
                "path": str(REPO_ROOT / "pages/accounting_md.py"),
                "title": "Accounting MD",
                "icon": "🛠️",
            },
        ],
    ),
]


def _build_navigation_pages() -> dict[str, list[st.Page]]:
    return {
        section_title: [
            st.Page(page["path"], title=page["title"], icon=page["icon"])
            for page in section_pages
        ]
        for section_title, section_pages in ACCOUNTING_NAV_SECTIONS
    }


def _validate_accounting_pages() -> list[str]:
    issues: list[str] = []
    for _, section_pages in ACCOUNTING_NAV_SECTIONS:
        for page_info in section_pages:
            page_path = Path(page_info["path"])
            if not page_path.exists():
                issues.append(f"Missing page file: {page_info['path']}")
    return issues


def main() -> None:
    load_environment()
    st.set_page_config(
        page_title="Neckar Wave Accounting",
        page_icon="🧮",
        layout="wide",
    )

    logger.info("Starting app: Neckar Wave Accounting")

    issues = _validate_accounting_pages()
    if issues:
        logger.error("Accounting navigation issues detected: %s", issues)
        st.error("⚠️ Accounting page registration is incomplete. See terminal logs for details.")
        return

    nav_pages = _build_navigation_pages()
    if not nav_pages:
        st.sidebar.error("No accounting pages registered for navigation.")
        return

    nav = st.navigation(nav_pages, position="sidebar", expanded=True)
    nav.run()
