from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

UPLOAD_ARCHIVE_ROOT = Path("data/uploads")
METADATA_FILENAME = "metadata.json"


@dataclass(frozen=True)
class ArchivedFile:
    label: str
    original_name: str
    filename: str
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ArchivedUploadRun:
    workflow: str
    run_id: str
    run_dir: Path
    metadata_path: Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._") or "file"


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _write_file(run_dir: Path, label: str, original_name: str, data: bytes) -> ArchivedFile:
    filename = safe_filename(original_name)
    target = run_dir / filename
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        target = run_dir / f"{stem}_{sha256_bytes(data)[:8]}{suffix}"
        filename = target.name
    target.write_bytes(data)
    return ArchivedFile(
        label=label,
        original_name=original_name,
        filename=filename,
        path=str(target),
        size_bytes=len(data),
        sha256=sha256_bytes(data),
    )


def archive_upload_run(
    *,
    workflow: str,
    input_files: dict[str, tuple[str, bytes]],
    output_files: dict[str, tuple[str, bytes]] | None = None,
    summary: dict[str, Any] | None = None,
    status: str = "ok",
    error: str | None = None,
) -> ArchivedUploadRun:
    archive_root = UPLOAD_ARCHIVE_ROOT / safe_filename(workflow)
    archive_root.mkdir(parents=True, exist_ok=True)

    hash_seed = "".join(sha256_bytes(data) for _, data in input_files.values())
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_id = f"{timestamp}_{sha256_bytes(hash_seed.encode())[:8]}"
    run_dir = archive_root / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = archive_root / f"{run_id}_{suffix}"
    run_dir.mkdir(parents=True)

    archived_inputs = [
        _write_file(run_dir, label, original_name, data)
        for label, (original_name, data) in input_files.items()
    ]
    archived_outputs = [
        _write_file(run_dir, label, original_name, data)
        for label, (original_name, data) in (output_files or {}).items()
    ]

    metadata = {
        "workflow": workflow,
        "run_id": run_dir.name,
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "error": error or "",
        "inputs": [file.__dict__ for file in archived_inputs],
        "outputs": [file.__dict__ for file in archived_outputs],
        "summary": summary or {},
    }
    metadata_path = run_dir / METADATA_FILENAME
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return ArchivedUploadRun(
        workflow=workflow,
        run_id=run_dir.name,
        run_dir=run_dir,
        metadata_path=metadata_path,
    )


def load_upload_runs(root: Path = UPLOAD_ARCHIVE_ROOT) -> list[dict[str, Any]]:
    if not root.exists():
        return []

    runs: list[dict[str, Any]] = []
    for metadata_path in sorted(root.glob(f"*/*/{METADATA_FILENAME}"), reverse=True):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        metadata["_metadata_path"] = str(metadata_path)
        metadata["_run_dir"] = str(metadata_path.parent)
        runs.append(metadata)
    return runs
