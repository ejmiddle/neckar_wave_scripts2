from datetime import date

from src.accounting.ready2order import (
    aggregate_ready2order_product_group_sales,
    build_overall_sales_by_period,
    build_ready2order_product_group_summaries,
    fetch_ready2order_invoices,
    fetch_ready2order_invoices_cached_by_day,
    flatten_ready2order_line_items,
    write_ready2order_invoice_cache,
)


def test_ready2order_product_group_summaries_by_period() -> None:
    invoices = [
        {
            "invoice_id": "inv-1",
            "invoice_numberFull": "R-1",
            "invoice_timestamp": "2026-04-01T10:00:00",
            "items": [
                {
                    "item_id": "item-1",
                    "item_timestamp": "2026-04-01T10:00:00",
                    "productGroup_id": "pg-food",
                    "productgroup_name": "Food",
                    "item_name": "Bowl",
                    "item_quantity": "2",
                    "item_total": "20.00",
                    "item_totalNet": "18.69",
                    "item_vat": "1.31",
                },
                {
                    "item_id": "item-2",
                    "item_timestamp": "2026-04-02T10:00:00",
                    "productGroup_id": "pg-drink",
                    "productgroup_name": "Drinks",
                    "item_name": "Coffee",
                    "item_quantity": "1",
                    "item_total": "3.50",
                    "item_totalNet": "2.94",
                    "item_vat": "0.56",
                },
            ],
        },
        {
            "invoice_id": "inv-2",
            "invoice_numberFull": "R-2",
            "invoice_timestamp": "2026-04-08T10:00:00",
            "items": [
                {
                    "item_id": "item-3",
                    "item_timestamp": "2026-04-08T10:00:00",
                    "productGroup_id": "pg-food",
                    "productgroup_name": "Food",
                    "item_name": "Bowl",
                    "item_quantity": "1",
                    "item_total": "10.00",
                    "item_totalNet": "9.35",
                    "item_vat": "0.65",
                }
            ],
        },
    ]

    summaries = build_ready2order_product_group_summaries(invoices)

    monthly_food = summaries["month"].query("period == '2026-04' and product_group_name == 'Food'")
    assert monthly_food.iloc[0]["quantity"] == 3
    assert monthly_food.iloc[0]["gross_sales"] == 30
    assert monthly_food.iloc[0]["invoice_count"] == 2

    weekly = summaries["week"]
    assert set(weekly["period"]) == {"2026-W14", "2026-W15"}

    daily = summaries["day"]
    assert set(daily["period"]) == {"2026-04-01", "2026-04-02", "2026-04-08"}


def test_ready2order_return_items_reduce_quantity_and_totals() -> None:
    line_items = flatten_ready2order_line_items(
        [
            {
                "invoice_id": "inv-1",
                "invoice_timestamp": "2026-04-01T10:00:00",
                "items": [
                    {
                        "item_id": "item-1",
                        "item_timestamp": "2026-04-01T10:00:00",
                        "productGroup_id": "pg-food",
                        "productgroup_name": "Food",
                        "item_quantity": "1",
                        "item_total": "10.00",
                        "item_totalNet": "9.35",
                        "item_vat": "0.65",
                        "item_retour": True,
                    }
                ],
            }
        ]
    )

    summary = aggregate_ready2order_product_group_sales(line_items, "day")

    assert summary.iloc[0]["quantity"] == -1
    assert summary.iloc[0]["gross_sales"] == -10
    assert summary.iloc[0]["net_sales"] == -9.35


def test_ready2order_complete_daily_periods_for_product_groups() -> None:
    line_items = flatten_ready2order_line_items(
        [
            {
                "invoice_id": "inv-1",
                "invoice_timestamp": "2026-04-01T10:00:00",
                "items": [
                    {
                        "item_id": "item-1",
                        "item_timestamp": "2026-04-01T10:00:00",
                        "productGroup_id": "pg-food",
                        "productgroup_name": "Food",
                        "item_quantity": "2",
                        "item_total": "20.00",
                        "item_totalNet": "18.69",
                        "item_vat": "1.31",
                    }
                ],
            }
        ]
    )

    summary = aggregate_ready2order_product_group_sales(
        line_items,
        "day",
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 3),
    )

    assert list(summary.sort_values("period")["period"]) == [
        "2026-04-01",
        "2026-04-02",
        "2026-04-03",
    ]
    zero_day = summary.query("period == '2026-04-02'").iloc[0]
    assert zero_day["quantity"] == 0
    assert zero_day["gross_sales"] == 0


