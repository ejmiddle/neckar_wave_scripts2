from datetime import time

import pytest
import pandas as pd

from src.schichtplan_utils import (
    _prepare_availability_dataframe,
    detect_availability_time_columns,
    generate_fixed_schedule_entries,
    generate_schichtplan,
    normalize_fixed_schedule_records,
    parse_wann,
    transform_to_schedule_format,
)


def test_prepares_current_notion_availability_format():
    df = pd.DataFrame(
        {
            "Wann": [{"start": "2026-06-01", "end": "2026-06-06", "time_zone": None}],
            "Kommentar": [""],
            "Select": [None],
            "Name": ["Maya"],
        }
    )

    detected = detect_availability_time_columns(df)
    prepared = _prepare_availability_dataframe(df)

    assert detected == {"mode": "span", "span": "Wann"}
    assert len(prepared) == 6
    assert prepared.loc[0, "Start Time"] == pd.Timestamp("2026-06-01 00:00")
    assert prepared.loc[0, "End Time"] == pd.Timestamp("2026-06-01 23:59:59")
    assert prepared.loc[5, "End Time"] == pd.Timestamp("2026-06-06 23:59:59")
    assert prepared.loc[0, "Name"] == "Maya"


def test_prepares_current_notion_availability_format_with_times():
    df = pd.DataFrame(
        {
            "Wann": [
                {
                    "start": "2026-06-29T09:00:00.000+02:00",
                    "end": "2026-06-29T16:00:00.000+02:00",
                    "time_zone": None,
                }
            ],
            "Kommentar": ["Available for morning shift"],
            "Select": [None],
            "Name": ["Charley"],
        }
    )

    prepared = _prepare_availability_dataframe(df)

    assert prepared.loc[0, "Start Time"] == pd.Timestamp("2026-06-29 09:00:00")
    assert prepared.loc[0, "End Time"] == pd.Timestamp("2026-06-29 16:00:00")


def test_rejects_non_current_availability_columns():
    df = pd.DataFrame(
        {
            "Name": ["Moritz"],
            "Start": ["2026-06-03 09:00"],
            "End": ["2026-06-03 14:00"],
            "Kommentar": [""],
        }
    )

    with pytest.raises(ValueError, match="exactly these columns"):
        _prepare_availability_dataframe(df)


def test_normalizes_fixed_schedule_records_from_notion_rows():
    schedules, errors = normalize_fixed_schedule_records(
        [
            {
                "Name": "Jaime",
                "Wochentage": ["Dienstag", "Mittwoch", "Donnerstag"],
                "Start": "09:00",
                "Ende": "17:00",
            },
            {
                "Mitarbeiter": "Lennard",
                "Days": "Monday, Wednesday and Friday",
                "Start Time": "9",
                "End Time": "17",
            },
        ]
    )

    assert errors == []
    assert schedules["Jaime"] == {
        "days": ["Tuesday", "Wednesday", "Thursday"],
        "start_time": time(9, 0),
        "end_time": time(17, 0),
    }
    assert schedules["Lennard"] == {
        "days": ["Monday", "Wednesday", "Friday"],
        "start_time": time(9, 0),
        "end_time": time(17, 0),
    }


def test_reports_invalid_fixed_schedule_rows():
    schedules, errors = normalize_fixed_schedule_records(
        [
            {
                "Name": "No Days",
                "Start": "09:00",
                "Ende": "17:00",
            }
        ]
    )

    assert schedules == {}
    assert errors == ["Row 1: missing or invalid Days/Wochentage"]


def test_merges_duplicate_fixed_schedule_names_with_same_times():
    schedules, errors = normalize_fixed_schedule_records(
        [
            {"Name": "Ula", "Tag": "Dienstag", "Start": "09:00", "Ende": "17:00"},
            {"Name": "Ula", "Tag": "Mittwoch", "Start": "09:00", "Ende": "17:00"},
        ]
    )

    assert errors == []
    assert schedules["Ula"]["days"] == ["Tuesday", "Wednesday"]


def test_normalizes_fixed_schedule_records_from_notion_date_span():
    schedules, errors = normalize_fixed_schedule_records(
        [
            {
                "Name": "Kathi",
                "Wann": {
                    "start": "2026-06-04T09:00:00.000+02:00",
                    "end": "2026-06-04T17:00:00.000+02:00",
                    "time_zone": None,
                },
            }
        ]
    )

    assert errors == []
    assert schedules["Kathi"] == {
        "days": ["Thursday"],
        "start_time": time(9, 0),
        "end_time": time(17, 0),
    }


