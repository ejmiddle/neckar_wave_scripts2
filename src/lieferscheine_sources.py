from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from src.app_paths import DATA_DIR
from src.logging_config import logger

LIEFERSCHEINE_DIR = DATA_DIR / "lieferscheine"
SUPPORTED_LIEFERSCHEIN_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".pdf",
}
# Backwards-compatible alias for existing imports/usages.
SUPPORTED_IMAGE_EXTENSIONS = SUPPORTED_LIEFERSCHEIN_EXTENSIONS
_PREPROCESSED_PDF_PAGE_PATTERN = re.compile(r"_seite_(\d+)\.png$", re.IGNORECASE)


def _require_pymupdf() -> Any:
    try:
        import fitz  # type: ignore[import-not-found]

        return fitz
    except Exception:
        pass
    try:
        import pymupdf  # type: ignore[import-not-found]

        return pymupdf
    except Exception as exc:
        raise RuntimeError(
            "PyMuPDF ist nicht installiert. Bitte Abhaengigkeiten via `uv sync` aktualisieren."
        ) from exc


def _preprocessed_page_sort_key(page_path: Path) -> int:
    match = _PREPROCESSED_PDF_PAGE_PATTERN.search(page_path.name)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def get_preprocessed_pdf_page_paths(pdf_path: Path) -> list[Path]:
    pattern = f"{pdf_path.stem}_seite_*.png"
    return sorted(pdf_path.parent.glob(pattern), key=_preprocessed_page_sort_key)


def has_preprocessed_pdf_pages(pdf_path: Path) -> bool:
    return len(get_preprocessed_pdf_page_paths(pdf_path)) > 0


def preprocess_local_pdf_files_to_png(
    *,
    base_dir: Path = LIEFERSCHEINE_DIR,
    dpi: int = 200,
    overwrite: bool = False,
) -> dict[str, Any]:
    fitz = _require_pymupdf()

    started = time.perf_counter()
    if not base_dir.exists():
        return {
            "base_dir": str(base_dir),
            "pdf_count": 0,
            "processed_pdf_count": 0,
            "generated_png_count": 0,
            "skipped_existing_png_count": 0,
            "failed_pdfs": [],
            "duration_seconds": time.perf_counter() - started,
        }

    pdf_paths = sorted(base_dir.rglob("*.pdf"))
    generated_png_count = 0
    skipped_existing_png_count = 0
    processed_pdf_count = 0
    failed_pdfs: list[str] = []
    scale = max(0.1, dpi / 72.0)
    matrix = fitz.Matrix(scale, scale)
    logger.info(
        "PDF preprocessing started base_dir=%s pdfs=%s dpi=%s overwrite=%s",
        base_dir,
        len(pdf_paths),
        dpi,
        overwrite,
    )

    for pdf_path in pdf_paths:
        pdf_started = time.perf_counter()
        try:
            logger.info("PDF preprocessing file started pdf=%s", pdf_path)
            with fitz.open(pdf_path) as document:
                page_count = document.page_count
                for page_index in range(page_count):
                    page = document.load_page(page_index)
                    output_png = pdf_path.with_name(f"{pdf_path.stem}_seite_{page_index + 1}.png")
                    if output_png.exists() and not overwrite:
                        skipped_existing_png_count += 1
                        continue
                    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                    pixmap.save(output_png)
                    generated_png_count += 1
                processed_pdf_count += 1
                logger.info(
                    "PDF preprocessing file finished pdf=%s pages=%s duration_s=%.3f",
                    pdf_path,
                    page_count,
                    time.perf_counter() - pdf_started,
                )
        except Exception as exc:
            failed_pdfs.append(f"{pdf_path}: {exc}")
            logger.exception("PDF preprocessing failed pdf=%s", pdf_path)

    duration_seconds = time.perf_counter() - started
    logger.info(
        (
            "PDF preprocessing finished base_dir=%s pdf_count=%s processed_pdfs=%s "
            "generated_png=%s skipped_existing=%s failures=%s duration_s=%.3f"
        ),
        base_dir,
        len(pdf_paths),
        processed_pdf_count,
        generated_png_count,
        skipped_existing_png_count,
        len(failed_pdfs),
        duration_seconds,
    )
    return {
        "base_dir": str(base_dir),
        "pdf_count": len(pdf_paths),
        "processed_pdf_count": processed_pdf_count,
        "generated_png_count": generated_png_count,
        "skipped_existing_png_count": skipped_existing_png_count,
        "failed_pdfs": failed_pdfs,
        "duration_seconds": duration_seconds,
    }


