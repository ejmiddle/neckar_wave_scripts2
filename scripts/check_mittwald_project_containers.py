#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv

API_BASE_URL = "https://api.mittwald.de/v2"
TIMEOUT_SECONDS = 30


@dataclass
class ProjectMatch:
    id: str
    short_id: str
    description: str
    status: str


class MittwaldApiError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the status of all Mittwald containers/services in a project."
    )
    parser.add_argument(
        "--project",
        default="suedseite",
        help="Mittwald project name/description/short ID to resolve (default: %(default)s).",
    )
    parser.add_argument(
        "--fail-on-non-running",
        action="store_true",
        help="Exit with status 1 when the project or any service is not in a healthy running state.",
    )
    parser.add_argument(
        "--show-logs",
        action="store_true",
        help="Also print recent logs for unhealthy services.",
    )
    parser.add_argument(
        "--show-logs-for-all",
        action="store_true",
        help="Print recent logs for all services, not only unhealthy ones.",
    )
    parser.add_argument(
        "--log-tail",
        type=int,
        default=80,
        help="Number of log lines to request per service (default: %(default)s).",
    )
    parser.add_argument(
        "--only-unhealthy",
        action="store_true",
        help="Only print services that are not in running state.",
    )
    return parser.parse_args()


def build_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "X-Access-Token": token,
        }
    )
    return session


def api_get(session: requests.Session, path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{API_BASE_URL}{path}"
    response = session.get(url, params=params, timeout=TIMEOUT_SECONDS)

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text.strip()
        if len(detail) > 500:
            detail = detail[:497] + "..."
        raise MittwaldApiError(
            f"Mittwald API request failed: GET {path} -> {response.status_code} {response.reason}. "
            f"{detail}"
        ) from exc

    try:
        return response.json()
    except ValueError as exc:
        raise MittwaldApiError(f"Mittwald API returned non-JSON data for GET {path}.") from exc


def api_get_text(session: requests.Session, path: str, params: dict[str, Any] | None = None) -> str:
    url = f"{API_BASE_URL}{path}"
    response = session.get(url, params=params, timeout=TIMEOUT_SECONDS)

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text.strip()
        if len(detail) > 500:
            detail = detail[:497] + "..."
        raise MittwaldApiError(
            f"Mittwald API request failed: GET {path} -> {response.status_code} {response.reason}. "
            f"{detail}"
        ) from exc

    return response.text


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
    projects = api_get(
        session,
        "/projects",
        params={
            "searchTerm": target,
            "limit": 100,
        },
    )
    if not isinstance(projects, list):
        raise MittwaldApiError("Unexpected project list response from Mittwald API.")
    return choose_project(projects, target)


def fetch_services(session: requests.Session, project_id: str) -> list[dict[str, Any]]:
    services = api_get(
        session,
        f"/projects/{project_id}/services",
        params={"limit": 200},
    )
    if not isinstance(services, list):
        raise MittwaldApiError("Unexpected services list response from Mittwald API.")
    return services


def fetch_service_logs(
    session: requests.Session, stack_id: str, service_id: str, tail: int
) -> str:
    return api_get_text(
        session,
        f"/stacks/{stack_id}/services/{service_id}/logs",
        params={"tail": max(1, tail)},
    )


def format_timestamp(value: str | None) -> str:
    if not value:
        return "-"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def print_services(project: ProjectMatch, services: list[dict[str, Any]]) -> None:
    print(
        f"Mittwald project: {project.description} [{project.short_id}] "
        f"(status: {project.status})"
    )

    if not services:
        print("No services/containers found in this project.")
        return

    name_width = max(len("SERVICE"), *(len(str(item.get("serviceName", "-"))) for item in services))
    status_width = max(len("STATUS"), *(len(str(item.get("status", "-"))) for item in services))
    short_id_width = max(len("SHORT ID"), *(len(str(item.get("shortId", "-"))) for item in services))

    header = (
        f"{'SERVICE':<{name_width}}  "
        f"{'STATUS':<{status_width}}  "
        f"{'SHORT ID':<{short_id_width}}  "
        f"UPDATED                  MESSAGE"
    )
    print(header)
    print("-" * len(header))

    for service in services:
        service_name = str(service.get("serviceName", "-"))
        status = str(service.get("status", "-"))
        short_id = str(service.get("shortId", "-"))
        updated = format_timestamp(service.get("statusSetAt"))
        message = str(service.get("message", "")).strip() or "-"
        print(
            f"{service_name:<{name_width}}  "
            f"{status:<{status_width}}  "
            f"{short_id:<{short_id_width}}  "
            f"{updated:<24}  {message}"
        )


def filter_services(services: list[dict[str, Any]], only_unhealthy: bool) -> list[dict[str, Any]]:
    if not only_unhealthy:
        return services
    return [service for service in services if is_unhealthy_service(service)]


def has_unhealthy_services(project: ProjectMatch, services: list[dict[str, Any]]) -> bool:
    if project.status != "ready":
        return True
    return any(str(service.get("status")) != "running" for service in services)


def is_unhealthy_service(service: dict[str, Any]) -> bool:
    return str(service.get("status")) != "running"


def print_logs_for_services(
    session: requests.Session,
    project: ProjectMatch,
    services: list[dict[str, Any]],
    tail: int,
    include_all: bool,
) -> None:
    selected_services = services if include_all else [service for service in services if is_unhealthy_service(service)]

    if not selected_services:
        print("\nNo unhealthy services detected, so no logs were requested.")
        return

    for service in selected_services:
        service_name = str(service.get("serviceName", "-"))
        service_id = str(service.get("id", ""))
        status = str(service.get("status", "-"))
        print(f"\n=== Logs: {service_name} ({status}) ===")

        if not service_id:
            print("Service ID missing in API response; cannot fetch logs.")
            continue

        try:
            logs = fetch_service_logs(session, project.id, service_id, tail)
        except MittwaldApiError as exc:
            print(f"Failed to fetch logs: {exc}")
            continue

        logs = logs.rstrip()
        if logs:
            print(logs)
        else:
            print("(No log output returned.)")


def main() -> int:
    load_dotenv()
    args = parse_args()

    token = os.getenv("MITTWALD_API_TOKEN")
    if not token:
        print("MITTWALD_API_TOKEN is not set.", file=sys.stderr)
        return 2

    session = build_session(token)

    try:
        project = fetch_project(session, args.project)
        services = fetch_services(session, project.id)
    except requests.RequestException as exc:
        print(f"Network error while calling Mittwald API: {exc}", file=sys.stderr)
        return 1
    except MittwaldApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    visible_services = filter_services(services, args.only_unhealthy)
    print_services(project, visible_services)

    if args.show_logs or args.show_logs_for_all:
        print_logs_for_services(
            session=session,
            project=project,
            services=visible_services,
            tail=args.log_tail,
            include_all=args.show_logs_for_all,
        )

    if args.fail_on_non_running and has_unhealthy_services(project, services):
        print(
            "One or more services are not running, or the project is not ready.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
