from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.order_prompt_config import allowed_product_values

CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "liefernscheine_prompt_config.json"

DEFAULT_SYSTEM_PROMPT = (
    "Du extrahierst Lieferscheindaten für Lieferungen der Südseite Bakery. "
    "Arbeite strikt faktisch: keine Annahmen, keine Erfindungen. "
    "Nutze ausschliesslich das vorgegebene Tool-Schema. "
    "Wenn Information fehlt, nutze Default-Werte oder null."
)
DEFAULT_IMAGE_USER_PROMPT = (
    "Analysiere das Foto eines Lieferscheins und extrahiere alle Positionen "
    "mit den Feldern folder, customer, product, no_items und date. "
    "date muss als YYYY-MM-DD ohne Uhrzeit ausgegeben werden."
)
DEFAULT_IMAGE_EXTRACTION_MODEL = "gpt-4o"


def allowed_lieferscheine_product_values() -> list[str]:
    return allowed_product_values()


class LieferscheineItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    customer: str | None = Field(
        default=None,
        alias="customer",
        description="Kundenname oder Besteller auf dem Lieferschein.",
    )
    product: str = Field(
        default="",
        alias="product",
        description="Produktname / Artikelbezeichnung.",
        json_schema_extra={"enum": allowed_lieferscheine_product_values()},
    )
    no_items: int = Field(
        default=1,
        ge=1,
        alias="no_items",
        description="Anzahl der Position.",
    )
    folder: str = Field(
        default="",
        alias="folder",
        description="Ordnername oder Pfad des Quellbildes.",
    )
    date: str | None = Field(
        default=None,
        alias="date",
        description="Liefer- oder Bestelldatum als YYYY-MM-DD (ohne Uhrzeit), falls lesbar.",
    )

    @field_validator("customer", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("product", mode="before")
    @classmethod
    def _empty_to_unknown_product(cls, value: Any) -> str:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("product")
    @classmethod
    def _validate_product(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Produkt ist erforderlich.")
        allowed = set(allowed_lieferscheine_product_values())
        if allowed and cleaned not in allowed:
            raise ValueError(f"Produkt muss einer der erlaubten Werte sein: {sorted(allowed)}")
        return cleaned

    @field_validator("date", mode="before")
    @classmethod
    def _normalize_date(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if not isinstance(value, str):
            return value

        cleaned = value.strip()
        if not cleaned:
            return None

        compact = cleaned.replace(" ", "")
        normalized = cleaned.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.date().isoformat()
        except ValueError:
            pass

        datetime_formats = (
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d.%m.%y %H:%M",
        )
        for fmt in datetime_formats:
            try:
                parsed = datetime.strptime(cleaned, fmt)
                return parsed.date().isoformat()
            except ValueError:
                continue

        date_formats = (
            "%d.%m.%Y",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d.%m.%y",
        )
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(cleaned, fmt).date()
                return parsed_date.isoformat()
            except ValueError:
                continue

        short_match = re.match(r"^(\d{1,2})\.(\d{1,2})\.?$", compact)
        if short_match:
            day = int(short_match.group(1))
            month = int(short_match.group(2))
            try:
                parsed_date = date(date.today().year, month, day)
                return parsed_date.isoformat()
            except ValueError:
                return None

        return None


class LieferscheinePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    orders: list[LieferscheineItem] = Field(default_factory=list)


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    system_prompt: str = DEFAULT_SYSTEM_PROMPT


def default_output_schema(_default_eintragender: str = "") -> dict[str, list[dict[str, Any]]]:
    return {
        "orders": [
            {
                "customer": None,
                "product": "",
                "no_items": 1,
                "folder": "",
                "date": None,
            }
        ]
    }


def output_schema_json_schema() -> dict[str, Any]:
    return LieferscheinePayload.model_json_schema(by_alias=True)


def field_descriptions_from_model() -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for name, field in LieferscheineItem.model_fields.items():
        alias = field.alias or name
        if field.description:
            descriptions[alias] = field.description
    return descriptions


def validate_lieferscheine_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return LieferscheinePayload().model_dump(by_alias=True)

    normalized_orders: list[dict[str, Any]] = []
    for row in payload.get("orders", []):
        if not isinstance(row, dict):
            continue
        try:
            item = LieferscheineItem.model_validate(row)
        except ValidationError:
            continue
        normalized_orders.append(item.model_dump(by_alias=True))

    output = LieferscheinePayload(
        orders=[LieferscheineItem.model_validate(item) for item in normalized_orders]
    )
    return output.model_dump(by_alias=True)


def validate_lieferscheine_payload_with_report(
    payload: Any,
) -> tuple[dict[str, Any], dict[str, int]]:
    raw_count = 0
    if isinstance(payload, dict) and isinstance(payload.get("orders"), list):
        raw_count = len(payload.get("orders", []))
    normalized = validate_lieferscheine_payload(payload)
    valid_count = len(normalized.get("orders", []))
    return normalized, {
        "raw_orders": raw_count,
        "valid_orders": valid_count,
        "dropped_orders": max(0, raw_count - valid_count),
    }


def load_prompt_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            data = {}
    else:
        data = {}

    try:
        config = PromptConfig.model_validate(
            {
                "system_prompt": data.get("system_prompt", DEFAULT_SYSTEM_PROMPT),
            }
        )
    except ValidationError:
        config = PromptConfig(system_prompt=DEFAULT_SYSTEM_PROMPT)
    return config.model_dump()


def save_prompt_config(config: dict[str, Any]) -> None:
    merged = {"system_prompt": config.get("system_prompt", DEFAULT_SYSTEM_PROMPT)}
    validated = PromptConfig.model_validate(merged)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(validated.model_dump(), handle, ensure_ascii=True, indent=2)


def build_system_prompt_with_descriptions(
    system_prompt: str,
    output_schema: dict[str, Any],
) -> str:
    products = allowed_lieferscheine_product_values()
    base = system_prompt.rstrip()
    if products:
        base = f"{base}\n\nVerfügbare Produkte: {', '.join(products)}"
    return base


def build_transcript_user_prompt(transcript_text: str) -> str:
    return (
        "Transkription:\n"
        f"{transcript_text}\n\n"
        "Hinweis: Extrahiere nur customer, product, no_items und date. "
        "date immer als YYYY-MM-DD ohne Uhrzeit."
    )


def build_image_user_prompt() -> str:
    return DEFAULT_IMAGE_USER_PROMPT


def get_image_extraction_model() -> str:
    return DEFAULT_IMAGE_EXTRACTION_MODEL
