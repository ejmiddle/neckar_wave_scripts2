from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DEFAULT_SYSTEM_PROMPT = (
    "Du extrahierst Buchhaltungsinformationen aus einem Amazon-Beleg oder einer Rechnung. "
    "Arbeite strikt faktisch: keine Annahmen, keine Erfindungen. "
    "Nutze ausschliesslich Informationen, die auf dem Dokument klar erkennbar sind. "
    "Wenn ein Feld nicht sicher belegt ist, gib null zurueck. "
    "Wenn ein PDF mehrere Seiten hat, behandle standardmaessig jede Seite als eigenen einzelnen Beleg. "
    "amount ist der Bruttogesamtbetrag in EUR. "
    "vat_rate_percent darf nur 19, 7 oder 0 sein. "
    "purchase_category darf nur 'Sonstiges Material' oder 'Bürobedarf' sein. "
    "Wenn eine konkrete Umsatzsteuer oder Mehrwertsteuer ausgewiesen ist, ist "
    "intra_community_supply false, auch wenn eine USt-IdNr. vorhanden ist. "
    "intra_community_supply ist nur true, wenn der Beleg klar eine innergemeinschaftliche "
    "Lieferung, Reverse-Charge oder eine steuerfreie EU-Lieferung erkennen laesst."
)
DEFAULT_IMAGE_USER_PROMPT = (
    "Analysiere alle Seiten des Amazon-Belegs und extrahiere die wichtigsten "
    "Buchhaltungsfelder. Wenn mehrere Seiten vorhanden sind, behandle standardmaessig "
    "jede Seite als eigenen Beleg. invoice_date muss als YYYY-MM-DD ausgegeben werden."
)

PURCHASE_CATEGORIES = ("Sonstiges Material", "Bürobedarf")
ALLOWED_VAT_RATES = {0, 7, 19}


