#!/usr/bin/env python3

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values, load_dotenv

API_BASE_URL = "https://api.mittwald.de/v2"
TIMEOUT_SECONDS = 30


@dataclass
class ProjectMatch:
    id: str
    short_id: str
    description: str
    status: str


@dataclass
class ServiceMatch:
    id: str
    service_name: str
    short_id: str
    stack_id: str


class MittwaldApiError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync env vars from the encrypted deploy config into a Mittwald service."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--project", required=True, help="Mittwald project selector.")
        subparser.add_argument("--service", required=True, help="Mittwald service/container name.")
        subparser.add_argument(
            "--env-file",
            default="config/secrets/production.enc.env",
            help="Encrypted dotenv file managed with SOPS.",
        )
        subparser.add_argument(
            "--env-keys-file",
            required=True,
            help="Text file listing env keys to sync into this service.",
        )

    sync_parser = subparsers.add_parser(
        "sync-secrets",
        help="Update the Mittwald service env vars from the encrypted file.",
    )
    add_common(sync_parser)
    sync_parser.add_argument(
        "--recreate",
        action="store_true",
        help="Recreate the service immediately so env var changes take effect.",
    )

    publish_parser = subparsers.add_parser(
        "publish",
        help="Update env vars and image reference, then pull the image and recreate the service.",
    )
    add_common(publish_parser)
    publish_parser.add_argument("--image", required=True, help="Container image reference.")

    return parser.parse_args()


def build_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
    )
    return session


def api_request(
    session: requests.Session,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    expect_json: bool = True,
) -> Any:
    response = session.request(
        method,
        f"{API_BASE_URL}{path}",
        params=params,
        json=json_body,
        timeout=TIMEOUT_SECONDS,
    )

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text.strip()
        if len(detail) > 500:
            detail = detail[:497] + "..."
        raise MittwaldApiError(
            f"Mittwald API request failed: {method} {path} -> "
            f"{response.status_code} {response.reason}. {detail}"
        ) from exc

    if not expect_json or response.status_code == 204:
        return None

    try:
        return response.json()
    except ValueError as exc:
        raise MittwaldApiError(
            f"Mittwald API returned non-JSON data for {method} {path}."
        ) from exc


def normalize(value: str) -> str:
    return value.strip().casefold()


def choose_project(projects: list[dict[str, Any]], target: str) -> ProjectMatch:
    if not projects:
        raise MittwaldApiError(f"No Mittwald project matched '{target}'.")

    wanted = normalize(target)
    exact_matches: list[dict[str, Any]] = []

    for project in projects:
        candidates = [
            str(project.get("description", "")),
            str(project.get("shortId", "")),
            str(project.get("id", "")),
        ]
        if any(normalize(candidate) == wanted for candidate in candidates if candidate):
            exact_matches.append(project)

    matches = exact_matches or projects
    if len(matches) > 1:
        formatted_matches = ", ".join(
            f"{item.get('description', '<unknown>')} [{item.get('shortId', '-')}]"
            for item in matches[:10]
        )
        raise MittwaldApiError(
            f"Project lookup for '{target}' is ambiguous. Matches: {formatted_matches}"
        )

    match = matches[0]
    return ProjectMatch(
        id=str(match["id"]),
        short_id=str(match.get("shortId", "-")),
        description=str(match.get("description", "")),
        status=str(match.get("status", "unknown")),
    )


def fetch_project(session: requests.Session, target: str) -> ProjectMatch:
    projects = api_request(
        session,
        "GET",
        "/projects",
        params={"searchTerm": target, "limit": 100},
    )
    if not isinstance(projects, list):
        raise MittwaldApiError("Unexpected project list response from Mittwald API.")
    return choose_project(projects, target)


def fetch_services(session: requests.Session, project_id: str) -> list[dict[str, Any]]:
    services = api_request(
        session,
        "GET",
        f"/projects/{project_id}/services",
        params={"limit": 200},
    )
    if not isinstance(services, list):
        raise MittwaldApiError("Unexpected services list response from Mittwald API.")
    return services


def choose_service(services: list[dict[str, Any]], target: str, stack_id: str) -> ServiceMatch:
    wanted = normalize(target)
    exact_matches = [
        service
        for service in services
        if normalize(str(service.get("serviceName", ""))) == wanted
        or normalize(str(service.get("shortId", ""))) == wanted
        or normalize(str(service.get("id", ""))) == wanted
    ]

    if not exact_matches:
        available = ", ".join(
            sorted(str(service.get("serviceName", "<unknown>")) for service in services)
        )
        raise MittwaldApiError(
            f"No Mittwald service matched '{target}'. Available services: {available}"
        )

    if len(exact_matches) > 1:
        matches = ", ".join(
            f"{service.get('serviceName', '<unknown>')} [{service.get('shortId', '-')}]"
            for service in exact_matches
        )
        raise MittwaldApiError(f"Service lookup for '{target}' is ambiguous. Matches: {matches}")

    match = exact_matches[0]
    return ServiceMatch(
        id=str(match["id"]),
        service_name=str(match.get("serviceName", "")),
        short_id=str(match.get("shortId", "-")),
        stack_id=stack_id,
    )


def fetch_service(
    session: requests.Session, stack_id: str, service_id: str
) -> dict[str, Any]:
    service = api_request(session, "GET", f"/stacks/{stack_id}/services/{service_id}")
    if not isinstance(service, dict):
        raise MittwaldApiError("Unexpected service response from Mittwald API.")
    return service


