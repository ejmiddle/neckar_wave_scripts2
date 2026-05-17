from src.accounting.amazon_customers import build_customer_create_payload


def test_build_customer_create_payload_uses_supplier_category() -> None:
    payload = build_customer_create_payload(
        seller_name="TOP CHARGEUR",
        seller_vat_id="CZ684634178",
        customer_rows=[],
    )

    assert payload["category"] == {"id": 2, "objectName": "Category"}
