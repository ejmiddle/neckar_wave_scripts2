from __future__ import annotations

import json

import pandas as pd

from src.accounting import zeiterfassung_evaluation
from src.accounting.zeiterfassung_evaluation import (
    build_festangestellte_hours_evaluation,
    build_festangestellte_weekly_hours_evaluation,
    database_cache_key,
    evaluate_hours,
    extract_notion_id,
    load_cached_export,
    parse_month_start,
    prune_stale_cached_exports,
    shift_cluster,
    workdays_without_bw_holidays,
)


def test_extract_notion_id_from_page_url() -> None:
    assert (
        extract_notion_id(
            "https://www.notion.so/suedseite/Stunden-Erfassung-Altstadt-1154e28bdf9e8115a0fef60ec21bcbca"
        )
        == "1154e28bdf9e8115a0fef60ec21bcbca"
    )


def test_discover_child_databases_only_uses_direct_page_children(monkeypatch) -> None:
    calls = []

    def fake_iter_block_children(token: str, block_id: str):
        calls.append((token, block_id))
        yield {
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "type": "child_database",
            "child_database": {"title": "Visible DB 2026"},
            "has_children": False,
        }
        yield {
            "id": "ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee",
            "type": "child_page",
            "child_page": {"title": "Nested page"},
            "has_children": True,
        }

    monkeypatch.setattr(zeiterfassung_evaluation, "_iter_block_children", fake_iter_block_children)

    databases = zeiterfassung_evaluation.discover_child_databases(
        "1154e28bdf9e8115a0fef60ec21bcbca",
        token="token",
        source_label="ALT",
    )

    assert calls == [("token", "1154e28bdf9e8115a0fef60ec21bcbca")]
    assert databases == [
        zeiterfassung_evaluation.NotionDatabaseRef(
            database_id="aaaaaaaabbbbccccddddeeeeeeeeeeee",
            title="Visible DB 2026",
            source_label="ALT",
        )
    ]


def test_evaluate_hours_uses_trimmed_employee_column_name() -> None:
    df = pd.DataFrame(
        [
            {
                "_database_title": "Zeiterfassung Februar 2026",
                "_source_location": "ALT",
                "Worked Hours": "4.5",
                "Mitarbeiter ": "Ava",
                "Shift": '["Bakery"]',
            },
            {
                "_database_title": "Zeiterfassung Februar 2026",
                "_source_location": "ALT",
                "Worked Hours": 2,
                "Mitarbeiter ": "Ava",
                "Shift": '["Roasting etc."]',
            },
            {
                "_database_title": "Zeiterfassung März 2026",
                "_source_location": "WIE",
                "Worked Hours": 3,
                "Mitarbeiter ": "Ben",
                "Shift": '["Barista"]',
            },
        ]
    )

    result = evaluate_hours(df)

    assert result.total_hours == 9.5
    assert result.row_count == 3
    assert result.hours_by_employee.to_dict("records") == [
        {"Mitarbeiter": "Ava", "Stunden": 6.5},
        {"Mitarbeiter": "Ben", "Stunden": 3.0},
    ]
    assert result.hours_by_month_location_shift.to_dict("records") == [
        {
            "Monat": "Zeiterfassung Februar 2026",
            "Location": "ALT",
            "Shift": "Bakery",
            "Stunden": 4.5,
        },
        {
            "Monat": "Zeiterfassung Februar 2026",
            "Location": "ALT",
            "Shift": "Roasting",
            "Stunden": 2.0,
        },
        {
            "Monat": "Zeiterfassung März 2026",
            "Location": "WIE",
            "Shift": "Service",
            "Stunden": 3.0,
        },
    ]
    assert result.hours_by_month_employee.to_dict("records") == [
        {"Monat": "Zeiterfassung Februar 2026", "Mitarbeiter": "Ava", "Stunden": 6.5},
        {"Monat": "Zeiterfassung März 2026", "Mitarbeiter": "Ben", "Stunden": 3.0},
    ]
    assert result.shift_value_overview.to_dict("records") == [
        {"Shift Value": "Bakery", "Cluster": "Bakery", "Eintraege": 1, "Stunden": 4.5},
        {"Shift Value": "Roasting etc.", "Cluster": "Roasting", "Eintraege": 1, "Stunden": 2.0},
        {"Shift Value": "Barista", "Cluster": "Service", "Eintraege": 1, "Stunden": 3.0},
    ]


