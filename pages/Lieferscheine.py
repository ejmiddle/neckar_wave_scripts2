import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

import pandas as pd
import streamlit as st

from src.app_paths import DATA_DIR
from src.drive_lieferscheine import (
    DRIVE_CACHE_TTL_SECONDS,
    DRIVE_DEFAULT_LIEFERSCHEINE_FOLDER_NAME,
    discover_drive_images,
    download_drive_image_bytes,
    get_drive_folder_id_default,
    get_drive_folder_name_default,
    normalize_drive_text,
    resolve_drive_folder_id,
)
from src.liefernscheine_prompt_config import (
    DEFAULT_SYSTEM_PROMPT,
    default_output_schema,
)
from src.lieferscheine_llm import (
    LLM_MODELS_BY_PROVIDER,
    LLM_PROVIDER_GOOGLE,
    LLM_PROVIDER_OPENAI,
    build_image_user_content,
    extract_lieferscheine_orders,
    resolve_llm_api_key,
)
from src.lieferscheine_orders import (
    normalize_orders_for_json,
    orders_to_editor_df,
    to_orders_payload,
)
from src.lieferscheine_sources import (
    LIEFERSCHEINE_DIR,
    SUPPORTED_LIEFERSCHEIN_EXTENSIONS,
    discover_local_lieferschein_images,
    read_lieferschein_image_bytes,
    split_pdf_bytes_to_page_images,
)
from src.logging_config import logger

_PAGE_SESSION_STATE_KEYS = {
    "orders_json",
    "orders_output_template",
    "orders_editor",
    "lieferscheine_preprocessed_dir",
}
LIEFERSCHEINE_EXTRACTIONS_DIR = DATA_DIR / "lieferscheine_extract"
LIEFERSCHEINE_PREPROCESSED_DIR = DATA_DIR / "lieferscheine_preprocessed"


def _reset_lieferscheine_page_state() -> None:
    for key in _PAGE_SESSION_STATE_KEYS:
        st.session_state.pop(key, None)


