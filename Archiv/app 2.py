import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from backend.settings import get_neunzehn_settings

# --- Custom help functions - use case specific
from backend.utils.logging import logger
from staging.agent19.common.analyze_rag_data import analyze_rag_data
from staging.config.config import (
    PathConfig,
    load_config,
)
from staging.config.global_vars import INDEXACCESS
from staging.frontend.streamlit_pt.frontend_functions import (
    initiate_authenticator,
    is_exploration_user,
    page_configuration,
    show_logo,
)
from staging.frontend.streamlit_pt.pages_components import (
    select_index_from_existing,
    select_output_options,
    show_current_agent_config,
    show_pages_based_on_user_type,
    update_agent_config_frontend,
    user_type_selection,
)

logger.info("Starting application")


@st.cache_resource
def get_neunzehn_settings_cached():
    return get_neunzehn_settings()


neunzehn_settings = get_neunzehn_settings_cached()

################################################################
### On first call   ###########################################
################################################################
if "init_landing" not in st.session_state:
    # path_config = update_user_dep_index_infos(username, config_def)
    agent_config, frontend_config = load_config()

    # write to session state on initialization
    st.session_state.init_landing = True
    st.session_state.agent_config = agent_config
    st.session_state.frontend_config = frontend_config

else:
    agent_config = st.session_state.agent_config
    frontend_config = st.session_state.frontend_config


globalPaths = PathConfig()
page_configuration(frontend_config, "Welcome and Login")
show_logo(frontend_config)

authenticator = (
    initiate_authenticator()
)  # TODO this triggers restart of streamlit, dont know why
try:
    authenticator.login()

    name = st.session_state["name"]
    username = st.session_state["username"]
    email = st.session_state["email"]

    logger.info(f"Logged in with username = {username}, name = {name} email = {email}")

except Exception as e:  # Corrected the syntax here to properly capture the exception
    st.info(f"... {e}")  # Added exception message for more clarity
    name = ""
    username = ""

# --- Show content only if successfully authenticated
if st.session_state["authentication_status"] is False:
    st.error("Benutzername/Passwort falsch")

elif st.session_state["authentication_status"]:
    st.header("Hello " + name)

    authenticator.logout("Logout", "main")
    if "selected_user_type" not in st.session_state:
        if name == "admin":
            selected_user_type = "Exploration"  # Always start as Exploration
        elif name == "exploration_user":
            selected_user_type = "Exploration"
        else:
            selected_user_type = "Standard_User"
        st.session_state.selected_user_type = selected_user_type

    if st.session_state.selected_user_type in ["Developer", "Exploration"]:
        if st.button("Lade Konfiguration neu"):
            del st.session_state.init_landing
            # del st.session_state.selected_user_type
        user_type_selection()

    show_pages_based_on_user_type(frontend_config)

    ################################################################
    ### Frontend adaptions of configs ##############################
    ################################################################

    agent_config["user_name"] = username
    if INDEXACCESS:
        agent_config = update_agent_config_frontend(
            widget_id="1", agent_config=agent_config
        )
        # --- Selection of index here

    show_current_agent_config(agent_config)

    # TODO ASC Think about values that are read from a vector dbs metadata
    # Problem is that we have to decide which variables characterize a vecDB: storage_id, vector_db_id, ...???
    if os.getenv("WITH_API_CALL", "").lower() not in ("1", "true", "yes"):
        if is_exploration_user():
            checked = st.toggle(
                "Prüfe ob bereits ein Index existiert und wähle eine existierenden"
            )
            if checked:
                agent_config = select_index_from_existing(agent_config)

    frontend_config["output_options"] = select_output_options(
        frontend_config["output_options"]
    )

    # --- Put all major configs to session state after possible modification
    st.session_state.agent_config = agent_config
    st.session_state.frontend_config = frontend_config

    if is_exploration_user():
        with st.expander("Zeige alle globalen Pfade:", expanded=False):
            st.write(globalPaths)

        with st.expander("Zeige alle agent_config", expanded=False):
            st.write(agent_config)

        with st.expander("Zeige alle frontend_config", expanded=False):
            st.write(frontend_config)

        with st.expander("Preprocessing functions", expanded=True):
            import importlib
            import inspect

            try:
                customer_id = globalPaths.customer_id
                preprocessing_module = importlib.import_module(
                    f"custom.{customer_id}.preprocessing_functions"
                )

                # Get all functions from the module
                preprocessing_functions = {}
                for name, obj in inspect.getmembers(preprocessing_module):
                    if inspect.isfunction(obj) and not name.startswith("_"):
                        preprocessing_functions[name] = obj

                preprocessing_functions_available = len(preprocessing_functions) > 0

                if preprocessing_functions_available:
                    logger.info(
                        f"Found preprocessing functions for customer {customer_id}: {list(preprocessing_functions.keys())}"
                    )

            except (ImportError, AttributeError) as e:
                logger.warning(
                    f"Could not import preprocessing functions for customer {globalPaths.customer_id}: {e}"
                )
                preprocessing_functions_available = False
                preprocessing_functions = {}

            if preprocessing_functions_available:
                # Create buttons for all available functions
                for func_name, func in preprocessing_functions.items():
                    # Create a user-friendly button label
                    button_label = func_name.replace("_", " ").title()

                    if st.button(f"Run: {button_label}"):
                        try:
                            result = func()

                            # Handle different return types
                            if isinstance(result, dict):
                                # If it returns a dict, display each key-value pair
                                for key, value in result.items():
                                    st.write(f"**{key.title()}:**")
                                    if isinstance(value, pd.DataFrame):
                                        st.write(value)
                                    else:
                                        st.write(value)
                            elif isinstance(result, pd.DataFrame):
                                st.write(result)
                            elif result is not None:
                                st.write(result)

                            st.success(f"Successfully executed: {button_label}")

                        except Exception as e:
                            st.error(f"Error executing {button_label}: {e}")
            else:
                st.info(
                    f"Preprocessing functions not available for customer: {globalPaths.customer_id}"
                )

            with st.expander("RAG Data Analysis", expanded=True):
                st.write("### Analyze RAG Data")

                # Get the data source ID from the agent config or use a default
                default_data_source_id = agent_config["idx_config"].get(
                    "data_source_id", "default"
                )

                # Add text input for manual data source ID entry
                data_source_id = st.text_input(
                    "Data Source ID:",
                    value=default_data_source_id,
                    help="Enter the folder name to analyze. This should be a subfolder in the document root.",
                )

                if st.button("Analyze RAG Data"):
                    try:
                        # Call the analyze_rag_data function
                        rag_data_df = analyze_rag_data(data_source_id)

                        if not rag_data_df.empty:
                            st.write(f"**Analysis Results for {data_source_id}:**")
                            st.write(f"Found {len(rag_data_df)} files")

                            # Display the dataframe
                            st.dataframe(rag_data_df)

                            # Show some statistics
                            if "file_size" in rag_data_df.columns:
                                total_size = rag_data_df["file_size"].sum()
                                avg_size = rag_data_df["file_size"].mean()
                                st.write(f"**Total size:** {total_size:,} bytes")
                                st.write(
                                    f"**Average file size:** {avg_size:,.0f} bytes"
                                )

                            if "file_extension" in rag_data_df.columns:
                                extension_counts = rag_data_df[
                                    "file_extension"
                                ].value_counts()
                                st.write("**File extensions:**")
                                st.write(extension_counts)

                        else:
                            st.warning(f"No data found for source: {data_source_id}")

                    except Exception as e:
                        st.error(f"Error analyzing RAG data: {e}")
                        logger.error(f"Error in analyze_rag_data: {e}")
