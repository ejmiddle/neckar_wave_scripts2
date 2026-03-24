from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.amazon_accounting_prompt_config import (
    build_image_user_prompt,
    build_system_prompt_with_descriptions,
    default_output_schema,
    output_schema_json_schema,
    validate_amazon_accounting_payload_with_report,
)
from src.lieferscheine_llm import (
    LLM_MODELS_BY_PROVIDER,
    LLM_PROVIDER_GOOGLE,
    LLM_PROVIDER_OPENAI,
    resolve_llm_api_key,
)
from src.logging_config import logger
from src.structured_extraction import extract_with_repair


def _truncate_for_log(value: str, *, max_chars: int = 8000) -> str:
    normalized = value.replace("\r\n", "\n")
    if len(normalized) <= max_chars:
        return normalized
    remaining = len(normalized) - max_chars
    return f"{normalized[:max_chars]}...[truncated {remaining} chars]"


def _collect_prompt_logging_details(
    user_content: str | list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(user_content, str):
        return _truncate_for_log(user_content), []
    if not isinstance(user_content, list):
        return "", []

    text_parts: list[str] = []
    image_parts: list[dict[str, Any]] = []
    for block in user_content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
            continue
        if block_type != "image_url":
            continue
        image_url = block.get("image_url")
        url = image_url.get("url") if isinstance(image_url, dict) else None
        detail = image_url.get("detail") if isinstance(image_url, dict) else None
        if not isinstance(url, str):
            continue
        mime = "unknown"
        b64_length = 0
        if url.startswith("data:"):
            header, _, b64_data = url.partition(",")
            if header.startswith("data:"):
                mime = header[5:].split(";", 1)[0]
            b64_length = len(b64_data)
        image_parts.append(
            {
                "mime": mime,
                "detail": detail,
                "base64_chars": b64_length,
            }
        )
    joined_text = "\n\n".join(text_parts)
    return _truncate_for_log(joined_text), image_parts


def _extract_json_string(raw: str) -> str:
    payload = raw.strip()
    if not payload:
        return "{}"
    if payload.startswith("{") and payload.endswith("}"):
        return payload
    start = payload.find("{")
    end = payload.rfind("}")
    if start != -1 and end != -1 and end > start:
        return payload[start : end + 1]
    return "{}"


def _build_google_content_parts(user_content: str | list[dict[str, Any]]) -> list[Any]:
    from google.genai import types

    if isinstance(user_content, str):
        return [types.Part.from_text(text=user_content)]
    if not isinstance(user_content, list):
        return []

    content_parts = []
    for block in user_content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                content_parts.append(types.Part.from_text(text=text))
            continue
        if block.get("type") != "image_url":
            continue
        image_url = block.get("image_url", {})
        image_data = image_url.get("url") if isinstance(image_url, dict) else None
        if not isinstance(image_data, str) or not image_data.startswith("data:"):
            continue
        header, _, b64_data = image_data.partition(",")
        if not b64_data:
            continue
        mime = "image/jpeg"
        if header.startswith("data:"):
            mime = header[5:].split(";", 1)[0]
        try:
            decoded_data = base64.b64decode(b64_data.encode("ascii"))
        except Exception:
            continue
        content_parts.append(types.Part.from_bytes(data=decoded_data, mime_type=mime))
    return content_parts


def _normalize_with_target(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        normalized_payload, _ = validate_amazon_accounting_payload_with_report(raw_payload)
        return normalized_payload
    return default_output_schema()


def _build_image_block(
    image_bytes: bytes,
    image_name: str,
) -> dict[str, Any]:
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
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": build_image_user_prompt()}]
    for image_bytes, image_name in images:
        content.append(_build_image_block(image_bytes, image_name))
    return content


def _extract_with_google(
    *,
    api_key: str,
    model_name: str,
    user_content: str | list[dict[str, Any]],
    system_prompt: str,
) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    started = time.perf_counter()
    logger.info("Google Amazon accounting extraction started model=%s", model_name)
    content_parts = _build_google_content_parts(user_content)
    if not content_parts:
        raise RuntimeError("No valid prompt content for Google model call.")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=content_parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=output_schema_json_schema(),
            system_instruction=system_prompt,
        ),
    )
    raw_output = response.text or ""
    try:
        parsed_payload = json.loads(_extract_json_string(raw_output))
    except Exception as exc:
        raise RuntimeError("Google-Extraktion fehlgeschlagen: Response ist kein valides JSON") from exc

    normalized = _normalize_with_target(parsed_payload)
    logger.info(
        "Google Amazon accounting extraction finished model=%s duration_s=%.3f",
        model_name,
        time.perf_counter() - started,
    )
    return normalized


def extract_amazon_accounting_data(
    *,
    provider: str,
    api_key: str,
    model_name: str,
    user_content: str | list[dict[str, Any]],
    system_prompt_base: str,
    target_key: str = "amazon_receipt_accounting_v1",
) -> dict[str, Any]:
    extraction_started = time.perf_counter()
    system_prompt = build_system_prompt_with_descriptions(system_prompt_base)
    user_prompt_text, image_context = _collect_prompt_logging_details(user_content)
    logger.info("Amazon receipt extraction provider=%s model=%s", provider, model_name)
    logger.info(
        "Amazon receipt prompts provider=%s model=%s system_prompt=%s user_prompt=%s image_context=%s",
        provider,
        model_name,
        _truncate_for_log(system_prompt),
        user_prompt_text,
        image_context,
    )

    if provider == LLM_PROVIDER_GOOGLE:
        return _extract_with_google(
            api_key=api_key,
            model_name=model_name,
            user_content=user_content,
            system_prompt=system_prompt,
        )

    client = OpenAI(api_key=api_key)
    try:
        parsed, _ = extract_with_repair(
            client=client,
            model_name=model_name,
            system_prompt=system_prompt,
            user_content=user_content,
            target_key=target_key,
            context={},
            max_retries=2,
            temperature=0,
        )
        logger.info(
            "OpenAI Amazon accounting extraction finished model=%s duration_s=%.3f",
            model_name,
            time.perf_counter() - extraction_started,
        )
        return parsed
    except Exception as exc:
        logger.exception(
            "Amazon accounting extraction failed provider=%s model=%s target=%s",
            provider,
            model_name,
            target_key,
        )
        raise RuntimeError(f"{provider}-Extraktion fehlgeschlagen: {exc}") from exc