def test_fixed_schedule_date_span_only_supplies_weekday_and_time():
    schedules, errors = normalize_fixed_schedule_records(
        [
            {
                "Name": "Kathi",
                "Wann": {
                    "start": "2026-06-04T09:00:00.000+02:00",
                    "end": "2026-06-04T17:00:00.000+02:00",
                    "time_zone": None,
                },
            }
        ]
    )

    generated = generate_fixed_schedule_entries("2026-07-01", "2026-07-10", schedules)

    assert errors == []
    assert generated["Start Time"].tolist() == [
        pd.Timestamp("2026-07-02 09:00:00"),
        pd.Timestamp("2026-07-09 09:00:00"),
    ]
    assert generated["End Time"].tolist() == [
        pd.Timestamp("2026-07-02 17:00:00"),
        pd.Timestamp("2026-07-09 17:00:00"),
    ]


def test_rejects_fixed_schedule_calendar_rows_from_multiple_weeks():
    schedules, errors = normalize_fixed_schedule_records(
        [
            {
                "Name": "Kathi",
                "Wann": {
                    "start": "2026-06-04T09:00:00.000+02:00",
                    "end": "2026-06-04T17:00:00.000+02:00",
                    "time_zone": None,
                },
            },
            {
                "Name": "Jaime",
                "Wann": {
                    "start": "2026-06-11T09:00:00.000+02:00",
                    "end": "2026-06-11T17:00:00.000+02:00",
                    "time_zone": None,
                },
            },
        ]
    )

    assert schedules == {}
    assert errors == ["Fixed schedule calendar must contain exactly one week. Found: 2026-W23, 2026-W24"]


def test_parse_wann_converts_utc_to_berlin_wall_time():
    start, end = parse_wann(
        {
            "start": "2026-06-04T07:00:00.000Z",
            "end": "2026-06-04T15:00:00.000Z",
            "time_zone": None,
        }
    )

    assert start == pd.Timestamp("2026-06-04 09:00:00")
    assert end == pd.Timestamp("2026-06-04 17:00:00")


def test_transform_to_schedule_format_does_not_apply_manual_timezone_offset():
    df = pd.DataFrame(
        {
            "Name": ["Jaime"],
            "new_name": ["Jaime"],
            "Start Time": [pd.Timestamp("2026-06-04 09:00:00")],
            "End Time": [pd.Timestamp("2026-06-04 17:00:00")],
        }
    )

    formatted = transform_to_schedule_format(df, [("Jaime", "BAK", "Bakery")])

    assert formatted.loc[0, "Date"] == "2026-06-04 09:00 → 2026-06-04 17:00"


def test_generate_schichtplan_keeps_explicit_non_fixed_times_and_fixed_times(tmp_path):
    availability = pd.DataFrame(
        {
            "Wann": [
                {
                    "start": "2026-06-04T08:00:00.000+02:00",
                    "end": "2026-06-04T20:00:00.000+02:00",
                    "time_zone": None,
                }
            ],
            "Kommentar": [""],
            "Select": [None],
            "Name": ["Maya"],
        }
    )
    fixed_schedules = {
        "Kathi": {
            "days": ["Thursday"],
            "start_time": time(9, 0),
            "end_time": time(17, 0),
        }
    }

    output_files, _ = generate_schichtplan(
        availability,
        "2026-06-04",
        "2026-06-04",
        [("Maya", "ALT", "Service"), ("Kathi", "BAK", "Bakery")],
        fixed_schedules=fixed_schedules,
        output_dir=tmp_path,
    )

    export = pd.read_csv(output_files["export"])
    dates_by_name = dict(zip(export["Name"], export["Date"], strict=True))
    assert dates_by_name["Maya"] == "2026-06-04 08:00 → 2026-06-04 20:00"
    assert dates_by_name["Kathi"] == "2026-06-04 09:00 → 2026-06-04 17:00"


def test_generate_schichtplan_falls_back_to_11_16_for_non_fixed_without_explicit_time(tmp_path):
    availability = pd.DataFrame(
        {
            "Wann": [{"start": "2026-06-04", "end": None, "time_zone": None}],
            "Kommentar": [""],
            "Select": [None],
            "Name": ["Maya"],
        }
    )

    output_files, _ = generate_schichtplan(
        availability,
        "2026-06-04",
        "2026-06-04",
        [("Maya", "ALT", "Service")],
        fixed_schedules=None,
        output_dir=tmp_path,
    )

    export = pd.read_csv(output_files["export"])
    assert export.loc[0, "Date"] == "2026-06-04 11:00 → 2026-06-04 16:00"
