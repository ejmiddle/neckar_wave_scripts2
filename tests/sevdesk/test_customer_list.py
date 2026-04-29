from __future__ import annotations

from src.sevdesk.customer_list import (
    add_rechnungen_customer_name,
    load_rechnungen_customer_names,
    remove_rechnungen_customer_name,
    save_rechnungen_customer_names,
)


def test_remove_rechnungen_customer_name_removes_matching_entry(tmp_path) -> None:
    path = tmp_path / "rechnungen_customers.json"
    save_rechnungen_customer_names(["Alpha GmbH", "Beta GmbH"], path=path)

    updated = remove_rechnungen_customer_name("beta gmbh", path=path)

    assert updated == ["Alpha GmbH"]
    assert load_rechnungen_customer_names(path=path) == ["Alpha GmbH"]


def test_remove_rechnungen_customer_name_is_noop_for_missing_entry(tmp_path) -> None:
    path = tmp_path / "rechnungen_customers.json"
    save_rechnungen_customer_names(["Alpha GmbH"], path=path)

    updated = remove_rechnungen_customer_name("Gamma GmbH", path=path)

    assert updated == ["Alpha GmbH"]


def test_add_rechnungen_customer_name_still_deduplicates(tmp_path) -> None:
    path = tmp_path / "rechnungen_customers.json"
    save_rechnungen_customer_names(["Alpha GmbH"], path=path)

    updated = add_rechnungen_customer_name("alpha gmbh", path=path)

    assert updated == ["Alpha GmbH"]