def test_shift_cluster_rules() -> None:
    assert shift_cluster("Bakery") == "Bakery"
    assert shift_cluster("Bakery helper") == "Service"
    assert shift_cluster("Roasting etc.") == "Roasting"
    assert shift_cluster("Coffee roasting") == "Roasting"
    assert shift_cluster("Barista") == "Service"


def test_workdays_without_bw_holidays_deducts_weekday_holidays() -> None:
    assert workdays_without_bw_holidays(pd.Timestamp("2026-05-01")) == 18


def test_parse_month_start_requires_clear_month_and_year() -> None:
    assert parse_month_start("Zeiterfassung Mai 2026") == pd.Timestamp("2026-05-01")
    assert parse_month_start("Zeiterfassung 2026-05 ALT") == pd.Timestamp("2026-05-01")
    assert pd.isna(parse_month_start("Zeiterfassung aktuell"))


def test_build_festangestellte_hours_evaluation_compares_target_and_actual() -> None:
    actual = pd.DataFrame(
        [
            {"Monat": "Zeiterfassung April 2026", "Mitarbeiter": "Ava", "Stunden": 140.0},
            {"Monat": "Zeiterfassung April 2026", "Mitarbeiter": "Ben", "Stunden": 90.0},
            {"Monat": "Zeiterfassung Mai 2026", "Mitarbeiter": "Ava", "Stunden": 120.0},
        ]
    )
    employees = pd.DataFrame(
        [
            {"Mitarbeiter": "Ava", "Wochenstunden": 40.0, "Taegliche Sollstunden": 8.0},
            {"Mitarbeiter": "Ben", "Wochenstunden": 20.0, "Taegliche Sollstunden": 4.0},
        ]
    )

    result = build_festangestellte_hours_evaluation(actual, employees)

    assert result.to_dict("records") == [
        {
            "Monat": "Zeiterfassung April 2026",
            "Mitarbeiter": "Ava",
            "Wochenstunden": 40.0,
            "Arbeitstage": 20,
            "Sollstunden": 160.0,
            "Iststunden": 140.0,
            "Bereinigt": 124.0,
            "Differenz": -20.0,
            "Erfuellung %": 87.5,
        },
        {
            "Monat": "Zeiterfassung April 2026",
            "Mitarbeiter": "Ben",
            "Wochenstunden": 20.0,
            "Arbeitstage": 20,
            "Sollstunden": 80.0,
            "Iststunden": 90.0,
            "Bereinigt": 82.0,
            "Differenz": 10.0,
            "Erfuellung %": 112.5,
        },
        {
            "Monat": "Zeiterfassung Mai 2026",
            "Mitarbeiter": "Ava",
            "Wochenstunden": 40.0,
            "Arbeitstage": 18,
            "Sollstunden": 144.0,
            "Iststunden": 120.0,
            "Bereinigt": 105.6,
            "Differenz": -24.0,
            "Erfuellung %": 83.3,
        },
        {
            "Monat": "Zeiterfassung Mai 2026",
            "Mitarbeiter": "Ben",
            "Wochenstunden": 20.0,
            "Arbeitstage": 18,
            "Sollstunden": 72.0,
            "Iststunden": 0.0,
            "Bereinigt": -7.2,
            "Differenz": -72.0,
            "Erfuellung %": 0.0,
        },
    ]


