from src import bestellungen_analyse


def test_sync_default_title_selection_restores_available_defaults(monkeypatch):
    session_state = {"shopify_brot_titles": []}
    monkeypatch.setattr(bestellungen_analyse.st, "session_state", session_state)

    bestellungen_analyse.sync_default_title_selection(
        "shopify_brot_titles",
        [
            "Finca Milan | Kolumbien",
            "Gutes Brot nach Ziegelhausen/Schlierbach",
            "Unsere Brote",
        ],
        bestellungen_analyse.BREAD_PRODUCT_TITLES,
    )

    assert session_state["shopify_brot_titles"] == [
        "Gutes Brot nach Ziegelhausen/Schlierbach",
        "Unsere Brote",
    ]


def test_sync_default_title_selection_drops_titles_missing_from_current_orders(
    monkeypatch,
):
    session_state = {
        "shopify_brot_titles": [
            "Gutes Brot nach Ziegelhausen/Schlierbach",
            "Altes Brotprodukt",
        ]
    }
    monkeypatch.setattr(bestellungen_analyse.st, "session_state", session_state)

    bestellungen_analyse.sync_default_title_selection(
        "shopify_brot_titles",
        ["Gutes Brot nach Ziegelhausen/Schlierbach"],
        bestellungen_analyse.BREAD_PRODUCT_TITLES,
    )

    assert session_state["shopify_brot_titles"] == [
        "Gutes Brot nach Ziegelhausen/Schlierbach"
    ]