def test_ready2order_overall_sales_by_period_keeps_empty_days() -> None:
    line_items = flatten_ready2order_line_items(
        [
            {
                "invoice_id": "inv-1",
                "invoice_timestamp": "2026-04-02T10:00:00",
                "items": [
                    {
                        "item_id": "item-1",
                        "item_timestamp": "2026-04-02T10:00:00",
                        "productGroup_id": "pg-food",
                        "productgroup_name": "Food",
                        "item_quantity": "1",
                        "item_total": "10.00",
                        "item_totalNet": "9.35",
                        "item_vat": "0.65",
                    }
                ],
            }
        ]
    )
    summary = aggregate_ready2order_product_group_sales(
        line_items,
        "day",
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 3),
    )

    chart_data = build_overall_sales_by_period(
        summary,
        "day",
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 3),
    )

    assert list(chart_data["period"]) == ["2026-04-01", "2026-04-02", "2026-04-03"]
    assert list(chart_data["gross_sales"]) == [0, 10, 0]


def test_ready2order_overall_sales_accepts_string_period_start() -> None:
    summary = aggregate_ready2order_product_group_sales(
        flatten_ready2order_line_items(
            [
                {
                    "invoice_id": "inv-1",
                    "invoice_timestamp": "2026-04-02T10:00:00",
                    "items": [
                        {
                            "item_id": "item-1",
                            "item_timestamp": "2026-04-02T10:00:00",
                            "productGroup_id": "pg-food",
                            "productgroup_name": "Food",
                            "item_quantity": "1",
                            "item_total": "10.00",
                            "item_totalNet": "9.35",
                            "item_vat": "0.65",
                        }
                    ],
                }
            ]
        ),
        "day",
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 3),
    )
    summary["period_start"] = summary["period_start"].astype(str)

    chart_data = build_overall_sales_by_period(
        summary,
        "day",
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 3),
    )

    assert list(chart_data["gross_sales"]) == [0, 10, 0]


def test_ready2order_fetch_invoices_pages_past_misleading_count(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(path, token, *, params=None, base_url=None):
        calls.append(dict(params or {}))
        offset = params["offset"]
        if offset == 0:
            return {
                "count": 100,
                "invoices": [{"invoice_id": f"first-{idx}"} for idx in range(100)],
            }
        if offset == 100:
            return {
                "count": 100,
                "invoices": [{"invoice_id": f"second-{idx}"} for idx in range(20)],
            }
        return {"count": 100, "invoices": []}

    monkeypatch.setattr("src.accounting.ready2order.ready2order_get", fake_get)

    invoices = fetch_ready2order_invoices(
        "token",
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 30),
        limit=255,
    )

    assert len(invoices) == 120
    assert [call["offset"] for call in calls] == [0, 100]
    assert all(call["limit"] == 100 for call in calls)


def test_ready2order_cached_by_day_reuses_cached_days(monkeypatch, tmp_path) -> None:
    write_ready2order_invoice_cache(
        date(2026, 4, 1),
        [{"invoice_id": "cached"}],
        cache_dir=tmp_path,
    )
    calls: list[date] = []

    def fake_fetch(token, *, date_from, date_to, **kwargs):
        calls.append(date_from)
        assert date_from == date_to
        return [{"invoice_id": f"api-{date_from.isoformat()}"}]

    monkeypatch.setattr("src.accounting.ready2order.fetch_ready2order_invoices", fake_fetch)

    invoices, stats = fetch_ready2order_invoices_cached_by_day(
        "token",
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 2),
        cache_dir=tmp_path,
    )

    assert [invoice["invoice_id"] for invoice in invoices] == ["cached", "api-2026-04-02"]
    assert calls == [date(2026, 4, 2)]
    assert stats["cache_hits"] == 1
    assert stats["api_fetches"] == 1


def test_ready2order_cached_by_day_force_refresh_reloads_cached_days(monkeypatch, tmp_path) -> None:
    write_ready2order_invoice_cache(
        date(2026, 4, 1),
        [{"invoice_id": "cached"}],
        cache_dir=tmp_path,
    )
    calls: list[date] = []

    def fake_fetch(token, *, date_from, date_to, **kwargs):
        calls.append(date_from)
        return [{"invoice_id": f"fresh-{date_from.isoformat()}"}]

    monkeypatch.setattr("src.accounting.ready2order.fetch_ready2order_invoices", fake_fetch)

    invoices, stats = fetch_ready2order_invoices_cached_by_day(
        "token",
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 1),
        cache_dir=tmp_path,
        force_refresh=True,
    )

    assert [invoice["invoice_id"] for invoice in invoices] == ["fresh-2026-04-01"]
    assert calls == [date(2026, 4, 1)]
    assert stats["cache_hits"] == 0
    assert stats["api_fetches"] == 1
    assert stats["refreshed_days"] == 1
