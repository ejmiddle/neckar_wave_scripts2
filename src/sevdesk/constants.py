from pathlib import Path

DEFAULT_BASE_URL = "https://my.sevdesk.de/api/v1"
SEVDESK_CONFIG_DIR = Path("config/sevdesk")
SEVDESK_TEMPLATE_DIR = SEVDESK_CONFIG_DIR / "templates"
SEVDESK_EXAMPLES_DIR = SEVDESK_CONFIG_DIR / "examples"

DEFAULT_TEMPLATE_PATH = SEVDESK_EXAMPLES_DIR / "beleg_create_input.example.json"
DEFAULT_APITEST_TEMPLATE_PATH = SEVDESK_EXAMPLES_DIR / "beleg_create_input.apitest_verrechnungskonto.json"
DEFAULT_BUCHUNGGSKONTEN_EXPORT_PATH = Path("data/sevdesk/exports/account_guidance/buchunggskonten.json")
DEFAULT_ZAHLUNGSKONTEN_EXPORT_PATH = Path("data/sevdesk/exports/account_guidance/zahlungskonten.json")
RECHNUNGEN_CUSTOMERS_PATH = Path("data/sevdesk/state/rechnungen_customers.json")

MONTHLY_UMSATZ_TEMPLATE_PATH = SEVDESK_TEMPLATE_DIR / "umsatz_voucher_template.json"
VOUCHER_SOLD_TEMPLATE_PATH = SEVDESK_TEMPLATE_DIR / "voucher_verkauft_voucher_template.json"
VOUCHER_REDEEMED_TEMPLATE_PATH = SEVDESK_TEMPLATE_DIR / "voucher_eingeloest_voucher_template.json"
KRANKENKASSE_U1_TEMPLATE_PATH = SEVDESK_TEMPLATE_DIR / "krankenkasse_u1_voucher_template.json"
LOHN_TEMPLATE_PATH = SEVDESK_TEMPLATE_DIR / "lohn_voucher_template.json"
KRANKENKASSE_TEMPLATE_PATH = SEVDESK_TEMPLATE_DIR / "krankenkasse_voucher_template.json"
STEUER_LOHN_TEMPLATE_PATH = SEVDESK_TEMPLATE_DIR / "steuer_lohn_voucher_template.json"
AMAZON_19_VAT_TEMPLATE_PATH = SEVDESK_TEMPLATE_DIR / "amazon_19_vat_voucher_template.json"
AMAZON_INNER_COMMUNITY_TEMPLATE_PATH = (
    SEVDESK_TEMPLATE_DIR / "amazon_innergemeinschaftlich_voucher_template.json"
)
B2B_INVOICE_TEMPLATE_PATH = SEVDESK_TEMPLATE_DIR / "b2b_invoice_template.json"

FALLBACK_ACCOUNTING_TYPE_ID = "2"
FALLBACK_ACCOUNTING_TYPE_NAME = "Sonstiges"