def _slug_for_filename(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    slug = slug.strip("._-")
    return slug or "unknown"


def _write_extraction_artifacts(
    *,
    source: str,
    provider: str,
    model: str,
    available_images: list[dict[str, Any]],
    attempted_inputs: int,
    extracted_rows: list[dict[str, Any]],
    failures: list[str],
    duration_seconds: float,
) -> tuple[Path, Path]:
    run_timestamp_utc = datetime.now(timezone.utc)
    run_stamp = run_timestamp_utc.strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{run_stamp}__{_slug_for_filename(provider)}__{_slug_for_filename(model)}"
    LIEFERSCHEINE_EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)

    json_path = LIEFERSCHEINE_EXTRACTIONS_DIR / f"run_{run_id}.json"
    csv_path = LIEFERSCHEINE_EXTRACTIONS_DIR / f"run_{run_id}.csv"

    normalized_rows = normalize_orders_for_json(extracted_rows)
    run_payload = {
        "run_id": run_id,
        "saved_at_utc": run_timestamp_utc.isoformat(),
        "source": source,
        "provider": provider,
        "model": model,
        "duration_seconds": round(duration_seconds, 3),
        "total_files": len(available_images),
        "attempted_inputs": attempted_inputs,
        "extracted_rows_count": len(normalized_rows),
        "failure_count": len(failures),
        "files": [
            {
                "name": str(item.get("name") or ""),
                "folder": str(item.get("folder") or ""),
                "kind": str(item.get("kind") or ""),
            }
            for item in available_images
        ],
        "failures": failures,
        "orders": normalized_rows,
    }
    json_path.write_text(json.dumps(run_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    csv_columns = ["folder", "customer", "product", "no_items", "date"]
    csv_rows = [
        {
            "folder": row.get("folder", ""),
            "customer": row.get("customer", ""),
            "product": row.get("product", ""),
            "no_items": row.get("no_items", 1),
            "date": row.get("date", ""),
        }
        for row in normalized_rows
    ]
    pd.DataFrame(csv_rows, columns=csv_columns).to_csv(csv_path, index=False)
    return json_path, csv_path


def _orders_to_excel_bytes(orders: list[dict[str, Any]]) -> bytes:
    normalized_orders = normalize_orders_for_json(orders)
    detail_columns = ["folder", "customer", "product", "no_items", "date"]
    detail_rows = [
        {
            "folder": row.get("folder", ""),
            "customer": row.get("customer", ""),
            "product": row.get("product", ""),
            "no_items": row.get("no_items", 1),
            "date": row.get("date", ""),
        }
        for row in normalized_orders
    ]
    summary_df = _summarize_orders_by_folder_product(normalized_orders)
    output = BytesIO()
    with pd.ExcelWriter(output) as writer:
        pd.DataFrame(detail_rows, columns=detail_columns).to_excel(
            writer,
            index=False,
            sheet_name="orders",
        )
        summary_df.to_excel(
            writer,
            index=False,
            sheet_name="summary_folder_product",
        )
    return output.getvalue()


def _summarize_orders_by_folder_product(orders: list[dict[str, Any]]) -> pd.DataFrame:
    normalized_orders = normalize_orders_for_json(orders)
    rows = [
        {
            "folder": row.get("folder", ""),
            "product": row.get("product", ""),
            "no_items": row.get("no_items", 1),
        }
        for row in normalized_orders
    ]
    if not rows:
        return pd.DataFrame(columns=["folder", "product", "total_no_items", "positions"])
    df = pd.DataFrame(rows)
    df["no_items"] = pd.to_numeric(df["no_items"], errors="coerce").fillna(0)
    grouped = (
        df.groupby(["folder", "product"], dropna=False, as_index=False)
        .agg(total_no_items=("no_items", "sum"), positions=("no_items", "size"))
        .sort_values(["folder", "product"])
    )
    grouped["total_no_items"] = grouped["total_no_items"].astype(int)
    grouped["positions"] = grouped["positions"].astype(int)
    return grouped


def _preprocessed_output_dir() -> Path:
    return LIEFERSCHEINE_PREPROCESSED_DIR


def _clean_preprocessed_output_dir(base_dir: Path) -> None:
    if base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)


def _safe_relative_folder(folder: str) -> Path:
    normalized = folder.replace("\\", "/").strip()
    if normalized in {"", ".", "/"}:
        return Path()
    safe_parts: list[str] = []
    for part in PurePosixPath(normalized).parts:
        if part in {"", ".", "..", "/"}:
            continue
        safe_parts.append(part)
    if not safe_parts:
        return Path()
    return Path(*safe_parts)


def _discover_preprocessed_inputs(base_dir: Path) -> list[dict[str, Any]]:
    image_extensions = {
        extension
        for extension in SUPPORTED_LIEFERSCHEIN_EXTENSIONS
        if extension != ".pdf"
    }
    return discover_local_lieferschein_images(
        base_dir=base_dir,
        supported_extensions=image_extensions,
    )


def _preprocess_files_to_output_dir(
    *,
    available_images: list[dict[str, Any]],
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    copied_image_count = 0
    skipped_existing_image_count = 0
    generated_page_image_count = 0
    skipped_existing_page_image_count = 0
    processed_pdf_count = 0
    failed_files: list[str] = []
    logger.info(
        "Input preprocessing started files=%s output_dir=%s overwrite=%s",
        len(available_images),
        output_dir,
        overwrite,
    )

    for item in available_images:
        source_name = str(item.get("name") or "unbekannt")
        folder = str(item.get("folder") or ".")
        item_kind = str(item.get("kind") or "")
        file_started = time.perf_counter()
        folder_output_dir = output_dir / _safe_relative_folder(folder)
        folder_output_dir.mkdir(parents=True, exist_ok=True)
        try:
            source_bytes, image_name, _ = read_lieferschein_image_bytes(
                item,
                download_drive_image_bytes=_download_drive_image_bytes_cached,
            )
            lower_name = image_name.lower()
            if lower_name.endswith(".pdf"):
                page_images = split_pdf_bytes_to_page_images(source_bytes, pdf_name=image_name)
                item_discriminator = (
                    str(item.get("file_id") or "").strip()
                    if item_kind == "drive"
                    else str(item.get("path") or "").strip()
                )
                safe_discriminator = _slug_for_filename(item_discriminator)[:24] or "source"
                for page_bytes, page_name in page_images:
                    page_suffix = Path(page_name).suffix or ".jpg"
                    page_stem = _slug_for_filename(Path(page_name).stem)
                    if not page_stem:
                        page_stem = f"{_slug_for_filename(Path(image_name).stem)}_seite"
                    unique_page_name = f"{page_stem}__{safe_discriminator}{page_suffix}"
                    page_output_path = folder_output_dir / unique_page_name
                    if page_output_path.exists() and not overwrite:
                        skipped_existing_page_image_count += 1
                        continue
                    page_output_path.write_bytes(page_bytes)
                    generated_page_image_count += 1
                processed_pdf_count += 1
                logger.info(
                    (
                        "Input preprocessing PDF finished name=%s pages=%s folder=%s "
                        "duration_s=%.3f"
                    ),
                    source_name,
                    len(page_images),
                    folder,
                    time.perf_counter() - file_started,
                )
                continue

            image_output_path = folder_output_dir / image_name
            if image_output_path.exists() and not overwrite:
                skipped_existing_image_count += 1
                continue
            image_output_path.write_bytes(source_bytes)
            copied_image_count += 1
            logger.info(
                "Input preprocessing image copied name=%s folder=%s duration_s=%.3f",
                source_name,
                folder,
                time.perf_counter() - file_started,
            )
        except Exception as exc:
            failed_files.append(f"{source_name}: {exc}")
            logger.exception(
                "Input preprocessing failed name=%s kind=%s folder=%s",
                source_name,
                item_kind,
                folder,
            )

    duration_seconds = time.perf_counter() - started
    logger.info(
        (
            "Input preprocessing finished files=%s processed_pdfs=%s copied_images=%s "
            "generated_page_images=%s skipped_images=%s skipped_pages=%s "
            "failures=%s duration_s=%.3f output_dir=%s"
        ),
        len(available_images),
        processed_pdf_count,
        copied_image_count,
        generated_page_image_count,
        skipped_existing_image_count,
        skipped_existing_page_image_count,
        len(failed_files),
        duration_seconds,
        output_dir,
    )
    return {
        "base_dir": str(output_dir),
        "file_count": len(available_images),
        "processed_pdf_count": processed_pdf_count,
        "copied_image_count": copied_image_count,
        "generated_page_image_count": generated_page_image_count,
        "skipped_existing_image_count": skipped_existing_image_count,
        "skipped_existing_page_image_count": skipped_existing_page_image_count,
        "failed_files": failed_files,
        "duration_seconds": duration_seconds,
    }


st.title("🧾 Lieferscheine erfassen")
if st.button("Reset Ergebnisse", width="stretch"):
    _reset_lieferscheine_page_state()

@st.cache_data(ttl=DRIVE_CACHE_TTL_SECONDS, show_spinner=False)
def _discover_drive_images_cached(
    root_folder_id: str,
    recursive: bool = True,
    scan_signature: str = "v2_include_pdfs_and_shortcuts",
) -> list[dict[str, Any]]:
    del scan_signature  # Cache-busting knob for discovery behavior changes.
    started = time.perf_counter()
    logger.info(
        "Drive discovery cache miss folder_id=%s recursive=%s",
        root_folder_id,
        recursive,
    )
    files = discover_drive_images(
        root_folder_id,
        recursive=recursive,
        secrets=st.secrets,
        environ=os.environ,
    )
    logger.info(
        "Drive discovery cache fill folder_id=%s files=%s duration_s=%.3f",
        root_folder_id,
        len(files),
        time.perf_counter() - started,
    )
    return files


@st.cache_data(ttl=DRIVE_CACHE_TTL_SECONDS, show_spinner=False)
def _download_drive_image_bytes_cached(file_id: str) -> bytes:
    started = time.perf_counter()
    logger.info("Drive download cache miss file_id=%s", file_id)
    payload = download_drive_image_bytes(
        file_id,
        secrets=st.secrets,
        environ=os.environ,
    )
    logger.info(
        "Drive download cache fill file_id=%s bytes=%s duration_s=%.3f",
        file_id,
        len(payload),
        time.perf_counter() - started,
    )
    return payload


st.divider()
st.subheader("Lieferscheine aus Dateien extrahieren")
default_drive_folder = (
    get_drive_folder_id_default(
        session_state=st.session_state,
        secrets=st.secrets,
        environ=os.environ,
    )
    or get_drive_folder_name_default(
        session_state=st.session_state,
        secrets=st.secrets,
        environ=os.environ,
    )
    or DRIVE_DEFAULT_LIEFERSCHEINE_FOLDER_NAME
)
image_source = st.radio(
    "Bildquelle",
    ("Lokal (data/lieferscheine)", "Google Drive"),
    index=1,
    horizontal=True,
)

if image_source == "Google Drive":
    logger.info("Lieferscheine page source selected source=drive")
    drive_folder_id = normalize_drive_text(
        st.text_input(
            "Google Drive Ordner-ID oder Ordnername",
            value=default_drive_folder or "",
            key="lieferscheine_drive_folder_id",
            help="Ordner-ID oder exakter Ordnername. Beispiel: Scan von Ausgangslieferschein",
        )
    )
    drive_recursive = st.toggle("Unterordner rekursiv durchsuchen", value=True, key="lieferscheine_drive_recursive")
    refresh_drive_cache = st.button(
        "Drive-Cache aktualisieren",
        key="lieferscheine_drive_refresh_cache",
        help="Leert den Google-Drive-Datei- und Download-Cache und lädt neu.",
    )
    if refresh_drive_cache:
        _discover_drive_images_cached.clear()
        _download_drive_image_bytes_cached.clear()
        st.info("Google-Drive-Cache wurde geleert.")
    if not drive_folder_id.strip():
        st.warning("Bitte eine Google Drive Ordner-ID oder den Ordnernamen angeben.")
        available_images: list[dict[str, Any]] = []
    else:
        try:
            logger.info(
                "Resolving drive folder input=%s recursive=%s",
                drive_folder_id.strip(),
                drive_recursive,
            )
            resolved_folder_id, resolved_label = resolve_drive_folder_id(
                drive_folder_id.strip(),
                secrets=st.secrets,
                environ=os.environ,
            )
            if not resolved_folder_id:
                st.info(f"Hinweis: Google Drive Ordner nicht gefunden: {drive_folder_id.strip()}")
                logger.warning(
                    "Drive folder not found: %s (recursive=%s)",
                    drive_folder_id.strip(),
                    drive_recursive,
                )
                available_images = []
            else:
                if resolved_label:
                    st.caption(f"Drive-Quelle: {resolved_label}")
                logger.info(
                    "Discovering drive files folder_id=%s recursive=%s",
                    resolved_folder_id,
                    drive_recursive,
                )
                available_images = _discover_drive_images_cached(
                    resolved_folder_id,
                    recursive=drive_recursive,
                    scan_signature="v2_include_pdfs_and_shortcuts",
                )
                logger.info(
                    "Drive discovery completed files=%s folder_id=%s",
                    len(available_images),
                    resolved_folder_id,
                )
        except RuntimeError as exc:
            logger.warning(
                "Drive folder note in page context: folder=%s recursive=%s error=%s",
                drive_folder_id.strip(),
                drive_recursive,
                exc,
            )
            st.info(f"Hinweis: {exc}")
            available_images = []
        except Exception as exc:
            logger.exception(
                "Google Drive failed in page context: folder=%s recursive=%s",
                drive_folder_id.strip(),
                drive_recursive,
            )
            st.error(f"Google Drive konnte nicht geladen werden: {exc}")
            available_images = []
else:
    logger.info("Lieferscheine page source selected source=local path=%s", LIEFERSCHEINE_DIR)
    available_images = discover_local_lieferschein_images()
    logger.info("Local discovery completed files=%s path=%s", len(available_images), LIEFERSCHEINE_DIR)

source_available_images = sorted(
    available_images,
    key=lambda item: (
        str(item.get("folder") or "."),
        str(item.get("name") or ""),
    ),
)
available_images = source_available_images

is_using_preprocessed_inputs = False
preprocessed_base_dir: Path | None = None
stored_preprocessed_dir = str(st.session_state.get("lieferscheine_preprocessed_dir") or "").strip()
if not stored_preprocessed_dir:
    stored_preprocessed_dir = str(_preprocessed_output_dir())
candidate_dir = Path(stored_preprocessed_dir)
preprocessed_inputs: list[dict[str, Any]] = []
if candidate_dir.exists():
    preprocessed_inputs = _discover_preprocessed_inputs(candidate_dir)
    if preprocessed_inputs and image_source != "Google Drive":
        available_images = sorted(
            preprocessed_inputs,
            key=lambda item: (
                str(item.get("folder") or "."),
                str(item.get("name") or ""),
            ),
        )
        is_using_preprocessed_inputs = True
        preprocessed_base_dir = candidate_dir
    elif preprocessed_inputs and image_source == "Google Drive":
        logger.info(
            "Preprocessed inputs exist but are ignored for live Google Drive source path=%s",
            candidate_dir,
        )
preprocess_col, clean_preprocess_col = st.columns(2)
preprocess_pdfs_clicked = preprocess_col.button(
    "Preprocess Dateien",
    help=(
        "Konvertiert PDF-Seiten in Bilder und kopiert vorhandene Bilddateien in einen "
        "gemeinsamen Preprocessing-Ordner. Die Extraktion kann danach direkt aus diesem Ordner laufen."
    ),
)
clean_preprocess_clicked = clean_preprocess_col.button(
    "Clean preprocess data",
    help="Loescht den kompletten Inhalt von data/lieferscheine_preprocessed und erstellt den Ordner leer neu.",
)

if clean_preprocess_clicked:
    try:
        output_dir = _preprocessed_output_dir()
        with st.spinner("Leere Preprocessing-Ordner..."):
            _clean_preprocessed_output_dir(output_dir)
        st.session_state["lieferscheine_preprocessed_dir"] = str(output_dir)
        available_images = source_available_images
        is_using_preprocessed_inputs = False
        preprocessed_base_dir = None
        st.success(f"Preprocessing-Daten wurden geloescht: {output_dir}")
    except Exception as exc:
        logger.exception("Cleaning preprocessing output failed path=%s", _preprocessed_output_dir())
        st.error(f"Clean preprocess data fehlgeschlagen: {exc}")

if preprocess_pdfs_clicked:
    try:
        output_dir = _preprocessed_output_dir()
        with st.spinner("Preprocess läuft: PDFs konvertieren und Bilder kopieren..."):
            preprocess_report = _preprocess_files_to_output_dir(
                available_images=source_available_images,
                output_dir=output_dir,
            )

        preprocess_base_dir = str(preprocess_report.get("base_dir") or "").strip()
        if preprocess_base_dir:
            st.session_state["lieferscheine_preprocessed_dir"] = preprocess_base_dir
            discovered_preprocessed = _discover_preprocessed_inputs(Path(preprocess_base_dir))
            available_images = sorted(
                discovered_preprocessed,
                key=lambda item: (
                    str(item.get("folder") or "."),
                    str(item.get("name") or ""),
                ),
            )
            preprocessed_base_dir = Path(preprocess_base_dir)
            is_using_preprocessed_inputs = True

        st.success(
            "Preprocessing abgeschlossen: "
            f"{preprocess_report.get('processed_pdf_count', 0)} PDFs konvertiert, "
            f"{preprocess_report.get('generated_page_image_count', 0)} Seitenbilder erzeugt, "
            f"{preprocess_report.get('copied_image_count', 0)} Bilddateien kopiert."
        )
        skipped_images = int(preprocess_report.get("skipped_existing_image_count", 0))
        skipped_pages = int(preprocess_report.get("skipped_existing_page_image_count", 0))
        if skipped_images or skipped_pages:
            st.info(
                f"{skipped_images} Bilder und {skipped_pages} Seitenbilder wurden uebersprungen "
                "(bereits vorhanden)."
            )
        if preprocess_base_dir:
            st.caption(f"Preprocessing Ausgabe: {preprocess_base_dir}")
        failed_files = preprocess_report.get("failed_files", [])
        if isinstance(failed_files, list) and failed_files:
            st.warning(f"{len(failed_files)} Dateien konnten nicht verarbeitet werden.")
            with st.expander("Preprocessing Fehler"):
                for failure in failed_files:
                    st.write(str(failure))
    except Exception as exc:
        logger.exception("Input preprocessing failed source=%s", image_source)
        st.error(f"Preprocessing fehlgeschlagen: {exc}")

if available_images:
    if is_using_preprocessed_inputs and preprocessed_base_dir:
        st.caption(f"{len(available_images)} Bilder aus Preprocessing-Ordner: {preprocessed_base_dir}")
    elif image_source == "Google Drive":
        st.caption(f"{len(available_images)} Dateien (Bilder + PDFs) in Google Drive gefunden.")
        if preprocessed_inputs:
            st.caption(
                "Vorhandene Preprocessing-Daten werden bei Google Drive nicht automatisch verwendet."
            )
    else:
        st.caption(f"{len(available_images)} Dateien (Bilder + PDFs) gefunden in: {LIEFERSCHEINE_DIR}")

    with st.expander("Gefundene Dateien", expanded=False):
        files_by_folder: dict[str, list[str]] = {}
        for item in available_images:
            folder = str(item.get("folder") or ".")
            files_by_folder.setdefault(folder, []).append(str(item.get("name") or ""))

        for folder in sorted(files_by_folder):
            st.markdown(f"**Ordner: `{folder}`**")
            st.dataframe(
                pd.DataFrame({"Dateiname": sorted(files_by_folder[folder])}),
                hide_index=True,
                width="stretch",
            )
else:
    if is_using_preprocessed_inputs and preprocessed_base_dir:
        st.warning(f"Keine Bilder im Preprocessing-Ordner gefunden: {preprocessed_base_dir}")
    elif image_source == "Google Drive":
        st.warning("Keine Dateien in der gewählten Google Drive Quelle gefunden.")
    else:
        st.warning(f"Keine Dateien gefunden in: {LIEFERSCHEINE_DIR}")

extract_from_disk = st.button("Alle Dateien extrahieren")
base_system_prompt = DEFAULT_SYSTEM_PROMPT
output_template = default_output_schema()
st.session_state["orders_output_template"] = output_template
llm_provider = st.selectbox(
    "LLM Provider",
    [LLM_PROVIDER_OPENAI, LLM_PROVIDER_GOOGLE],
    index=0,
    help="Wähle den LLM-Anbieter für die Extraktion.",
)

extract_model = st.selectbox(
    "Extraktionsmodell (API)",
    LLM_MODELS_BY_PROVIDER.get(llm_provider, LLM_MODELS_BY_PROVIDER[LLM_PROVIDER_OPENAI]),
    index=0,
    help=(
        "Modelle zur strukturierten Extraktion aus Text oder Bild. "
        "Bei Google sind Gemini-Modelle aktiv."
    ),
)
extraction_source_label = image_source
if is_using_preprocessed_inputs and preprocessed_base_dir:
    extraction_source_label = "Preprocessed Dateien"

if extract_from_disk:
    if not available_images:
        st.warning("Keine Dateien zum Verarbeiten gefunden.")
    else:
        logger.info(
            "Extraction requested files=%s provider=%s model=%s source=%s",
            len(available_images),
            llm_provider,
            extract_model,
            extraction_source_label,
        )
        api_key = resolve_llm_api_key(
            llm_provider,
            session_state=st.session_state,
            secrets=st.secrets,
            environ=os.environ,
        )
        if not api_key:
            st.error(f"{llm_provider}-API Key nicht gefunden. Bitte in .env oder st.secrets setzen.")
            st.stop()
        logger.info("API key resolved provider=%s", llm_provider)
        with st.spinner("Extrahiere aus allen Dateien..."):
            extraction_started = time.perf_counter()
            extracted_rows: list[dict] = []
            failures: list[str] = []
            attempted_inputs = 0
            total_files = len(available_images)
            for file_index, image_item in enumerate(available_images, start=1):
                image_label = image_item.get("name", "unbekannt")
                item_kind = str(image_item.get("kind") or "")
                item_folder = str(image_item.get("folder") or "")
                logger.info(
                    "File processing started file_index=%s/%s label=%s kind=%s folder=%s",
                    file_index,
                    total_files,
                    image_label,
                    item_kind,
                    item_folder,
                )
                prep_started = time.perf_counter()
                try:
                    logger.info("Reading source bytes label=%s kind=%s", image_label, item_kind)
                    read_started = time.perf_counter()
                    image_bytes, image_name, folder = read_lieferschein_image_bytes(
                        image_item,
                        download_drive_image_bytes=_download_drive_image_bytes_cached,
                    )
                    logger.info(
                        "Source bytes loaded image=%s bytes=%s extension=%s duration_s=%.3f",
                        image_name,
                        len(image_bytes),
                        os.path.splitext(str(image_name))[1].lower(),
                        time.perf_counter() - read_started,
                    )
                    images_to_process: list[tuple[bytes, str]] = [(image_bytes, image_name)]
                    if image_name.lower().endswith(".pdf"):
                        logger.info("PDF conversion started file=%s bytes=%s", image_name, len(image_bytes))
                        pdf_split_started = time.perf_counter()
                        images_to_process = split_pdf_bytes_to_page_images(
                            image_bytes,
                            pdf_name=image_name,
                        )
                        logger.info(
                            "PDF expanded into pages file=%s pages=%s duration_s=%.3f",
                            image_name,
                            len(images_to_process),
                            time.perf_counter() - pdf_split_started,
                        )
                    logger.info(
                        "File preparation completed file=%s units=%s duration_s=%.3f",
                        image_name,
                        len(images_to_process),
                        time.perf_counter() - prep_started,
                    )
                except Exception as exc:
                    failures.append(f"{image_label}: {exc}")
                    logger.exception(
                        "Extraction preparation failed for image=%s provider=%s model=%s duration_s=%.3f",
                        image_label,
                        llm_provider,
                        extract_model,
                        time.perf_counter() - prep_started,
                    )
                    continue

                total_units = len(images_to_process)
                for unit_index, (page_bytes, page_name) in enumerate(images_to_process, start=1):
                    attempted_inputs += 1
                    unit_started = time.perf_counter()
                    try:
                        logger.info(
                            (
                                "Input extraction started file=%s unit=%s/%s input=%s bytes=%s "
                                "provider=%s model=%s folder=%s global_input=%s"
                            ),
                            image_name,
                            unit_index,
                            total_units,
                            page_name,
                            len(page_bytes),
                            llm_provider,
                            extract_model,
                            folder,
                            attempted_inputs,
                        )
                        prompt_started = time.perf_counter()
                        image_user_content = build_image_user_content(
                            page_bytes,
                            page_name,
                        )
                        logger.info(
                            "Prompt payload built input=%s blocks=%s duration_s=%.3f",
                            page_name,
                            len(image_user_content) if isinstance(image_user_content, list) else 0,
                            time.perf_counter() - prompt_started,
                        )
                        llm_started = time.perf_counter()
                        orders_json = extract_lieferscheine_orders(
                            provider=llm_provider,
                            api_key=api_key,
                            model_name=extract_model,
                            user_content=image_user_content,
                            output_template=st.session_state.get("orders_output_template"),
                            system_prompt_base=base_system_prompt,
                            target_key="lieferscheine_v1",
                        )
                        raw_orders = orders_json.get("orders", [])
                        raw_orders_count = len(raw_orders) if isinstance(raw_orders, list) else 0
                        logger.info(
                            "LLM extraction completed input=%s raw_orders=%s duration_s=%.3f",
                            page_name,
                            raw_orders_count,
                            time.perf_counter() - llm_started,
                        )
                        map_started = time.perf_counter()
                        mapped_orders = to_orders_payload(
                            raw_orders,
                            folder=folder,
                        )
                        logger.info(
                            "Mapping completed input=%s mapped_orders=%s duration_s=%.3f",
                            page_name,
                            len(mapped_orders),
                            time.perf_counter() - map_started,
                        )
                        if not mapped_orders:
                            logger.warning(
                                "No mapped orders for image=%s provider=%s model=%s",
                                page_name,
                                llm_provider,
                                extract_model,
                            )
                        extracted_rows.extend(mapped_orders)
                        logger.info(
                            "Input extraction finished input=%s total_duration_s=%.3f",
                            page_name,
                            time.perf_counter() - unit_started,
                        )
                    except Exception as exc:
                        failures.append(f"{image_label} / {page_name}: {exc}")
                        logger.exception(
                            "Extraction failed for image=%s page=%s provider=%s model=%s duration_s=%.3f",
                            image_label,
                            page_name,
                            llm_provider,
                            extract_model,
                            time.perf_counter() - unit_started,
                        )
                logger.info(
                    "File processing finished file=%s processed_units=%s",
                    image_label,
                    total_units,
                )
            run_duration_s = time.perf_counter() - extraction_started
            normalized_extracted_rows = normalize_orders_for_json(extracted_rows)
            st.session_state["orders_json"] = {"orders": normalized_extracted_rows}
            try:
                saved_json_path, saved_csv_path = _write_extraction_artifacts(
                    source=extraction_source_label,
                    provider=llm_provider,
                    model=extract_model,
                    available_images=available_images,
                    attempted_inputs=attempted_inputs,
                    extracted_rows=normalized_extracted_rows,
                    failures=failures,
                    duration_seconds=run_duration_s,
                )
                logger.info(
                    "Extraction artifacts saved json=%s csv=%s",
                    saved_json_path,
                    saved_csv_path,
                )
                st.caption(f"Ergebnisse gespeichert: {saved_json_path} und {saved_csv_path}")
            except Exception:
                logger.exception(
                    "Failed to persist extraction artifacts output_dir=%s",
                    LIEFERSCHEINE_EXTRACTIONS_DIR,
                )
                st.error(
                    "Extraktion abgeschlossen, aber Speichern der Ergebnisdateien fehlgeschlagen. "
                    f"Bitte Logs pruefen: {LIEFERSCHEINE_EXTRACTIONS_DIR}"
                )
            logger.info(
                "Extraction run finished files=%s attempted_inputs=%s extracted_rows=%s failures=%s duration_s=%.3f",
                total_files,
                attempted_inputs,
                len(extracted_rows),
                len(failures),
                run_duration_s,
            )
            if extracted_rows:
                st.success(
                    f"{len(extracted_rows)} Positionen aus {len(available_images)} Dateien "
                    f"({attempted_inputs} Seiten/Bilder) extrahiert."
                )
            if failures:
                st.warning(f"{len(failures)} Seiten/Bilder konnten nicht verarbeitet werden.")
                with st.expander("Fehlerhafte Dateien"):
                    for msg in failures:
                        st.write(msg)
            if not extracted_rows and not failures and llm_provider == LLM_PROVIDER_GOOGLE:
                st.warning(
                    "Gemini hat keine Positionen extrahiert. Prüfe ggf. Dateiqualität, Modellzugang oder "
                    "ob die Datei sichtbaren Lieferschein-Text enthält."
                )
            if not extracted_rows and failures:
                st.error("Extraktion fehlgeschlagen.")




current_orders = None
if st.session_state.get("orders_json", {}).get("orders"):
    st.subheader("✏️ Bestellungen bearbeiten")
    edited_orders = st.data_editor(
        orders_to_editor_df(st.session_state["orders_json"]["orders"]),
        num_rows="dynamic",
        width="stretch",
        key="orders_editor",
    )
    current_orders = edited_orders.to_dict(orient="records")
    if st.button("Änderungen übernehmen"):
        st.session_state["orders_json"]["orders"] = normalize_orders_for_json(current_orders)
        st.success("Änderungen gespeichert.")

    try:
        orders_for_export = normalize_orders_for_json(current_orders)
        excel_bytes = _orders_to_excel_bytes(orders_for_export)
        export_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        st.download_button(
            "Download Excel (orders + summary)",
            data=excel_bytes,
            file_name=f"lieferscheine_orders_{export_stamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as exc:
        logger.exception("Excel export build failed orders=%s", len(current_orders))
        st.error(f"Excel-Export fehlgeschlagen: {exc}")

    if st.button("Sum by folder + product"):
        summary_df = _summarize_orders_by_folder_product(current_orders)
        if summary_df.empty:
            st.info("Keine Daten für die Summierung vorhanden.")
        else:
            st.subheader("Summe nach Ordner und Produkt")
            st.dataframe(summary_df, hide_index=True, width="stretch")
