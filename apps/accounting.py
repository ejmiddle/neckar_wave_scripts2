import _bootstrap

_bootstrap.ensure_repo_root_on_path()

from src.streamlit_apps.accounting_app import main


if __name__ == "__main__":
    main()
