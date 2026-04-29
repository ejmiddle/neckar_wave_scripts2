from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy_mittwald_service.py"
SPEC = importlib.util.spec_from_file_location("deploy_mittwald_service", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

build_env_subset = MODULE.build_env_subset
build_service_patch = MODULE.build_service_patch


def test_build_env_subset_requires_all_keys() -> None:
    all_values = {"OPENAI_API_KEY": "x", "SEVDESK_KEY": "y"}
    subset = build_env_subset(all_values, ["SEVDESK_KEY"])
    assert subset == {"SEVDESK_KEY": "y"}


def test_build_service_patch_merges_existing_environment() -> None:
    service = {
        "description": "Accounting",
        "serviceName": "accounting",
        "deploy": {"resources": {"limits": {"cpus": "0.5", "memory": "1gb"}}},
        "pendingState": {
            "image": "ghcr.io/ejmiddle/neckarwave-scripts-accounting:latest",
            "envs": {"STREAMLIT_APP_FILE": "apps/accounting.py", "OPENAI_API_KEY": "old"},
            "ports": ["8501/tcp"],
            "entrypoint": ["sh", "-c"],
        },
    }

    patch = build_service_patch(
        service,
        {"OPENAI_API_KEY": "new", "SEVDESK_KEY": "token"},
        image=None,
    )

    assert patch["image"] == "ghcr.io/ejmiddle/neckarwave-scripts-accounting:latest"
    assert patch["environment"] == {
        "STREAMLIT_APP_FILE": "apps/accounting.py",
        "OPENAI_API_KEY": "new",
        "SEVDESK_KEY": "token",
    }
    assert patch["ports"] == ["8501/tcp"]
    assert patch["entrypoint"] == ["sh", "-c"]
    assert patch["deploy"] == {"resources": {"limits": {"cpus": "0.5", "memory": "1gb"}}}