def decrypt_env_file(path: Path) -> dict[str, str]:
    env = os.environ.copy()
    if "SOPS_AGE_KEY_FILE" not in env and Path("secrets.keys").exists():
        env["SOPS_AGE_KEY_FILE"] = "./secrets.keys"

    process = subprocess.run(
        [
            "sops",
            "--decrypt",
            "--input-type",
            "dotenv",
            "--output-type",
            "dotenv",
            str(path),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "unknown sops error"
        raise RuntimeError(f"Failed to decrypt {path}: {detail}")

    values = dotenv_values(stream=io.StringIO(process.stdout))
    return {key: value for key, value in values.items() if value is not None}


def read_env_key_list(path: Path) -> list[str]:
    keys: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        keys.append(line)
    if not keys:
        raise RuntimeError(f"No env keys configured in {path}")
    return keys


def build_env_subset(all_values: dict[str, str], wanted_keys: list[str]) -> dict[str, str]:
    missing = [key for key in wanted_keys if key not in all_values]
    if missing:
        missing_keys = ", ".join(missing)
        raise RuntimeError(f"Encrypted env file is missing required keys: {missing_keys}")
    return {key: all_values[key] for key in wanted_keys}


def desired_state(service: dict[str, Any]) -> dict[str, Any]:
    pending_state = service.get("pendingState")
    if isinstance(pending_state, dict) and pending_state:
        return pending_state
    deployed_state = service.get("deployedState")
    if isinstance(deployed_state, dict):
        return deployed_state
    raise MittwaldApiError("Mittwald service response does not include a usable state payload.")


def build_service_patch(
    service: dict[str, Any], env_updates: dict[str, str], image: str | None
) -> dict[str, Any]:
    state = desired_state(service)
    current_env = state.get("envs")
    if not isinstance(current_env, dict):
        current_env = {}

    image_value = image or state.get("image")
    if not image_value:
        raise MittwaldApiError("Mittwald service payload did not include an image reference.")

    patch: dict[str, Any] = {
        "description": str(service.get("description", service.get("serviceName", ""))),
        "image": str(image_value),
        "environment": {**current_env, **env_updates},
    }

    for key in ("command", "entrypoint", "ports", "volumes"):
        value = state.get(key)
        if value:
            patch[key] = value

    deploy = service.get("deploy")
    if isinstance(deploy, dict) and deploy:
        patch["deploy"] = deploy

    return patch


def update_service(
    session: requests.Session,
    service: ServiceMatch,
    service_payload: dict[str, Any],
    *,
    recreate: bool,
) -> None:
    api_request(
        session,
        "PATCH",
        f"/stacks/{service.stack_id}",
        params={"recreate": "true" if recreate else "false"},
        json_body={"services": {service.service_name: service_payload}},
    )


def pull_image_and_recreate(session: requests.Session, service: ServiceMatch) -> None:
    api_request(
        session,
        "POST",
        f"/stacks/{service.stack_id}/services/{service.id}/actions/pull",
        expect_json=False,
    )


def resolve_target(
    session: requests.Session, project_selector: str, service_selector: str
) -> tuple[ProjectMatch, ServiceMatch, dict[str, Any]]:
    project = fetch_project(session, project_selector)
    stack_id = project.id
    services = fetch_services(session, project.id)
    service = choose_service(services, service_selector, stack_id)
    service_details = fetch_service(session, stack_id, service.id)
    return project, service, service_details


def sync_secrets(
    session: requests.Session,
    *,
    project_selector: str,
    service_selector: str,
    env_file: Path,
    env_keys_file: Path,
    recreate: bool,
    image: str | None,
) -> tuple[ProjectMatch, ServiceMatch, int]:
    decrypted_env = decrypt_env_file(env_file)
    env_keys = read_env_key_list(env_keys_file)
    env_subset = build_env_subset(decrypted_env, env_keys)

    project, service, service_details = resolve_target(session, project_selector, service_selector)
    service_payload = build_service_patch(service_details, env_subset, image)
    update_service(session, service, service_payload, recreate=recreate)
    return project, service, len(env_subset)


def main() -> int:
    load_dotenv()
    args = parse_args()

    token = os.getenv("MITTWALD_API_TOKEN")
    if not token:
        print("MITTWALD_API_TOKEN is not set.", file=sys.stderr)
        return 2

    session = build_session(token)

    try:
        if args.command == "sync-secrets":
            project, service, key_count = sync_secrets(
                session,
                project_selector=args.project,
                service_selector=args.service,
                env_file=Path(args.env_file),
                env_keys_file=Path(args.env_keys_file),
                recreate=args.recreate,
                image=None,
            )
            print(
                f"Synced {key_count} env vars to {service.service_name} "
                f"in project {project.description} [{project.short_id}]."
            )
            if args.recreate:
                print("Mittwald service recreate requested.")
            return 0

        if args.command == "publish":
            project, service, key_count = sync_secrets(
                session,
                project_selector=args.project,
                service_selector=args.service,
                env_file=Path(args.env_file),
                env_keys_file=Path(args.env_keys_file),
                recreate=False,
                image=args.image,
            )
            pull_image_and_recreate(session, service)
            print(
                f"Synced {key_count} env vars and updated image for {service.service_name} "
                f"in project {project.description} [{project.short_id}]."
            )
            print("Mittwald image pull and recreate requested.")
            return 0

        raise AssertionError(f"Unhandled command: {args.command}")
    except requests.RequestException as exc:
        print(f"Network error while calling Mittwald API: {exc}", file=sys.stderr)
        return 1
    except (MittwaldApiError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