def test_build_festangestellte_weekly_hours_evaluation_uses_only_full_weeks() -> None:
    analysis = pd.DataFrame(
        [
            {
                "_month_label": "Zeiterfassung April 2026",
                "Mitarbeiter": "Ava",
                "Worked Hours": 12.0,
                "Date": "2026-04-06",
            },
            {
                "_month_label": "Zeiterfassung April 2026",
                "Mitarbeiter": "Ava",
                "Worked Hours": 40.0,
                "Date": "2026-04-08T09:00:00.000+02:00 -> 2026-04-08T17:00:00.000+02:00",
            },
            {
                "_month_label": "Zeiterfassung April 2026",
                "Mitarbeiter": "Ben",
                "Worked Hours": 25.0,
                "Date": "2026-04-09",
            },
            {
                "_month_label": "Zeiterfassung April 2026",
                "Mitarbeiter": "Ava",
                "Worked Hours": 99.0,
                "Date": "2026-04-01",
            },
        ]
    )
    employees = pd.DataFrame(
        [
            {"Mitarbeiter": "Ava", "Wochenstunden": 40.0, "Taegliche Sollstunden": 8.0},
            {"Mitarbeiter": "Ben", "Wochenstunden": 20.0, "Taegliche Sollstunden": 4.0},
        ]
    )

    result = build_festangestellte_weekly_hours_evaluation(
        analysis,
        hours_column="Worked Hours",
        employee_column="Mitarbeiter",
        date_column="Date",
        employees=employees,
    )

    assert result.to_dict("records") == [
        {
            "Woche": "2026-W15",
            "Von": "2026-04-06",
            "Bis": "2026-04-12",
            "Mitarbeiter": "Ava",
            "Wochenstunden": 40.0,
            "Arbeitstage": 4,
            "SOLL": 32.0,
            "IST": 52.0,
            "Stunden bereinigt": 48.0,
        },
        {
            "Woche": "2026-W15",
            "Von": "2026-04-06",
            "Bis": "2026-04-12",
            "Mitarbeiter": "Ben",
            "Wochenstunden": 20.0,
            "Arbeitstage": 4,
            "SOLL": 16.0,
            "IST": 25.0,
            "Stunden bereinigt": 21.0,
        },
    ]


def test_database_cache_key_is_independent_of_selection_order() -> None:
    databases = [
        zeiterfassung_evaluation.NotionDatabaseRef("bbbb", "B", "WIE"),
        zeiterfassung_evaluation.NotionDatabaseRef("aaaa", "A", "ALT"),
    ]

    assert database_cache_key(databases) == database_cache_key(list(reversed(databases)))


def test_load_cached_export_reads_matching_cached_combined_csv(tmp_path) -> None:
    databases = [zeiterfassung_evaluation.NotionDatabaseRef("aaaa", "A", "ALT")]
    cache_key = database_cache_key(databases)
    cache_dir = tmp_path / "cache" / cache_key
    cache_dir.mkdir(parents=True)
    combined_path = cache_dir / "combined.csv"
    manifest_path = cache_dir / "manifest.json"
    pd.DataFrame([{"_database_title": "Zeiterfassung Mai 2026", "Worked Hours": 4.0}]).to_csv(
        combined_path,
        index=False,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "run_dir": str(tmp_path / "runs" / "20260520_120000"),
                "database_cache_key": cache_key,
                "combined_csv_path": str(combined_path),
                "databases": [{"database_id": "aaaa", "title": "A", "source_location": "ALT"}],
            }
        ),
        encoding="utf-8",
    )

    cached = load_cached_export(databases, output_root=tmp_path)

    assert cached is not None
    run_dir, combined, manifest = cached
    assert run_dir == tmp_path / "runs" / "20260520_120000"
    assert combined.to_dict("records") == [
        {"_database_title": "Zeiterfassung Mai 2026", "Worked Hours": 4.0}
    ]
    assert manifest["loaded_from_cache"] is True


def test_prune_stale_cached_exports_removes_cache_for_deleted_notion_databases(tmp_path) -> None:
    active = [zeiterfassung_evaluation.NotionDatabaseRef("aaaa", "A", "ALT")]
    deleted = [zeiterfassung_evaluation.NotionDatabaseRef("bbbb", "B", "WIE")]

    active_cache_dir = tmp_path / "cache" / database_cache_key(active)
    active_cache_dir.mkdir(parents=True)
    (active_cache_dir / "manifest.json").write_text(
        json.dumps({"databases": [{"database_id": "aaaa"}]}),
        encoding="utf-8",
    )

    stale_cache_dir = tmp_path / "cache" / database_cache_key(deleted)
    stale_cache_dir.mkdir(parents=True)
    (stale_cache_dir / "manifest.json").write_text(
        json.dumps({"databases": [{"database_id": "bbbb"}]}),
        encoding="utf-8",
    )
    (stale_cache_dir / "combined.csv").write_text("x\n1\n", encoding="utf-8")

    removed = prune_stale_cached_exports(active, output_root=tmp_path)

    assert removed == [stale_cache_dir]
    assert active_cache_dir.exists()
    assert not stale_cache_dir.exists()
