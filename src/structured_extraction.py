from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from src.order_prompt_config import OrdersPayload, validate_orders_payload_with_report


class StructuredExtractionError(RuntimeError):
    pass


NormalizerFn = Callable[[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]
ExtractionPattern = str

PATTERN_TOOL_CALL_REPAIR: ExtractionPattern = "tool_call_repair"
# Placeholder patterns for future evolution:
PATTERN_JSON_MODE_ONCE: ExtractionPattern = "json_mode_once"
# PATTERN_RESPONSES_PARSE: ExtractionPattern = "responses_parse"


@dataclass(frozen=True)
class ExtractionTarget:
    key: str
    pattern: ExtractionPattern
    function_name: str
    description: str
    model: type[BaseModel]
    normalize: NormalizerFn


def _normalize_orders(
    payload: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    default_eintragender = str(context.get("default_eintragender", "")).strip()
    return validate_orders_payload_with_report(
        payload,
        default_eintragender=default_eintragender,
    )


EXTRACTION_TARGETS: dict[str, ExtractionTarget] = {
    "orders_v1": ExtractionTarget(
        key="orders_v1",
        pattern=PATTERN_TOOL_CALL_REPAIR,
        function_name="extract_orders_v1",
        description="Extract bakery orders from text/image into the orders payload schema.",
        model=OrdersPayload,
        normalize=_normalize_orders,
    ),
    # Placeholder for future structures:
    # "customers_v1": ExtractionTarget(...),
    # "invoices_v1": ExtractionTarget(...),
}


def _build_tools(target: ExtractionTarget) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": target.function_name,
                "description": target.description,
                "parameters": target.model.model_json_schema(by_alias=True),
            },
        }
    ]


def get_tools_for_target(target_key: str) -> list[dict[str, Any]]:
    target = EXTRACTION_TARGETS.get(target_key)
    if target is None:
        raise StructuredExtractionError(f"Unknown extraction target: {target_key}")
    return _build_tools(target)


def _extract_arguments(response: Any) -> str:
    message = response.choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        fn = getattr(tool_calls[0], "function", None)
        args = getattr(fn, "arguments", None)
        if isinstance(args, str) and args.strip():
            return args
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    return "{}"


def _json_error_message(raw_args: str, error: ValidationError) -> str:
    return (
        "The following JSON failed schema validation.\n\n"
        "Re-read the original source content from this conversation and only correct invalid parts.\n\n"
        "JSON:\n"
        f"{raw_args}\n\n"
        "Validation error:\n"
        f"{error}\n\n"
        "Fix ONLY invalid fields and return corrected JSON that fully matches the schema."
    )


def extract_with_repair(
    *,
    client: OpenAI,
    model_name: str,
    system_prompt: str,
    user_content: Any,
    target_key: str,
    context: dict[str, Any] | None = None,
    max_retries: int = 2,
    temperature: float = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    target = EXTRACTION_TARGETS.get(target_key)
    if target is None:
        raise StructuredExtractionError(f"Unknown extraction target: {target_key}")
    if target.pattern != PATTERN_TOOL_CALL_REPAIR:
        raise StructuredExtractionError(
            f"Unsupported extraction pattern for target {target_key}: {target.pattern}"
        )

    context = context or {}
    tools = _build_tools(target)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    last_error: ValidationError | None = None
    last_raw_args = "{}"

    for attempt in range(max_retries + 1):
        response = client.chat.completions.create(
            model=model_name,
            temperature=temperature,
            messages=messages,
            tools=tools,
            tool_choice={
                "type": "function",
                "function": {"name": target.function_name},
            },
        )
        raw_args = _extract_arguments(response)
        last_raw_args = raw_args

        try:
            parsed = target.model.model_validate_json(raw_args).model_dump(by_alias=True)
            normalized, normalization_report = target.normalize(parsed, context)
            return normalized, {
                "attempts": attempt + 1,
                "raw_arguments": raw_args,
                "target_key": target_key,
                "pattern": target.pattern,
                "normalization": normalization_report,
            }
        except ValidationError as error:
            last_error = error
            if attempt == max_retries:
                break
            messages.append({"role": "user", "content": _json_error_message(raw_args, error)})

    # Fallback: try to salvage with local normalization before failing hard.
    try:
        fallback_payload = json.loads(last_raw_args)
        if isinstance(fallback_payload, list):
            fallback_payload = fallback_payload[0] if fallback_payload else {}
        if not isinstance(fallback_payload, dict):
            fallback_payload = {}
    except Exception:
        fallback_payload = {}
    normalized, normalization_report = target.normalize(fallback_payload, context)
    has_structured_content = any(
        isinstance(value, list) and len(value) > 0 for value in normalized.values()
    )
    if has_structured_content:
        return normalized, {
            "attempts": max_retries + 1,
            "raw_arguments": last_raw_args,
            "target_key": target_key,
            "pattern": target.pattern,
            "fallback_used": True,
            "normalization": normalization_report,
            "validation_error": str(last_error) if last_error else "",
        }

    raise StructuredExtractionError(
        f"Extraction failed for target {target_key} after {max_retries + 1} attempts"
    ) from last_error
