from datetime import date

from src.sevdesk import api


def test_request_voucher_by_id_requests_depth_one(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_sevdesk_request(method, base_url, token, path, *, params=None, payload=None):
        captured["method"] = method
        captured["base_url"] = base_url
        captured["token"] = token
        captured["path"] = path
        captured["params"] = params
        captured["payload"] = payload
        return {"objects": {"id": "123"}}

    monkeypatch.setattr(api, "sevdesk_request", fake_sevdesk_request)

    result = api.request_voucher_by_id("https://example.test", "secret", "123")

    assert result == {"id": "123"}
    assert captured["method"] == "GET"
    assert captured["path"] == "/Voucher/123"
    assert captured["params"] == {"depth": "1"}


def test_request_voucher_position_page_passes_filters(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_sevdesk_request(method, base_url, token, path, *, params=None, payload=None):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params
        return {"objects": [{"id": "pos-1"}]}

    monkeypatch.setattr(api, "sevdesk_request", fake_sevdesk_request)

    result = api.request_voucher_position_page(
        "https://example.test",
        "secret",
        200,
        0,
        filters={"voucher[id]": "123", "depth": "1"},
    )

    assert result == [{"id": "pos-1"}]
    assert captured["method"] == "GET"
    assert captured["path"] == "/VoucherPos"
    assert captured["params"] == {
        "limit": 200,
        "offset": 0,
        "voucher[id]": "123",
        "depth": "1",
    }


def test_attach_voucher_positions_merges_positions(monkeypatch) -> None:
    def fake_request_voucher_positions(base_url, token, *, filters=None):
        if filters == {
            "voucher[id]": "123",
            "voucher[objectName]": "Voucher",
            "depth": "1",
        }:
            return [{"id": "pos-1", "accountingType": {"id": "7001", "name": "Wareneingang"}}]
        return []

    monkeypatch.setattr(api, "request_voucher_positions", fake_request_voucher_positions)

    result = api.attach_voucher_positions(
        "https://example.test",
        "secret",
        [{"id": "123", "description": "FAC0001"}],
    )

    assert result == [
        {
            "id": "123",
            "description": "FAC0001",
            "voucherPos": [{"id": "pos-1", "accountingType": {"id": "7001", "name": "Wareneingang"}}],
        }
    ]


def test_request_vouchers_for_contacts_merges_dedupes_and_limits(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_request_vouchers(base_url, token, limit, *, filters=None, fetch_all=True):
        calls.append(
            {
                "base_url": base_url,
                "token": token,
                "limit": limit,
                "filters": filters,
                "fetch_all": fetch_all,
            }
        )
        contact_id = filters["contact[id]"]
        if contact_id == "c1":
            return [
                {"id": "2", "create": "2026-05-02T00:00:00+02:00"},
                {"id": "1", "create": "2026-05-01T00:00:00+02:00"},
            ]
        return [
            {"id": "2", "create": "2026-05-02T00:00:00+02:00"},
            {"id": "3", "create": "2026-05-03T00:00:00+02:00"},
        ]

    monkeypatch.setattr(api, "request_vouchers", fake_request_vouchers)

    result = api.request_vouchers_for_contacts(
        "https://example.test",
        "secret",
        2,
        ["c1", "c2"],
        filters={"status": "100"},
        fetch_all=False,
    )

    assert [row["id"] for row in result] == ["3", "2"]
    assert calls == [
        {
            "base_url": "https://example.test",
            "token": "secret",
            "limit": 2,
            "filters": {
                "status": "100",
                "contact[id]": "c1",
                "contact[objectName]": "Contact",
            },
            "fetch_all": False,
        },
        {
            "base_url": "https://example.test",
            "token": "secret",
            "limit": 2,
            "filters": {
                "status": "100",
                "contact[id]": "c2",
                "contact[objectName]": "Contact",
            },
            "fetch_all": False,
        },
    ]


def test_fetch_all_transactions_for_check_account_filters_date_range_and_stops(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []
    pages = [
        [
            {
                "id": "newer",
                "valueDate": "2026-05-10T00:00:00+02:00",
                "checkAccount": {"id": "1", "objectName": "CheckAccount"},
            },
            {
                "id": "inside",
                "valueDate": "2026-04-01T00:00:00+02:00",
                "checkAccount": {"id": "1", "objectName": "CheckAccount"},
            },
            {
                "id": "other-account",
                "valueDate": "2026-04-01T00:00:00+02:00",
                "checkAccount": {"id": "2", "objectName": "CheckAccount"},
            },
            {
                "id": "older",
                "valueDate": "2026-02-28T00:00:00+01:00",
                "checkAccount": {"id": "1", "objectName": "CheckAccount"},
            },
        ],
        [
            {
                "id": "not-loaded",
                "valueDate": "2026-02-01T00:00:00+01:00",
                "checkAccount": {"id": "1", "objectName": "CheckAccount"},
            },
        ],
    ]

    def fake_request_check_account_transactions(base_url, token, limit, offset, sort):
        calls.append((limit, offset))
        return pages[offset // limit]

    monkeypatch.setattr(
        api,
        "request_check_account_transactions",
        fake_request_check_account_transactions,
    )

    result = api.fetch_all_transactions_for_check_account(
        "https://example.test",
        "secret",
        "1",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 4, 30),
        page_size=4,
    )

    assert [row["id"] for row in result] == ["inside"]
    assert calls == [(4, 0)]
