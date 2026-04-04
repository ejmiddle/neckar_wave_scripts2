from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    normalized = cleaned.replace("\xa0", " ")
    normalized = normalized.replace("EUR", "").replace("€", "")
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace(".", "").replace(",", ".") if "," in normalized else normalized

    try:
        return round(float(normalized), 2)
    except ValueError:
        return None


DEFAULT_LOHNKOSTEN_SYSTEM_PROMPT = (
    "Du extrahierst Buchhaltungswerte aus einem Lohnkosten-PDF. "
    "Arbeite strikt faktisch und verwende nur klar sichtbare Werte. "
    "Keine Annahmen, keine Erfindungen. "
    "Gib null zurueck, wenn ein Wert nicht sicher lesbar ist. "
    "Extrahiere die Gesamtsumme aller Lohnueberweisungen, die Zwischensumme fuer Krankenkasse "
    "und die Zwischensumme fuer Finanzamt. "
    "Wenn Summen mehrfach im Dokument vorkommen, nimm nur die eindeutigen, klar bezeichneten Werte."
)
DEFAULT_LOHNKOSTEN_USER_PROMPT = (
    "Analysiere das gesamte PDF als Lohnkosten-Dokument und extrahiere genau die drei benoetigten "
    "Buchhaltungswerte als JSON."
)

DEFAULT_U1_SYSTEM_PROMPT = (
    "Du extrahierst Buchhaltungswerte aus einer U1-PDF-Seite. "
    "Arbeite strikt faktisch und verwende nur klar sichtbare Werte. "
    "Keine Annahmen, keine Erfindungen. "
    "Gib null zurueck, wenn der Erstattungsbeitrag oder die Krankenkasse nicht sicher lesbar ist. "
    "Extrahiere den Erstattungsbeitrag und die Krankenkasse der aktuellen Seite."
)
DEFAULT_U1_USER_PROMPT = (
    "Analysiere genau diese einzelne PDF-Seite und extrahiere den Erstattungsbeitrag "
    "und die Krankenkasse als JSON."
)


class LohnkostenPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    gesamtsumme_lohnueberweisungen: float | None = Field(
        default=None,
        description="Gesamtsumme aller Lohnueberweisungen in EUR.",
    )
    zwischensumme_krankenkasse: float | None = Field(
        default=None,
        description="Zwischensumme der Krankenkassenbeitraege in EUR.",
    )
    zwischensumme_finanzamt: float | None = Field(
        default=None,
        description="Zwischensumme der Beitraege fuer das Finanzamt in EUR.",
    )

    @field_validator(
        "gesamtsumme_lohnueberweisungen",
        "zwischensumme_krankenkasse",
        "zwischensumme_finanzamt",
        mode="before",
    )
    @classmethod
    def _normalize_amount_field(cls, value: Any) -> float | None:
        return _normalize_amount(value)


class U1PagePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    erstattungsbeitrag: float | None = Field(
        default=None,
        description="Erstattungsbeitrag der aktuellen U1-Seite in EUR.",
    )
    krankenkasse: str | None = Field(
        default=None,
        description="Name der Krankenkasse, von der die Erstattung kommt.",
    )

    @field_validator("erstattungsbeitrag", mode="before")
    @classmethod
    def _normalize_amount_field(cls, value: Any) -> float | None:
        return _normalize_amount(value)

    @field_validator("krankenkasse", mode="before")
    @classmethod
    def _normalize_text_field(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        cleaned = str(value).strip()
        return cleaned or None


def default_lohnkosten_output_schema() -> dict[str, Any]:
    return {
        "gesamtsumme_lohnueberweisungen": None,
        "zwischensumme_krankenkasse": None,
        "zwischensumme_finanzamt": None,
    }


def default_u1_output_schema() -> dict[str, Any]:
    return {
        "erstattungsbeitrag": None,
        "krankenkasse": None,
    }


def output_schema_json_schema(target_key: str) -> dict[str, Any]:
    if target_key == "lohnkosten_accounting_v1":
        return LohnkostenPayload.model_json_schema(by_alias=True)
    if target_key == "u1_page_accounting_v1":
        return U1PagePayload.model_json_schema(by_alias=True)
    raise ValueError(f"Unknown output schema target: {target_key}")


def validate_lohnkosten_payload_with_report(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        payload = {}
    normalized = LohnkostenPayload.model_validate(payload).model_dump(by_alias=True)
    return normalized, {"target": "lohnkosten_accounting_v1"}


def validate_u1_page_payload_with_report(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        payload = {}
    normalized = U1PagePayload.model_validate(payload).model_dump(by_alias=True)
    return normalized, {"target": "u1_page_accounting_v1"}