def split_pdf_bytes_to_page_images(
    pdf_bytes: bytes,
    *,
    pdf_name: str = "lieferschein.pdf",
    dpi: int = 150,
    image_format: str = "jpeg",
    jpeg_quality: int = 70,
    grayscale: bool = True,
    max_image_bytes: int | None = 1_000_000,
) -> list[tuple[bytes, str]]:
    fitz = _require_pymupdf()
    started = time.perf_counter()
    normalized_format = "jpeg" if image_format.lower() in {"jpg", "jpeg"} else "png"
    output_extension = "jpg" if normalized_format == "jpeg" else "png"
    clamped_quality = min(max(jpeg_quality, 30), 95)
    min_jpeg_quality = 45
    logger.info(
        (
            "PDF split started file=%s bytes=%s dpi=%s format=%s "
            "jpeg_quality=%s grayscale=%s max_image_bytes=%s"
        ),
        pdf_name,
        len(pdf_bytes),
        dpi,
        normalized_format,
        clamped_quality,
        grayscale,
        max_image_bytes,
    )
    base_scale = max(0.1, dpi / 72.0)
    min_scale = 1.0
    colorspace = fitz.csGRAY if grayscale else None
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            page_count = document.page_count
            if page_count <= 0:
                raise RuntimeError("PDF enthaelt keine Seiten.")

            base_stem = Path(pdf_name).stem or "lieferschein"
            output: list[tuple[bytes, str]] = []
            for page_index in range(page_count):
                page = document.load_page(page_index)
                render_scale = base_scale
                current_quality = clamped_quality
                page_bytes = b""
                while True:
                    matrix = fitz.Matrix(render_scale, render_scale)
                    pixmap_kwargs = {
                        "matrix": matrix,
                        "alpha": False,
                    }
                    if colorspace is not None:
                        pixmap_kwargs["colorspace"] = colorspace
                    pixmap = page.get_pixmap(**pixmap_kwargs)

                    if normalized_format == "jpeg":
                        try:
                            page_bytes = pixmap.tobytes("jpeg", jpg_quality=current_quality)
                        except TypeError:
                            page_bytes = pixmap.tobytes("jpg", jpg_quality=current_quality)
                    else:
                        page_bytes = pixmap.tobytes("png")

                    if max_image_bytes is None or len(page_bytes) <= max_image_bytes:
                        break
                    if normalized_format == "jpeg" and current_quality > min_jpeg_quality:
                        current_quality = max(min_jpeg_quality, current_quality - 10)
                        continue
                    if render_scale > min_scale:
                        render_scale = max(min_scale, render_scale * 0.85)
                        continue
                    break
                output.append(
                    (
                        page_bytes,
                        f"{base_stem}_seite_{page_index + 1}.{output_extension}",
                    )
                )
    except Exception as exc:
        raise RuntimeError(f"PDF konnte nicht mit PyMuPDF konvertiert werden: {exc}") from exc

    output_sizes = [len(page_bytes) for page_bytes, _ in output]
    total_output_bytes = sum(output_sizes)
    logger.info(
        (
            "PDF split finished file=%s pages=%s total_output_bytes=%s "
            "max_page_bytes=%s duration_s=%.3f converter=pymupdf"
        ),
        pdf_name,
        len(output),
        total_output_bytes,
        max(output_sizes) if output_sizes else 0,
        time.perf_counter() - started,
    )
    return output


def discover_local_lieferschein_images(
    *,
    base_dir: Path = LIEFERSCHEINE_DIR,
    supported_extensions: set[str] = SUPPORTED_LIEFERSCHEIN_EXTENSIONS,
) -> list[dict[str, Any]]:
    if not base_dir.exists():
        return []

    images: list[dict[str, Any]] = []
    for image_path in sorted(base_dir.rglob("*")):
        if not image_path.is_file():
            continue
        if image_path.suffix.lower() not in supported_extensions:
            continue

        try:
            folder = str(image_path.parent.relative_to(base_dir))
        except ValueError:
            folder = image_path.parent.name
        if not folder:
            folder = "."
        images.append(
            {
                "kind": "local",
                "path": image_path,
                "name": image_path.name,
                "folder": folder,
            }
        )
    return images


def read_lieferschein_image_bytes(
    image_item: dict[str, Any],
    *,
    download_drive_image_bytes: Callable[[str], bytes],
) -> tuple[bytes, str, str]:
    kind = image_item.get("kind")
    if kind == "local":
        image_path = image_item.get("path")
        if not isinstance(image_path, Path):
            raise RuntimeError("Lokale Bildquelle ist ungültig.")
        started = time.perf_counter()
        logger.info("Reading local source path=%s", image_path)
        payload = image_path.read_bytes()
        logger.info(
            "Local source loaded path=%s bytes=%s duration_s=%.3f",
            image_path,
            len(payload),
            time.perf_counter() - started,
        )
        return (
            payload,
            image_item.get("name", image_path.name),
            image_item.get("folder", ""),
        )
    if kind == "drive":
        file_id = image_item.get("file_id")
        if not isinstance(file_id, str) or not file_id.strip():
            raise RuntimeError("Drive-Datei-ID fehlt.")
        started = time.perf_counter()
        logger.info("Reading drive source file_id=%s", file_id)
        payload = download_drive_image_bytes(file_id)
        logger.info(
            "Drive source loaded file_id=%s bytes=%s duration_s=%.3f",
            file_id,
            len(payload),
            time.perf_counter() - started,
        )
        return (
            payload,
            image_item.get("name", ""),
            image_item.get("folder", ""),
        )
    raise RuntimeError("Unbekannte Bildquelle.")
