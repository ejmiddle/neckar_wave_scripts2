from pathlib import Path

DEFAULT_BASE_URL = "https://my.sevdesk.de/api/v1"
DEFAULT_TEMPLATE_PATH = Path("data/sevdesk/beleg_create_input.example.json")
DEFAULT_BUCHUNGGSKONTEN_EXPORT_PATH = Path("data/sevdesk/informationen/buchunggskonten.json")
DEFAULT_ZAHLUNGSKONTEN_EXPORT_PATH = Path("data/sevdesk/informationen/zahlungskonten.json")

FALLBACK_ACCOUNTING_TYPE_ID = "2"
FALLBACK_ACCOUNTING_TYPE_NAME = "Sonstiges"

