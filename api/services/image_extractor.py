import base64
import json
import os
from copy import deepcopy
from time import perf_counter

from openai import OpenAI

from api.models.image_extract import ImageExtractResponse
from src.logging_config import logger
from src.order_prompt_config import (
    build_image_user_prompt,
    build_system_prompt_with_descriptions,
    default_output_schema,
    get_image_extraction_model,
    load_prompt_config,
    order_field_names,
)
from src.structured_extraction import extract_with_repair, get_tools_for_target


def _stringify(value) -> str:  # type: ignore[no-untyped-def]
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=True)


def _orders_to_rows(orders: list[dict], columns: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for order in orders:
        row = {column: _stringify(order.get(column, "")) for column in columns}
        rows.append(row)
    return rows


def _default_columns_from_template(output_template: dict) -> list[str]:
    orders = output_template.get("orders")
    if isinstance(orders, list) and orders and isinstance(orders[0], dict):
        return list(orders[0].keys())
    return []


def _default_order_from_template(output_template: dict) -> dict:
    orders = output_template.get("orders")
    if isinstance(orders, list) and orders and isinstance(orders[0], dict):
        return deepcopy(orders[0])
    return {}


def _build_dummy_response(
    request_id: str,
    default_eintragender: str,
    warning: str,
    output_template: dict,
    model_version: str = "placeholder-v1",
) -> ImageExtractResponse:
    columns = _default_columns_from_template(output_template)
    fallback_order = _default_order_from_template(output_template)

    if default_eintragender and not fallback_order.get("Eintragender"):
        fallback_order["Eintragender"] = default_eintragender
    if columns:
        for column in columns:
            fallback_order.setdefault(column, "")
    else:
        columns = list(fallback_order.keys())

    orders = [fallback_order]
    rows = _orders_to_rows(orders, columns)
    return ImageExtractResponse(
        request_id=request_id,
        status="ok",
        columns=columns,
        rows=rows,
        orders=orders,
        warnings=[warning],
        model_version=model_version,
    )


def extract_orders_from_image(
    *,
    request_id: str,
    image_bytes: bytes,
    content_type: str,
    metadata: dict,
) -> ImageExtractResponse:
    started_at = perf_counter()
    model_name = get_image_extraction_model()

    default_eintragender = str(metadata.get("default_eintragender", "")).strip()
    metadata_keys = sorted([str(key) for key in metadata.keys()])
    logger.info(
        "Image extraction start request_id=%s model=%s content_type=%s image_bytes=%s metadata_keys=%s has_default_eintragender=%s",
        request_id,
        model_name,
        content_type,
        len(image_bytes),
        metadata_keys,
        bool(default_eintragender),
    )

    prompt_config = load_prompt_config()
    output_template = default_output_schema(default_eintragender)
    system_prompt_base = prompt_config.get("system_prompt", "")
    system_prompt = build_system_prompt_with_descriptions(
        system_prompt_base,
        output_template,
    )
    user_prompt = build_image_user_prompt()
    tools = get_tools_for_target("orders_v1")
    logger.info(
        "Image extraction prompt ready request_id=%s system_prompt_chars=%s user_prompt_chars=%s tool_count=%s",
        request_id,
        len(system_prompt),
        len(user_prompt),
        len(tools),
    )

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "Image extraction request_id=%s: OPENAI_API_KEY missing, returning dummy response.",
            request_id,
        )
        dummy = _build_dummy_response(
            request_id=request_id,
            default_eintragender=default_eintragender,
            warning="OPENAI_API_KEY fehlt, dummy response verwendet.",
            output_template=output_template,
            model_version=model_name,
        )
        logger.info(
            "Image extraction done request_id=%s model=%s used_dummy=%s orders=%s columns=%s warnings=%s duration_ms=%s",
            request_id,
            model_name,
            True,
            len(dummy.orders),
            len(dummy.columns),
            len(dummy.warnings),
            round((perf_counter() - started_at) * 1000, 1),
        )
        return dummy

    try:
        client = OpenAI(api_key=api_key)
        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        image_url = f"data:{content_type};base64,{image_base64}"
        logger.info(
            "Image extraction OpenAI call request_id=%s model=%s image_base64_chars=%s",
            request_id,
            model_name,
            len(image_base64),
        )

        parsed, trace = extract_with_repair(
            client=client,
            model_name=model_name,
            system_prompt=system_prompt,
            user_content=[
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
            target_key="orders_v1",
            context={"default_eintragender": default_eintragender},
            max_retries=2,
            temperature=0,
        )
        logger.info(
            "Image extraction OpenAI result request_id=%s attempts=%s normalization=%s",
            request_id,
            trace.get("attempts"),
            trace.get("normalization"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Image extraction request_id=%s: OpenAI extraction failed (%s), returning dummy response.",
            request_id,
            exc.__class__.__name__,
        )
        dummy = _build_dummy_response(
            request_id=request_id,
            default_eintragender=default_eintragender,
            warning=f"OpenAI extraction failed ({exc.__class__.__name__}), dummy response verwendet.",
            output_template=output_template,
            model_version=model_name,
        )
        logger.info(
            "Image extraction done request_id=%s model=%s used_dummy=%s orders=%s columns=%s warnings=%s duration_ms=%s",
            request_id,
            model_name,
            True,
            len(dummy.orders),
            len(dummy.columns),
            len(dummy.warnings),
            round((perf_counter() - started_at) * 1000, 1),
        )
        return dummy

    columns = order_field_names()
    orders = parsed.get("orders", [])
    if not columns and orders:
        columns = list(orders[0].keys())
    rows = _orders_to_rows(orders, columns)

    warnings: list[str] = []
    if not orders:
        dummy = _build_dummy_response(
            request_id=request_id,
            default_eintragender=default_eintragender,
            warning="Keine orders erkannt, dummy response verwendet.",
            output_template=output_template,
            model_version=model_name,
        )
        logger.warning(
            "Image extraction request_id=%s: no orders extracted, returning dummy response.",
            request_id,
        )
        logger.info(
            "Image extraction done request_id=%s model=%s used_dummy=%s orders=%s columns=%s warnings=%s duration_ms=%s",
            request_id,
            model_name,
            True,
            len(dummy.orders),
            len(dummy.columns),
            len(dummy.warnings),
            round((perf_counter() - started_at) * 1000, 1),
        )
        return dummy

    response = ImageExtractResponse(
        request_id=request_id,
        status="ok",
        columns=columns,
        rows=rows,
        orders=orders,
        warnings=warnings,
        model_version=model_name,
    )
    logger.info(
        "Image extraction done request_id=%s model=%s used_dummy=%s orders=%s columns=%s warnings=%s duration_ms=%s",
        request_id,
        model_name,
        False,
        len(response.orders),
        len(response.columns),
        len(response.warnings),
        round((perf_counter() - started_at) * 1000, 1),
    )
    return response
