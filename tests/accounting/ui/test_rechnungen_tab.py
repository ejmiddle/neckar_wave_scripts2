from __future__ import annotations

from src.accounting.ui.rechnungen_tab import (
    _build_invoice_position_update_payload,
    _clean_invoice_position_for_save,
    _product_sort_key,
)


def test_product_sort_key_sorts_by_name_before_article_number() -> None:
    products = [
        {"id": "3", "articleNumber": "200", "name": "Zebra"},
        {"id": "1", "articleNumber": "010", "name": "Alpha"},
        {"id": "2", "articleNumber": "001", "name": "Beta"},
    ]

    sorted_products = sorted(products, key=_product_sort_key)

    assert [product["name"] for product in sorted_products] == ["Alpha", "Beta", "Zebra"]


def test_clean_invoice_position_for_save_uses_selected_product_tax_and_preserves_discount() -> None:
    payload = _clean_invoice_position_for_save(
        "123",
        {
            "id": "456",
            "name": "Old product",
            "description": "Old description",
            "price": "10.0",
            "taxRate": "7",
            "discountedValue": "5",
            "isPercentage": "1",
        },
        {
            "id": "789",
            "objectName": "Part",
            "name": "New product",
            "description": "New description",
            "priceNet": "20.0",
            "taxRate": "19",
        },
        quantity=2.0,
        fallback_unity={"id": 1, "objectName": "Unity"},
        show_net=True,
    )

    assert payload["taxRate"] == 19.0
    assert payload["discountedValue"] == "5"
    assert payload["isPercentage"] == "1"
    assert payload["quantity"] == 2.0
    assert payload["part"] == {"id": "789", "objectName": "Part"}


def test_build_invoice_position_update_payload_applies_tax_and_discount_overrides() -> None:
    payload = _build_invoice_position_update_payload(
        {
            "id": "123",
            "showNet": "1",
        },
        [
            {
                "position": {
                    "id": "456",
                    "name": "Position",
                    "taxRate": "0",
                },
                "selected_product": {
                    "id": "789",
                    "name": "Product",
                    "priceNet": "4.67",
                    "taxRate": "0",
                },
                "quantity": 32.0,
                "is_new": False,
            }
        ],
        tax_rate_override=7.0,
        discount_override=20.0,
    )

    position = payload["invoicePosSave"][0]
    assert position["taxRate"] == 7.0
    assert position["discountedValue"] == "20.0"
    assert position["isPercentage"] == "1"
