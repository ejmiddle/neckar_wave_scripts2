import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_environment() -> None:
    """Load `.env` values into the process when available."""
    repo_env = REPO_ROOT / ".env"
    if not repo_env.exists():
        return

    load_dotenv(dotenv_path=repo_env, override=True)
    for key, value in dotenv_values(repo_env).items():
        if value is not None:
            os.environ.setdefault(key, value)
