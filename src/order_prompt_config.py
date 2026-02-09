from __future__ import annotations

import functools
import json
from copy import deepcopy
from pathlib import Path

import pandas as pd

from src.app_paths import DATA_DIR

CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "order_extraction_prompt.json"

DEFAULT_SYSTEM_PROMPT = (
    "Du extrahierst strukturierte Bestelldaten aus einer Bäckerei-Transkription. "
    "Gib genau ein JSON-Objekt zurück (keine Mehrfachausgaben). "
    "Gib ausschließlich JSON zurück. Halte dich strikt an diese Struktur "
    "und verwende die Feldnamen exakt wie vorgegeben. "
    "WICHTIG: Für jedes Produkt eine eigene Zeile (ein Objekt in orders) "
    "mit der jeweiligen Menge."
)

DEFAULT_OUTPUT_SCHEMA = {
    "orders": [
        {
            "Notiz/Kunde": "",
            "Abgeholt": "Nein",
            "Datum": None,
            "Menge": 8,
            "Produkt": "",
            "Eintragender": "",
            "Wohin": "Wieblingen",
            "Zahlung": "Vor Ort",
        }
    ]
}

DEFAULT_ALLOWED_VALUES = {
    "Abgeholt": ["Ja", "Nein"],
    "Produkt": ["Kardamomknoten", "Zimtknoten", "Rustico", "Classico", "Baguette"],
    "Zahlung": ["Vor Ort"],
}

DEFAULT_FIELD_DESCRIPTIONS = {
    "Notiz/Kunde": "Freitext mit der Info für wen oder wer bestellt hat oder sonstige Notizen zur Bestellung.",
    "Abgeholt": "Ob die Bestellung abgeholt wurde (Ja/Nein).",
    "Datum": "Datum und Uhrzeit der Abholung oder Bestellung.",
    "Menge": "Anzahl der Einheiten für das Produkt.",
    "Produkt": "Produktname wie im Sortiment benannt.",
    "Eintragender": "Person, die die Bestellung erfasst hat.",
    "Wohin": "Lieferort oder Abholort.",
    "Zahlung": "Zahlungsart (z.B. Vor Ort, Rechnung, schon bezahlt).",
}

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
    products = (
        df["Produktbezeichnung"]
        .dropna()
        .astype(str)
        .map(str.strip)
    )
    products = [value for value in products.tolist() if value]
    seen = set()
    unique = []
    for value in products:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


_products_from_excel = _load_products_for_defaults()
if _products_from_excel:
    DEFAULT_ALLOWED_VALUES["Produkt"] = _products_from_excel


def _ensure_dict(value, fallback):
    return value if isinstance(value, dict) else deepcopy(fallback)


def _ensure_str(value, fallback):
    return value if isinstance(value, str) else fallback


def load_prompt_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            data = {}
    else:
        data = {}

    return {
        "system_prompt": _ensure_str(data.get("system_prompt"), DEFAULT_SYSTEM_PROMPT),
        "output_schema": _ensure_dict(data.get("output_schema"), DEFAULT_OUTPUT_SCHEMA),
        "allowed_values": _ensure_dict(data.get("allowed_values"), DEFAULT_ALLOWED_VALUES),
        "field_descriptions": _ensure_dict(
            data.get("field_descriptions"), DEFAULT_FIELD_DESCRIPTIONS
        ),
    }


def save_prompt_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=True, indent=2)


def apply_default_eintragender(output_schema: dict, default_eintragender: str) -> dict:
    schema = deepcopy(output_schema)
    if not default_eintragender:
        return schema
    orders = schema.get("orders")
    if isinstance(orders, list) and orders:
        first = orders[0]
        if isinstance(first, dict):
            first["Eintragender"] = default_eintragender
    return schema


def build_system_prompt(system_prompt: str, output_schema: dict, allowed_values: dict) -> str:
    prompt = system_prompt.rstrip()
    prompt += f" Struktur: {json.dumps(output_schema, ensure_ascii=True)}"
    if allowed_values:
        filtered = {
            key: value
            for key, value in allowed_values.items()
            if isinstance(value, list) and value
        }
        if filtered:
            prompt += f" Erlaubte Werte: {json.dumps(filtered, ensure_ascii=True)}"
    return prompt


def build_system_prompt_with_descriptions(
    system_prompt: str,
    output_schema: dict,
    allowed_values: dict,
    field_descriptions: dict,
) -> str:
    prompt = build_system_prompt(system_prompt, output_schema, allowed_values)
    if field_descriptions:
        filtered = {
            key: value
            for key, value in field_descriptions.items()
            if isinstance(value, str) and value.strip()
        }
        if filtered:
            prompt += f" Feld-Erklärungen: {json.dumps(filtered, ensure_ascii=True)}"
    return prompt