def _normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    normalized = cleaned.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.date().isoformat()
    except ValueError:
        pass

    for fmt in (
        "%d.%m.%Y",
        "%d.%m.%y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    normalized = cleaned.replace("\xa0", " ")
    normalized = re.sub(r"[^\d,.\-]", "", normalized)
    if not normalized:
        return None

    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif normalized.count(",") == 1:
        normalized = normalized.replace(",", ".")

    try:
        return round(float(normalized), 2)
    except ValueError:
        return None


def _normalize_vat_rate(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        normalized = int(round(float(value)))
        return normalized if normalized in ALLOWED_VAT_RATES else None
    if not isinstance(value, str):
        return None

    cleaned = value.strip().replace("%", "").replace(",", ".")
    if not cleaned:
        return None

    try:
        normalized = int(round(float(cleaned)))
    except ValueError:
        return None
    return normalized if normalized in ALLOWED_VAT_RATES else None


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    cleaned = str(value).strip()
    return cleaned or None


def _normalize_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if cleaned in {"true", "1", "yes", "ja"}:
        return True
    if cleaned in {"false", "0", "no", "nein"}:
        return False
    return None


def _normalize_purchase_category(value: Any) -> str | None:
    cleaned = _normalize_optional_string(value)
    if cleaned is None:
        return None
    folded = cleaned.casefold()
    if folded in {"sonstiges material", "material"}:
        return "Sonstiges Material"
    if folded in {"buerobedarf", "burobedarf", "bürobedarf"}:
        return "Bürobedarf"
    return None


class AmazonReceiptAccountingPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    document_number: str | None = Field(
        default=None,
        alias="document_number",
        description="Rechnungsnummer, Belegnummer oder Dokumentnummer, falls klar sichtbar.",
    )
    seller_name: str | None = Field(
        default=None,
        alias="seller_name",
        description="Name des Verkaeufers oder Lieferanten auf dem Dokument.",
    )
    invoice_date: str | None = Field(
        default=None,
        alias="invoice_date",
        description="Rechnungs- oder Belegdatum als YYYY-MM-DD.",
    )
    amount: float | None = Field(
        default=None,
        alias="amount",
        description="Bruttogesamtbetrag des Belegs in EUR.",
    )
    vat_rate_percent: int | None = Field(
        default=None,
        alias="vat_rate_percent",
        description="Umsatzsteuersatz in Prozent. Nur 19, 7 oder 0.",
    )
    seller_vat_id: str | None = Field(
        default=None,
        alias="seller_vat_id",
        description="USt-IdNr. oder VAT ID des Verkaeufers, falls auf dem Dokument vorhanden.",
    )
    intra_community_supply: bool | None = Field(
        default=None,
        alias="intra_community_supply",
        description="true bei klarer innergemeinschaftlicher Lieferung oder Reverse Charge; false bei ausgewiesener konkreter Umsatzsteuer/Mehrwertsteuer oder klarer regulaerer deutscher USt, auch wenn eine USt-IdNr. vorhanden ist; sonst null.",
    )
    purchase_category: str | None = Field(
        default=None,
        alias="purchase_category",
        description="Passende Einkaufskategorie fuer den Beleg.",
    )
    notes: str | None = Field(
        default=None,
        alias="notes",
        description="Kurzer Hinweis bei Unklarheiten, z.B. mehrere Steuersaetze oder schlecht lesbarer Beleg.",
    )

    @field_validator("document_number", "seller_name", "seller_vat_id", "notes", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _normalize_optional_string(value)

    @field_validator("invoice_date", mode="before")
    @classmethod
    def _validate_date(cls, value: Any) -> str | None:
        return _normalize_date(value)

    @field_validator("amount", mode="before")
    @classmethod
    def _validate_amount(cls, value: Any) -> float | None:
        return _normalize_amount(value)

    @field_validator("vat_rate_percent", mode="before")
    @classmethod
    def _validate_vat_rate(cls, value: Any) -> int | None:
        return _normalize_vat_rate(value)

    @field_validator("intra_community_supply", mode="before")
    @classmethod
    def _validate_optional_bool(cls, value: Any) -> bool | None:
        return _normalize_optional_bool(value)

    @field_validator("purchase_category", mode="before")
    @classmethod
    def _validate_purchase_category(cls, value: Any) -> str | None:
        return _normalize_purchase_category(value)


def default_output_schema() -> dict[str, Any]:
    return AmazonReceiptAccountingPayload().model_dump(by_alias=True)


def output_schema_json_schema() -> dict[str, Any]:
    return AmazonReceiptAccountingPayload.model_json_schema(by_alias=True)


def build_system_prompt_with_descriptions(system_prompt: str) -> str:
    return (
        f"{system_prompt.rstrip()}\n\n"
        "Erlaubte purchase_category-Werte: "
        f"{', '.join(PURCHASE_CATEGORIES)}"
    )


def build_image_user_prompt() -> str:
    return DEFAULT_IMAGE_USER_PROMPT


def validate_amazon_accounting_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return default_output_schema()
    safe_payload = {
        "document_number": _normalize_optional_string(payload.get("document_number")),
        "seller_name": _normalize_optional_string(payload.get("seller_name")),
        "invoice_date": _normalize_date(payload.get("invoice_date")),
        "amount": _normalize_amount(payload.get("amount")),
        "vat_rate_percent": _normalize_vat_rate(payload.get("vat_rate_percent")),
        "seller_vat_id": _normalize_optional_string(payload.get("seller_vat_id")),
        "intra_community_supply": _normalize_optional_bool(payload.get("intra_community_supply")),
        "purchase_category": _normalize_purchase_category(payload.get("purchase_category")),
        "notes": _normalize_optional_string(payload.get("notes")),
    }
    try:
        return AmazonReceiptAccountingPayload.model_validate(safe_payload).model_dump(by_alias=True)
    except ValidationError:
        return default_output_schema()


def validate_amazon_accounting_payload_with_report(
    payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = validate_amazon_accounting_payload(payload)
    populated_fields = sum(1 for value in normalized.values() if value not in {None, ""})
    return normalized, {
        "populated_fields": populated_fields,
        "total_fields": len(normalized),
    }
