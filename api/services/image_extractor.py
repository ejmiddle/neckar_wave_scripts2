import base64
import json
import os
from copy import deepcopy

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
    _ = metadata
    model_name = get_image_extraction_model()

    default_eintragender = str(metadata.get("default_eintragender", "")).strip()
    prompt_config = load_prompt_config()
    output_template = default_output_schema(default_eintragender)
    system_prompt_base = prompt_config.get("system_prompt", "")
    system_prompt = build_system_prompt_with_descriptions(
        system_prompt_base,
        output_template,
    )
    user_prompt = build_image_user_prompt()
    logger.info("Image extraction request_id=%s model=%s", request_id, model_name)
    logger.info("Image extraction system prompt:\n%s", system_prompt)
    logger.info("Image extraction user prompt:\n%s", user_prompt)
    logger.info(
        "Image extraction tool schema:\n%s",
        json.dumps(get_tools_for_target("orders_v1"), ensure_ascii=True, indent=2),
    )

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _build_dummy_response(
            request_id=request_id,
            default_eintragender=default_eintragender,
            warning="OPENAI_API_KEY fehlt, dummy response verwendet.",
            output_template=output_template,
            model_version=model_name,
        )

    try:
        client = OpenAI(api_key=api_key)
        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        image_url = f"data:{content_type};base64,{image_base64}"

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
        logger.info("Image extraction repair trace request_id=%s trace=%s", request_id, trace)
    except Exception as exc:  # noqa: BLE001
        return _build_dummy_response(
            request_id=request_id,
            default_eintragender=default_eintragender,
            warning=f"OpenAI extraction failed ({exc.__class__.__name__}), dummy response verwendet.",
            output_template=output_template,
            model_version=model_name,
        )

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
        return dummy

    return ImageExtractResponse(
        request_id=request_id,
        status="ok",
        columns=columns,
        rows=rows,
        orders=orders,
        warnings=warnings,
        model_version=model_name,
    )
