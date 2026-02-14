from __future__ import annotations

import functools
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.app_paths import DATA_DIR

CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "order_extraction_prompt.json"

DEFAULT_SYSTEM_PROMPT = (
    "Du extrahierst Bestelldaten fuer eine Baeckerei aus bereitgestelltem Inhalt. "
    "Arbeite strikt faktisch: nichts erfinden, nichts raten. "
    "Nutze ausschliesslich das vorgegebene Tool-Schema und erfasse pro Bestellzeile genau ein Produkt. "
    "Wenn Information fehlt, nutze Feld-Defaults, sonst null."
)
DEFAULT_IMAGE_USER_PROMPT = (
    "Analysiere das Foto eines handgeschriebenen Bestellzettels "
    "und extrahiere alle Bestellungen in `orders`."
)
# DEFAULT_IMAGE_EXTRACTION_MODEL = "gpt-5.2-mini"
DEFAULT_IMAGE_EXTRACTION_MODEL = "gpt-4o"


@functools.lru_cache(maxsize=1)
def load_product_list() -> pd.DataFrame:
    file_path = DATA_DIR / "Produktliste_Order_Erfassung.xlsx"
    return pd.read_excel(file_path)


def _load_products_for_defaults() -> list[str]:
    try:
        df = load_product_list()
    except FileNotFoundError:
        return []
    if "Produktbezeichnung" not in df.columns:
        return []

    products = df["Produktbezeichnung"].dropna().astype(str).map(str.strip)
    products = [value for value in products.tolist() if value]

    seen = set()
    unique = []
    for value in products:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def allowed_product_values() -> list[str]:
    fallback = ["Kardamomknoten", "Zimtknoten", "Rustico", "Classico", "Baguette"]
    values = _load_products_for_defaults()
    return values or fallback



class OrderItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    notiz_kunde: str | None = Field(
        default=None,
        alias="Notiz/Kunde",
        description="Freitext mit der allgemeinen Infos zur Bestellung: Wer hat bestellt? Was hat es mit der Bestellung auf sich? Worauf ist zu achten?",
    )
    abgeholt: Literal["Ja", "Nein"] = Field(
        default="Nein",
        alias="Abgeholt",
        description="Ob die Bestellung abgeholt wurde (Ja/Nein).",
    )
    datum: str | None = Field(
        default=None,
        alias="Datum",
        description="Datum und Uhrzeit der Abholung oder Bestellung.",
    )
    menge: int = Field(
        ge=1,
        alias="Menge",
        description="Anzahl der Einheiten fuer das Produkt.",
    )
    produkt: str = Field(
        alias="Produkt",
        description="Produkte wie im Sortiment, z.B, Classico, Rustico, ...",
        json_schema_extra={"enum": allowed_product_values()},
    )
    eintragender: str | None = Field(
        default=None,
        alias="Eintragender",
        description="Person, die die Bestellung erfasst hat.",
    )
    wohin: str | None = Field(
        default="Wieblingen",
        alias="Wohin",
        description="Lieferort oder Abholort.",
    )
    zahlung: Literal["Vor Ort", "Online", "Per Rechnung", "Schon bezahlt", "Unklar"] | None = Field(
        default="Vor Ort",
        alias="Zahlung",
        description="Zahlungsart der Bestellung.",
    )

    @field_validator("notiz_kunde", "eintragender", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("zahlung", mode="before")
    @classmethod
    def _normalize_zahlung(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if not normalized:
            return None
        mapping = {
            "vor ort": "Vor Ort",
            "online": "Online",
            "per rechnung": "Per Rechnung",
            "rechnung": "Per Rechnung",
            "schon bezahlt": "Schon bezahlt",
            "bezahlt": "Schon bezahlt",
            "unklar": "Unklar",
        }
        return mapping.get(normalized, value)

    @field_validator("datum", mode="before")
    @classmethod
    def _normalize_datum(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(microsecond=0).isoformat()
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time()).isoformat()
        if not isinstance(value, str):
            return value

        cleaned = value.strip()
        if not cleaned:
            return None
        compact = cleaned.replace(" ", "")

        normalized = cleaned.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.replace(microsecond=0).isoformat()
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
                return parsed.replace(microsecond=0).isoformat()
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
                return datetime.combine(parsed_date, datetime.min.time()).isoformat()
            except ValueError:
                continue

        short_match = re.match(r"^(\d{1,2})\.(\d{1,2})\.?$", compact)
        if short_match:
            day = int(short_match.group(1))
            month = int(short_match.group(2))
            try:
                parsed_date = date(date.today().year, month, day)
                return datetime.combine(parsed_date, datetime.min.time()).isoformat()
            except ValueError:
                return None

        return None

    @field_validator("produkt")
    @classmethod
    def _validate_produkt(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Produkt ist erforderlich.")
        allowed = set(allowed_product_values())
        if allowed and cleaned not in allowed:
            raise ValueError(f"Produkt muss einer der erlaubten Werte sein: {sorted(allowed)}")
        return cleaned


class OrdersPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    orders: list[OrderItem] = Field(default_factory=list)


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    system_prompt: str = DEFAULT_SYSTEM_PROMPT


def default_output_schema(default_eintragender: str = "") -> dict[str, list[dict[str, Any]]]:
    return {
        "orders": [
            {
                "Notiz/Kunde": None,
                "Abgeholt": "Nein",
                "Datum": None,
                "Menge": 1,
                "Produkt": "",
                "Eintragender": default_eintragender or None,
                "Wohin": "Wieblingen",
                "Zahlung": "Vor Ort",
            }
        ]
    }


def order_field_names() -> list[str]:
    return [field.alias or name for name, field in OrderItem.model_fields.items()]


def output_schema_json_schema() -> dict[str, Any]:
    return OrdersPayload.model_json_schema(by_alias=True)


def field_descriptions_from_model() -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for name, field in OrderItem.model_fields.items():
        alias = field.alias or name
        if field.description:
            descriptions[alias] = field.description
    return descriptions


DEFAULT_OUTPUT_SCHEMA = default_output_schema()
DEFAULT_PROMPT_CONFIG = PromptConfig(system_prompt=DEFAULT_SYSTEM_PROMPT)


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
        config = DEFAULT_PROMPT_CONFIG
    return config.model_dump()


def save_prompt_config(config: dict[str, Any]) -> None:
    merged = {
        "system_prompt": config.get("system_prompt", DEFAULT_SYSTEM_PROMPT),
    }
    validated = PromptConfig.model_validate(merged)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(validated.model_dump(), handle, ensure_ascii=True, indent=2)


def validate_orders_payload(
    payload: Any,
    *,
    default_eintragender: str = "",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return OrdersPayload().model_dump(by_alias=True)

    normalized_orders: list[dict[str, Any]] = []
    for row in payload.get("orders", []):
        if not isinstance(row, dict):
            continue
        candidate = dict(row)
        if default_eintragender and not candidate.get("Eintragender"):
            candidate["Eintragender"] = default_eintragender
        try:
            item = OrderItem.model_validate(candidate)
        except ValidationError:
            continue
        normalized_orders.append(item.model_dump(by_alias=True))

    output = OrdersPayload(
        orders=[OrderItem.model_validate(order) for order in normalized_orders]
    )
    return output.model_dump(by_alias=True)


def validate_orders_payload_with_report(
    payload: Any,
    *,
    default_eintragender: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    raw_count = 0
    if isinstance(payload, dict) and isinstance(payload.get("orders"), list):
        raw_count = len(payload.get("orders", []))
    normalized = validate_orders_payload(payload, default_eintragender=default_eintragender)
    valid_count = len(normalized.get("orders", []))
    return normalized, {
        "raw_orders": raw_count,
        "valid_orders": valid_count,
        "dropped_orders": max(0, raw_count - valid_count),
    }


def build_system_prompt(system_prompt: str, output_schema: dict[str, Any]) -> str:
    _ = output_schema
    return system_prompt.rstrip()


def build_system_prompt_with_descriptions(
    system_prompt: str,
    output_schema: dict[str, Any],
) -> str:
    return build_system_prompt(system_prompt, output_schema)


def build_image_user_prompt() -> str:
    return DEFAULT_IMAGE_USER_PROMPT


def build_transcript_user_prompt(transcript_text: str) -> str:
    return (
        "Transkription:\n"
        f"{transcript_text}\n\n"
        "Hinweis: Es kann mehrere Bestellungen geben. "
        "Wenn Felder fehlen, nutze die Default-Werte aus der Struktur."
    )


def get_image_extraction_model() -> str:
    return DEFAULT_IMAGE_EXTRACTION_MODEL
