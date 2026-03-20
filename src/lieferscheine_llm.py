from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.liefernscheine_prompt_config import (
    build_image_user_prompt,
    build_system_prompt_with_descriptions,
    default_output_schema,
    output_schema_json_schema,
    validate_lieferscheine_payload_with_report,
)
from src.logging_config import logger
from src.structured_extraction import extract_with_repair

LLM_PROVIDER_OPENAI = "OpenAI"
LLM_PROVIDER_GOOGLE = "Google (Gemini)"
LLM_MODELS_BY_PROVIDER = {
    LLM_PROVIDER_OPENAI: ["gpt-4o"],
    LLM_PROVIDER_GOOGLE: [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-3-flash-preview",
    ],
}


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


def resolve_openai_api_key(
    *,
    session_state: Any = None,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    env = environ or os.environ
    return (
        _read_str(session_state, "openai_api_key")
        or _read_str(env, "OPENAI_API_KEY")
        or _read_str(secrets, "OPENAI_API_KEY")
    )


def resolve_google_api_key(
    *,
    session_state: Any = None,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    env = environ or os.environ
    return (
        _read_str(session_state, "google_api_key")
        or _read_str(env, "GOOGLE_API_KEY")
        or _read_str(env, "GEMINI_API_KEY")
        or _read_str(secrets, "GOOGLE_API_KEY")
        or _read_str(secrets, "GEMINI_API_KEY")
    )


def resolve_llm_api_key(
    provider: str,
    *,
    session_state: Any = None,
    secrets: Any = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    if provider == LLM_PROVIDER_GOOGLE:
        return resolve_google_api_key(
            session_state=session_state,
            secrets=secrets,
            environ=environ,
        )
    return resolve_openai_api_key(
        session_state=session_state,
        secrets=secrets,
        environ=environ,
    )


def build_image_user_content(
    image_payload: bytes | Path,
    image_name: str | None = None,
) -> list[dict[str, Any]]:
    if isinstance(image_payload, Path):
        image_name = image_payload.name
        image_bytes = image_payload.read_bytes()
    else:
        image_bytes = image_payload
    if not image_name:
        image_name = "lieferschein.jpg"

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    image_ext = Path(image_name).suffix.lower().strip(".") or "jpeg"
    content_type_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "bmp": "image/bmp",
        "gif": "image/gif",
        "pdf": "application/pdf",
    }
    content_type = content_type_map.get(image_ext, "image/jpeg")
    image_url = f"data:{content_type};base64,{image_b64}"
    return [
        {"type": "text", "text": build_image_user_prompt()},
        {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
    ]


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


def _normalize_with_target(
    raw_payload: Any,
    target_key: str,
) -> dict[str, Any]:
    if target_key == "lieferscheine_v1":
        normalized_payload, _ = validate_lieferscheine_payload_with_report(raw_payload)
        return normalized_payload
    if isinstance(raw_payload, dict):
        return raw_payload
    return {}


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


def _extract_with_google(
    *,
    api_key: str,
    model_name: str,
    user_content: str | list[dict[str, Any]],
    target_key: str,
    system_prompt: str,
) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    started = time.perf_counter()
    logger.info("Google extraction started model=%s target=%s", model_name, target_key)

    parts_started = time.perf_counter()
    content_parts = _build_google_content_parts(user_content)
    logger.info(
        "Google content parts built model=%s parts=%s duration_s=%.3f",
        model_name,
        len(content_parts),
        time.perf_counter() - parts_started,
    )
    if not content_parts:
        raise RuntimeError("No valid prompt content for Google model call.")

    client_started = time.perf_counter()
    client = genai.Client(api_key=api_key)
    logger.info(
        "Google client initialized model=%s duration_s=%.3f",
        model_name,
        time.perf_counter() - client_started,
    )
    request_started = time.perf_counter()
    logger.info("Google API call started model=%s", model_name)
    response = client.models.generate_content(
        model=model_name,
        contents=content_parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=output_schema_json_schema(),
            system_instruction=system_prompt,
        ),
    )
    logger.info(
        "Google API call finished model=%s duration_s=%.3f",
        model_name,
        time.perf_counter() - request_started,
    )
    raw_output = response.text or ""
    logger.info(
        "Google raw response received model=%s response_chars=%s",
        model_name,
        len(raw_output),
    )
    try:
        parse_started = time.perf_counter()
        parsed_payload = json.loads(_extract_json_string(raw_output))
        logger.info(
            "Google response parsed model=%s duration_s=%.3f",
            model_name,
            time.perf_counter() - parse_started,
        )
    except Exception as exc:
        logger.exception("Google response was not valid JSON. model=%s", model_name)
        raise RuntimeError("Google-Extraktion fehlgeschlagen: Response ist kein valides JSON") from exc

    normalize_started = time.perf_counter()
    normalized = _normalize_with_target(parsed_payload, target_key)
    orders_value = normalized.get("orders", []) if isinstance(normalized, dict) else []
    orders_count = len(orders_value) if isinstance(orders_value, list) else 0
    logger.info(
        "Google extraction finished model=%s orders=%s normalize_s=%.3f total_s=%.3f",
        model_name,
        orders_count,
        time.perf_counter() - normalize_started,
        time.perf_counter() - started,
    )
    return normalized


def extract_lieferscheine_orders(
    *,
    provider: str,
    api_key: str,
    model_name: str,
    user_content: str | list[dict[str, Any]],
    output_template: dict[str, Any] | None,
    system_prompt_base: str,
    target_key: str = "lieferscheine_v1",
) -> dict[str, Any]:
    extraction_started = time.perf_counter()
    output_structure = output_template or default_output_schema()
    prompt_started = time.perf_counter()
    system_prompt = build_system_prompt_with_descriptions(
        system_prompt_base,
        output_structure,
    )
    logger.info(
        "System prompt prepared provider=%s model=%s duration_s=%.3f",
        provider,
        model_name,
        time.perf_counter() - prompt_started,
    )
    user_prompt_text, image_context = _collect_prompt_logging_details(user_content)
    logger.info("Lieferschein extraction provider=%s model=%s", provider, model_name)
    logger.info(
        "Lieferschein prompts provider=%s model=%s system_prompt=%s user_prompt=%s image_context=%s",
        provider,
        model_name,
        _truncate_for_log(system_prompt),
        user_prompt_text,
        image_context,
    )

    if provider == LLM_PROVIDER_GOOGLE:
        result = _extract_with_google(
            api_key=api_key,
            model_name=model_name,
            user_content=user_content,
            target_key=target_key,
            system_prompt=system_prompt,
        )
        logger.info(
            "Lieferschein extraction completed provider=%s model=%s total_s=%.3f",
            provider,
            model_name,
            time.perf_counter() - extraction_started,
        )
        return result

    logger.info("OpenAI extraction started model=%s target=%s", model_name, target_key)
    client_started = time.perf_counter()
    client = OpenAI(api_key=api_key)
    logger.info(
        "OpenAI client initialized model=%s duration_s=%.3f",
        model_name,
        time.perf_counter() - client_started,
    )
    try:
        request_started = time.perf_counter()
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
        orders_value = parsed.get("orders", []) if isinstance(parsed, dict) else []
        orders_count = len(orders_value) if isinstance(orders_value, list) else 0
        logger.info(
            "OpenAI extraction finished model=%s orders=%s request_s=%.3f total_s=%.3f",
            model_name,
            orders_count,
            time.perf_counter() - request_started,
            time.perf_counter() - extraction_started,
        )
        return parsed
    except Exception as exc:
        logger.exception(
            "Lieferschein extraction failed provider=%s model=%s target=%s",
            provider,
            model_name,
            target_key,
        )
        raise RuntimeError(f"{provider}-Extraktion fehlgeschlagen: {exc}") from exc
