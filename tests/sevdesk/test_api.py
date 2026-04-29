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
