# streamlit_app.py

import streamlit as st
import os
from datetime import datetime
from pathlib import Path

# Configuration
st.set_page_config(
    page_title="Neckar Wave Management",
    page_icon="📊",
    layout="wide"
)

# --- Configuration management (inspired by app 2.py)
@st.cache_resource
def get_app_settings():
    """Cache application settings."""
    return {
        "app_name": "Neckar Wave Management",
        "version": "1.0.0",
        "pages": {
            "buchhaltung": {
                "title": "📊 Buchhaltung",
                "description": "Accounting and financial analysis tools",
                "path": "pages/1_Buchhaltung.py",
                "icon": "📊"
            },
            "schichtplan": {
                "title": "👥 Schichtplan Management", 
                "description": "Employee and shift planning tools",
                "path": "pages/2_Schichtplan_Management.py",
                "icon": "👥"
            },
            "trinkgeld": {
                "title": "💰 Trinkgeld Management",
                "description": "Tip distribution and calculation tools",
                "path": "pages/3_Trinkgeld_Management.py",
                "icon": "💰"
            },
            "quartal_eval": {
                "title": "📈 Quartal Eval",
                "description": "Quarterly evaluation and Gutschein analysis tools",
                "path": "pages/4_Quartal_Eval.py",
                "icon": "📈"
            },
        }
    }

# --- Session state management (inspired by app 2.py)
if "init_landing" not in st.session_state:
    # Initialize application on first call
    st.session_state.init_landing = True
    st.session_state.app_settings = get_app_settings()

    st.session_state.current_page = "home"
    st.session_state.authenticated = True  # Simplified auth for now

# Get cached settings
app_settings = st.session_state.app_settings



# --- Page management functions (inspired by app 2.py)
def show_pages_based_on_user_type():
    """Display all available pages."""
    st.sidebar.header("📋 Available Pages")
    
    # Display all pages in a simple list
    for page_id, page_info in app_settings["pages"].items():
        if st.sidebar.button(
            f"{page_info['icon']} {page_info['title']}", 
            key=f"nav_{page_id}",
            use_container_width=True
        ):
            if page_info["path"]:
                st.switch_page(page_info["path"])
            else:
                st.session_state.current_page = page_id



# --- Main application function
def show_home_page():
    """Display the main home page with navigation."""
    st.title(f"📊 {app_settings['app_name']}")
    
    st.markdown("""
    ## Welcome to Neckar Wave Management System

    This application provides tools for managing Neckar Wave's business operations across different areas based on your user permissions.

    ### Available Features:
    """)
    
    # Show all available features
    for page_id, page_info in app_settings["pages"].items():
        st.write(f"**{page_info['icon']} {page_info['title']}**")
        st.write(f"*{page_info['description']}*")
        st.write("---")

def show_system_info_page():
    """Display system information page."""
    st.title("🔧 System Information")
    
    st.write(f"**App Version:** {app_settings['version']}")
    st.write(f"**Current Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    st.write(f"**Working Directory:** {os.getcwd()}")
    
    # Check if required directories exist
    required_dirs = ["buchhaltungsberichte", "Schichtplan", "Trinkgeld_Tabellen", "pages"]
    st.write("**Required Directories:**")
    for dir_name in required_dirs:
        if os.path.exists(dir_name):
            st.write(f"✅ {dir_name}")
        else:
            st.write(f"❌ {dir_name}")
    
    # Show session state info
    with st.expander("Session State Information", expanded=False):
        st.write(st.session_state)



def validate_environment():
    """Validate that the application environment is properly set up."""
    issues = []
    
    # Check for required directories
    required_dirs = ["buchhaltungsberichte", "Schichtplan", "Trinkgeld_Tabellen"]
    for dir_name in required_dirs:
        if not os.path.exists(dir_name):
            issues.append(f"Missing directory: {dir_name}")
    
    # Check for required pages
    for page_id, page_info in app_settings["pages"].items():
        if page_info["path"] and not os.path.exists(page_info["path"]):
            issues.append(f"Missing page file: {page_info['path']}")
    
    return issues

# --- Main application logic
def main():
    """Main application function with improved structure."""
    
    # Environment validation
    issues = validate_environment()
    if issues:
        st.error("⚠️ Environment Issues Detected:")
        for issue in issues:
            st.write(f"• {issue}")
        st.warning("Please ensure all required files and directories are present.")
        return
    
    # Sidebar navigation
    show_pages_based_on_user_type()
    
    # Main content area
    current_page = st.session_state.get("current_page", "home")
    
    if current_page == "home":
        show_home_page()
    elif current_page == "system_info":
        show_system_info_page()
    else:
        show_home_page()
    
    # Quick actions section (only on home page)
    if current_page == "home":
        st.divider()
        st.subheader("🚀 Quick Actions")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            if st.button("📊 Go to Buchhaltung", use_container_width=True):
                st.switch_page("pages/1_Buchhaltung.py")
        
        with col2:
            if st.button("👥 Go to Schichtplan Management", use_container_width=True):
                st.switch_page("pages/2_Schichtplan_Management.py")
        
        with col3:
            if st.button("💰 Go to Trinkgeld Management", use_container_width=True):
                st.switch_page("pages/3_Trinkgeld_Management.py")
        
        with col4:
            if st.button("📈 Go to Quartal Eval", use_container_width=True):
                st.switch_page("pages/4_Quartal_Eval.py")

# Run the main application
if __name__ == "__main__":
    main()

