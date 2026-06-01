from __future__ import annotations

import json

from src.accounting import upload_archive


def test_archive_upload_run_writes_files_and_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(upload_archive, "UPLOAD_ARCHIVE_ROOT", tmp_path / "uploads")

    archived = upload_archive.archive_upload_run(
        workflow="finom_open_payments",
        input_files={"open": ("transactions.csv", b"open-data")},
        output_files={"xlsx": ("result.xlsx", b"xlsx-data")},
        summary={"rows": 2},
        status="ok",
    )

    metadata = json.loads(archived.metadata_path.read_text(encoding="utf-8"))
    runs = upload_archive.load_upload_runs(tmp_path / "uploads")

    assert archived.run_dir.exists()
    assert (archived.run_dir / "transactions.csv").read_bytes() == b"open-data"
    assert (archived.run_dir / "result.xlsx").read_bytes() == b"xlsx-data"
    assert metadata["workflow"] == "finom_open_payments"
    assert metadata["status"] == "ok"
    assert metadata["summary"] == {"rows": 2}
    assert metadata["inputs"][0]["sha256"] == upload_archive.sha256_bytes(b"open-data")
    assert len(runs) == 1
    assert runs[0]["run_id"] == archived.run_id
