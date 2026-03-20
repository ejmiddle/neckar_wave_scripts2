from __future__ import annotations

import glob
import json
import os
import sys
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any

from src.logging_config import logger

DRIVE_LIEFERSCHEIN_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/bmp",
    "image/gif",
    "application/pdf",
}
# Backwards-compatible alias for existing imports/usages.
DRIVE_IMAGE_MIME_TYPES = DRIVE_LIEFERSCHEIN_MIME_TYPES
DRIVE_SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"
DRIVE_DEFAULT_LIEFERSCHEINE_FOLDER_NAME = "Scan_Ausgangslieferscheine"
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
DRIVE_AUTH_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
DRIVE_CACHE_TTL_SECONDS = 300


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_str(source: Any, key: str) -> str | None:
    if source is None:
        return None
    try:
        value = source.get(key, None)
    except Exception:
        value = None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def coerce_drive_service_account_info(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None

    if Path(raw).exists():
        try:
            return json.loads(Path(raw).read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read drive service account file.")
            return None

    try:
        return json.loads(raw)
    except Exception:
        pass

    try:
        return json.loads(raw.replace("\\n", "\n"))
    except Exception:
        return None


def get_drive_service_account_info(
    *,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    env = environ or os.environ
    secret_keys = [
        "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON",
        "GOOGLE_DRIVE_SERVICE_ACCOUNT",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "GOOGLE_SERVICE_ACCOUNT",
    ]
    for key in secret_keys:
        parsed = coerce_drive_service_account_info(_read_str(secrets, key))
        if parsed:
            return parsed

    env_keys = [
        "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON",
        "GOOGLE_DRIVE_SERVICE_ACCOUNT",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "GOOGLE_SERVICE_ACCOUNT",
        "GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE",
    ]
    for key in env_keys:
        parsed = coerce_drive_service_account_info(_read_str(env, key))
        if parsed:
            return parsed
    return None


def get_drive_folder_id_default(
    *,
    session_state: Any = None,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    env = environ or os.environ
    keys = [
        "GOOGLE_DRIVE_LIEFERSCHEINE_FOLDER_ID",
        "GOOGLE_DRIVE_FOLDER_ID",
        "GOOGLE_DRIVE_LIEFERSCHEINE_FOLDER",
    ]
    for key in keys:
        return_value = (
            _read_str(session_state, key)
            or _read_str(secrets, key)
            or _read_str(env, key)
        )
        if return_value:
            return return_value
    return None


def get_drive_folder_name_default(
    *,
    session_state: Any = None,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    env = environ or os.environ
    keys = [
        "GOOGLE_DRIVE_LIEFERSCHEINE_FOLDER_NAME",
        "GOOGLE_DRIVE_FOLDER_NAME",
    ]
    for key in keys:
        return_value = (
            _read_str(session_state, key)
            or _read_str(secrets, key)
            or _read_str(env, key)
        )
        if return_value:
            return return_value
    return None


def normalize_drive_text(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\u00a0", " ").strip()


def _get_drive_client_secret_file(*, environ: Mapping[str, str] | None = None) -> str | None:
    env = environ or os.environ
    configured = _read_str(env, "GOOGLE_CLIENT_SECRET")
    if configured and Path(configured).exists():
        return configured
    if Path("credentials.json").exists():
        return "credentials.json"
    candidates = sorted(glob.glob("client_secret*.json"))
    if candidates:
        return candidates[0]
    return None


def _get_drive_token_file(*, environ: Mapping[str, str] | None = None) -> str:
    env = environ or os.environ
    configured = _read_str(env, "GOOGLE_DRIVE_TOKEN_FILE")
    if configured:
        return configured
    return "token.json"


def _get_drive_oauth_mode(*, environ: Mapping[str, str] | None = None) -> str:
    env = environ or os.environ
    configured = _read_str(env, "GOOGLE_DRIVE_OAUTH_MODE")
    if not configured:
        return "auto"
    mode = configured.strip().lower()
    if mode in {"auto", "local_server", "console"}:
        return mode
    logger.warning(
        "Unsupported GOOGLE_DRIVE_OAUTH_MODE='%s'. Falling back to 'auto'.",
        configured,
    )
    return "auto"


def _get_drive_force_reauth(*, environ: Mapping[str, str] | None = None) -> bool:
    env = environ or os.environ
    return _is_truthy(_read_str(env, "GOOGLE_DRIVE_FORCE_REAUTH"))


def _drive_credentials_have_required_scopes(creds: Any) -> bool:
    if creds is None:
        return False
    try:
        return bool(creds.has_scopes(DRIVE_AUTH_SCOPES))
    except Exception:
        pass
    active_scopes = getattr(creds, "scopes", None)
    if not active_scopes:
        return False
    active_set = {str(scope).strip() for scope in active_scopes if isinstance(scope, str)}
    required_set = {str(scope).strip() for scope in DRIVE_AUTH_SCOPES}
    return required_set.issubset(active_set)


def _token_file_has_required_scopes(token_file: str) -> bool:
    token_path = Path(token_file)
    if not token_path.exists():
        return False
    try:
        token_payload = json.loads(token_path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to parse token file for scope check: %s", token_file)
        return False

    raw_scopes = token_payload.get("scopes")
    if not isinstance(raw_scopes, list):
        return False

    scope_set = {str(scope).strip() for scope in raw_scopes if isinstance(scope, str)}
    required_set = {str(scope).strip() for scope in DRIVE_AUTH_SCOPES}
    return required_set.issubset(scope_set)


def build_drive_credentials(
    *,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
):
    env = environ or os.environ

    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_info = get_drive_service_account_info(secrets=secrets, environ=env)
    if creds_info:
        logger.info("Google Drive auth mode: service_account")
        return service_account.Credentials.from_service_account_info(
            creds_info, scopes=DRIVE_AUTH_SCOPES
        )

    token_file = _get_drive_token_file(environ=env)
    force_reauth = _get_drive_force_reauth(environ=env)
    oauth_mode = _get_drive_oauth_mode(environ=env)
    logger.info(
        "Google Drive auth init: token_file=%s force_reauth=%s oauth_mode=%s service_account_env_present=%s",
        token_file,
        force_reauth,
        oauth_mode,
        bool(get_drive_service_account_info(environ=env)),
    )
    token_scopes_ok = _token_file_has_required_scopes(token_file) if Path(token_file).exists() else False
    if force_reauth:
        logger.info(
            "Google OAuth force re-auth enabled. Ignoring existing token file: %s",
            token_file,
        )
    if Path(token_file).exists() and not token_scopes_ok:
        logger.warning(
            "Google OAuth token file is missing required scopes. Required=%s token_file=%s",
            DRIVE_AUTH_SCOPES,
            token_file,
        )
    elif not Path(token_file).exists():
        logger.info("Google OAuth token file not found: %s", token_file)
    creds = None
    if Path(token_file).exists() and not force_reauth:
        try:
            creds = Credentials.from_authorized_user_file(token_file, DRIVE_AUTH_SCOPES)
            logger.info("Google Drive auth mode: oauth_token (%s)", token_file)
        except Exception:
            logger.exception("Failed to load Google OAuth token file: %s", token_file)
            creds = None

    if creds and creds.valid and token_scopes_ok and _drive_credentials_have_required_scopes(creds):
        return creds
    if creds and creds.valid and not _drive_credentials_have_required_scopes(creds):
        logger.warning(
            "Google OAuth token is missing required scopes. Required=%s actual=%s",
            DRIVE_AUTH_SCOPES,
            getattr(creds, "scopes", None),
        )

    if (
        creds
        and creds.expired
        and creds.refresh_token
        and token_scopes_ok
        and _drive_credentials_have_required_scopes(creds)
    ):
        try:
            creds.refresh(Request())
            Path(token_file).write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            logger.exception("Failed to refresh Google OAuth token.")

    if oauth_mode == "auto" and not sys.stdin.isatty():
        oauth_mode = "console"
        logger.info("Auto OAuth mode switched to console due non-interactive input.")

    client_secret_file = _get_drive_client_secret_file(environ=env)
    if client_secret_file:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secret_file, DRIVE_AUTH_SCOPES
            )
            if oauth_mode == "console":
                creds = flow.run_console()
            else:
                try:
                    creds = flow.run_local_server(port=0, open_browser=False)
                except Exception:
                    if oauth_mode == "local_server":
                        raise
                    logger.warning(
                        "Google OAuth local_server flow failed. Falling back to console flow."
                    )
                    creds = flow.run_console()
            if not _drive_credentials_have_required_scopes(creds):
                raise RuntimeError(
                    "Google OAuth Token enthält nicht die benötigten Scopes für Datei-Downloads. "
                    f"Benötigt: {', '.join(DRIVE_AUTH_SCOPES)}"
                )
            Path(token_file).write_text(creds.to_json(), encoding="utf-8")
            logger.info(
                "Google Drive auth mode: oauth_%s (client_secret=%s token=%s)",
                oauth_mode,
                client_secret_file,
                token_file,
            )
            return creds
        except Exception:
            logger.exception("Google OAuth browser flow failed.")
    else:
        logger.error(
            "Google OAuth client secret not found. Checked GOOGLE_CLIENT_SECRET and client_secret*.json/credentials.json in working directory %s",
            os.getcwd(),
        )

    raise RuntimeError(
        "Google Drive Auth fehlt. Entweder Service Account in secrets/"
        "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON setzen oder OAuth-Dateien bereitstellen "
        "(token.json plus credentials.json/client_secret*.json)."
    )


def build_drive_service(
    *,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
):
    try:
        from googleapiclient.discovery import build
    except Exception as exc:
        raise RuntimeError(
            "Google Drive hängt von google-api-python-client + google-auth + "
            "google-auth-oauthlib ab. "
            "Bitte diese Pakete in der Umgebung installieren."
        ) from exc

    credentials = build_drive_credentials(secrets=secrets, environ=environ)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _find_drive_folder_id_by_name(*, service: Any, folder_name: str) -> str | None:
    normalized_name = normalize_drive_text(folder_name)
    if not normalized_name:
        return None

    folder_name_normalized = normalized_name.lower()
    matches: list[str] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"mimeType='{DRIVE_FOLDER_MIME_TYPE}' and trashed = false",
                fields="nextPageToken, files(id,name,mimeType)",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for folder in resp.get("files", []):
            folder_id = folder.get("id")
            folder_label = folder.get("name")
            if (
                folder_id
                and isinstance(folder_label, str)
                and folder_label.strip().lower() == folder_name_normalized
            ):
                matches.append(folder_id)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(
            f"Mehrere Ordner mit Namen '{normalized_name}' gefunden. Bitte die Ordner-ID verwenden."
        )
    return None


def _resolve_drive_folder_id_if_accessible(
    *,
    service: Any,
    folder_id: str,
) -> tuple[str, str] | None:
    normalized_folder_id = normalize_drive_text(folder_id)
    if not normalized_folder_id:
        return None

    try:
        folder = (
            service.files()
            .get(
                fileId=normalized_folder_id,
                fields="id,name,mimeType",
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception as exc:
        status_code = getattr(getattr(exc, "resp", None), "status", None)
        if status_code == 404:
            return None
        raise

    if not isinstance(folder, dict):
        return None

    if folder.get("mimeType") != DRIVE_FOLDER_MIME_TYPE:
        raise RuntimeError(
            f"Die angegebene Google Drive-ID ist kein Ordner: {normalized_folder_id}"
        )

    resolved_id = folder.get("id")
    if not isinstance(resolved_id, str) or not resolved_id.strip():
        return None

    folder_name = folder.get("name")
    if isinstance(folder_name, str) and folder_name.strip():
        return resolved_id, f"{folder_name.strip()} (ID: {resolved_id})"
    return resolved_id, resolved_id


def resolve_drive_folder_id(
    raw_folder_input: str,
    *,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str | None, str]:
    trimmed = normalize_drive_text(raw_folder_input)
    if not trimmed:
        return None, ""

    service = build_drive_service(secrets=secrets, environ=environ)
    resolved_by_id = _resolve_drive_folder_id_if_accessible(
        service=service,
        folder_id=trimmed,
    )
    if resolved_by_id:
        return resolved_by_id

    resolved_id = _find_drive_folder_id_by_name(service=service, folder_name=trimmed)
    if resolved_id:
        return resolved_id, f"{trimmed} (ID: {resolved_id})"
    return None, ""


def discover_drive_images(
    root_folder_id: str,
    *,
    recursive: bool = True,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    folder_id = root_folder_id.strip()
    if not folder_id:
        return []

    service = build_drive_service(secrets=secrets, environ=environ)
    image_query = " or ".join(
        f"mimeType='{mime_type}'" for mime_type in sorted(DRIVE_LIEFERSCHEIN_MIME_TYPES)
    )
    folder_queue: list[tuple[str, str]] = [(folder_id, "")]
    seen_folders: set[str] = set()
    images: list[dict[str, Any]] = []

    while folder_queue:
        current_folder_id, current_folder_name = folder_queue.pop(0)
        if current_folder_id in seen_folders:
            continue
        seen_folders.add(current_folder_id)

        if recursive:
            page_token = None
            while True:
                folder_resp = (
                    service.files()
                    .list(
                        q=f"'{current_folder_id}' in parents and mimeType='{DRIVE_FOLDER_MIME_TYPE}' and trashed = false",
                        fields="nextPageToken, files(id,name,mimeType)",
                        pageSize=1000,
                        pageToken=page_token,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute()
                )
                for folder in folder_resp.get("files", []):
                    folder_name = folder.get("name") or folder.get("id")
                    if not folder_name:
                        continue
                    child_folder_id = folder.get("id")
                    if not child_folder_id:
                        continue
                    child_path = (
                        folder_name
                        if not current_folder_name
                        else f"{current_folder_name}/{folder_name}"
                    )
                    folder_queue.append((child_folder_id, child_path))
                page_token = folder_resp.get("nextPageToken")
                if not page_token:
                    break

        page_token = None
        while True:
            image_resp = (
                service.files()
                .list(
                    q=(
                        f"'{current_folder_id}' in parents and "
                        f"(({image_query}) or mimeType='{DRIVE_SHORTCUT_MIME_TYPE}') and trashed = false"
                    ),
                    fields=(
                        "nextPageToken, "
                        "files(id,name,mimeType,shortcutDetails(targetId,targetMimeType))"
                    ),
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for file_entry in image_resp.get("files", []):
                file_id = file_entry.get("id")
                mime_type = str(file_entry.get("mimeType") or "").strip()
                if mime_type == DRIVE_SHORTCUT_MIME_TYPE:
                    shortcut_details = file_entry.get("shortcutDetails", {})
                    if not isinstance(shortcut_details, dict):
                        continue
                    target_mime_type = str(shortcut_details.get("targetMimeType") or "").strip()
                    target_id = shortcut_details.get("targetId")
                    if target_mime_type not in DRIVE_LIEFERSCHEIN_MIME_TYPES:
                        continue
                    if not isinstance(target_id, str) or not target_id.strip():
                        continue
                    file_id = target_id.strip()
                elif mime_type not in DRIVE_LIEFERSCHEIN_MIME_TYPES:
                    continue
                elif not file_id:
                    continue
                image_name = file_entry.get("name") or "lieferschein"
                images.append(
                    {
                        "kind": "drive",
                        "file_id": file_id,
                        "name": image_name,
                        "folder": current_folder_name,
                    }
                )
            page_token = image_resp.get("nextPageToken")
            if not page_token:
                break

    images.sort(key=lambda item: (str(item.get("folder", "")), str(item.get("name", ""))))
    return images


def download_drive_image_bytes(
    file_id: str,
    *,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    service = build_drive_service(secrets=secrets, environ=environ)
    buffer = BytesIO()
    request = service.files().get_media(
        fileId=file_id,
        supportsAllDrives=True,
    )
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    binary_payload = buffer.getvalue()
    if binary_payload:
        return binary_payload

    response = service.files().get(
        fileId=file_id,
        alt="media",
        supportsAllDrives=True,
    ).execute()
    if isinstance(response, bytes):
        return response
    if isinstance(response, bytearray):
        return bytes(response)
    if isinstance(response, str):
        return response.encode("utf-8")
    raise RuntimeError(
        f"Drive-Bild konnte nicht als Byte-Inhalt geladen werden (Typ: {type(response).__name__})."
    )
