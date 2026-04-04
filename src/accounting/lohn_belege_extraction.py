from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.accounting.lohn_belege_prompt_config import (
    DEFAULT_LOHNKOSTEN_SYSTEM_PROMPT,
    DEFAULT_LOHNKOSTEN_USER_PROMPT,
    DEFAULT_U1_SYSTEM_PROMPT,
    DEFAULT_U1_USER_PROMPT,
)
from src.logging_config import logger
from src.lieferscheine_sources import (
    split_pdf_bytes_to_page_images,
    split_pdf_bytes_to_page_pdfs,
)
from src.structured_extraction import extract_with_repair


def _build_image_block(image_bytes: bytes, image_name: str) -> dict[str, Any]:
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    image_ext = Path(image_name).suffix.lower().strip(".") or "jpeg"
    content_type_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "bmp": "image/bmp",
        "gif": "image/gif",
    }
    content_type = content_type_map.get(image_ext, "image/jpeg")
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{content_type};base64,{image_b64}",
            "detail": "high",
        },
    }


def build_document_user_content(
    images: list[tuple[bytes, str]],
    *,
    prompt_text: str,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
    for image_bytes, image_name in images:
        content.append(_build_image_block(image_bytes, image_name))
    return content


def _extract_structured(
    *,
    api_key: str,
    model_name: str,
    user_content: list[dict[str, Any]],
    system_prompt_base: str,
    target_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    client = OpenAI(api_key=api_key)
    extracted, prompt_info = extract_with_repair(
        client=client,
        model_name=model_name,
        system_prompt=system_prompt_base,
        user_content=user_content,
        target_key=target_key,
        context={},
        max_retries=2,
        temperature=0,
    )
    logger.info(
        "Lohn Belege extraction finished target=%s model=%s attempts=%s",
        target_key,
        model_name,
        prompt_info.get("attempts"),
    )
    return extracted, prompt_info


def extract_lohnkosten_from_pdf(
    *,
    pdf_bytes: bytes,
    pdf_name: str,
    api_key: str,
    model_name: str,
) -> dict[str, Any]:
    page_images = split_pdf_bytes_to_page_images(
        pdf_bytes,
        pdf_name=pdf_name,
        dpi=180,
        grayscale=False,
        max_image_bytes=1_500_000,
    )
    if not page_images:
        raise RuntimeError(f"PDF contains no processable pages: {pdf_name}")

    user_content = build_document_user_content(page_images, prompt_text=DEFAULT_LOHNKOSTEN_USER_PROMPT)
    extracted, prompt_info = _extract_structured(
        api_key=api_key,
        model_name=model_name,
        user_content=user_content,
        system_prompt_base=DEFAULT_LOHNKOSTEN_SYSTEM_PROMPT,
        target_key="lohnkosten_accounting_v1",
    )
    return {
        "source_type": "Lohnkosten",
        "file_name": pdf_name,
        "page_count": len(page_images),
        "model": model_name,
        "extracted": extracted,
        "prompt_info": prompt_info,
    }


def extract_u1_pages_from_pdf(
    *,
    pdf_bytes: bytes,
    pdf_name: str,
    api_key: str,
    model_name: str,
) -> dict[str, Any]:
    try:
        page_documents = split_pdf_bytes_to_page_pdfs(
            pdf_bytes,
            pdf_name=pdf_name,
        )
    except Exception as exc:
        logger.warning("U1 page PDF split failed pdf=%s error=%s", pdf_name, exc)
        page_documents = []
    page_images = split_pdf_bytes_to_page_images(
        pdf_bytes,
        pdf_name=pdf_name,
        dpi=180,
        grayscale=False,
        max_image_bytes=1_500_000,
    )
    if not page_images:
        raise RuntimeError(f"PDF contains no processable pages: {pdf_name}")

    page_results: list[dict[str, Any]] = []
    for page_number, page_image in enumerate(page_images, start=1):
        try:
            if len(page_documents) >= page_number:
                page_pdf_bytes, page_pdf_name = page_documents[page_number - 1]
            else:
                page_pdf_bytes = None
                page_pdf_name = f"{pdf_name}_seite_{page_number}.pdf"
            user_content = build_document_user_content([page_image], prompt_text=DEFAULT_U1_USER_PROMPT)
            extracted, prompt_info = _extract_structured(
                api_key=api_key,
                model_name=model_name,
                user_content=user_content,
                system_prompt_base=DEFAULT_U1_SYSTEM_PROMPT,
                target_key="u1_page_accounting_v1",
            )
            page_results.append(
                {
                    "source_type": "U1",
                    "file_name": pdf_name,
                    "page_number": page_number,
                    "page_count": len(page_images),
                    "model": model_name,
                    "page_pdf_name": page_pdf_name,
                    "page_pdf_bytes": page_pdf_bytes,
                    "extracted": extracted,
                    "prompt_info": prompt_info,
                }
            )
        except Exception as exc:
            page_results.append(
                {
                    "source_type": "U1",
                    "file_name": pdf_name,
                    "page_number": page_number,
                    "page_count": len(page_images),
                    "model": model_name,
                    "page_pdf_name": page_documents[page_number - 1][1]
                    if len(page_documents) >= page_number
                    else f"{pdf_name}_seite_{page_number}.pdf",
                    "extracted": {},
                    "error": str(exc),
                }
            )

    return {
        "source_type": "U1",
        "file_name": pdf_name,
        "page_count": len(page_images),
        "model": model_name,
        "pages": page_results,
    }
