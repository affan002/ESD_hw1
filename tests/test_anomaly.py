from datetime import datetime, timedelta, timezone

import pytest

from app.anomaly import compute_status, evaluate

NORMAL = dict(temperature_c=22.0, humidity_pct=45.0, battery_pct=80.0)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("temperature_c", 10.0, []),
        ("temperature_c", 35.0, []),
        ("temperature_c", 9.99, ["temperature_low"]),
        ("temperature_c", 35.01, ["temperature_high"]),
        ("humidity_pct", 20.0, []),
        ("humidity_pct", 80.0, []),
        ("humidity_pct", 19.99, ["humidity_low"]),
        ("humidity_pct", 80.01, ["humidity_high"]),
        ("battery_pct", 15.0, []),
        ("battery_pct", 14.99, ["battery_low"]),
        ("battery_pct", 0.0, ["battery_low"]),
    ],
)
def test_boundaries(field, value, expected):
    assert evaluate(**{**NORMAL, field: value}) == expected


def test_multiple_reasons_in_fixed_order():
    assert evaluate(temperature_c=40.0, humidity_pct=10.0, battery_pct=5.0) == [
        "temperature_high",
        "humidity_low",
        "battery_low",
    ]


NOW = datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)


def test_status_ok_when_recent_and_normal():
    assert compute_status(NOW - timedelta(seconds=5), NOW, False, 30) == "ok"


def test_status_anomaly_when_recent_and_anomalous():
    assert compute_status(NOW - timedelta(seconds=5), NOW, True, 30) == "anomaly"


def test_status_stale_beats_anomaly():
    assert compute_status(NOW - timedelta(seconds=31), NOW, True, 30) == "stale"
    assert compute_status(NOW - timedelta(seconds=31), NOW, False, 30) == "stale"


def test_status_exactly_at_threshold_is_not_stale():
    assert compute_status(NOW - timedelta(seconds=30), NOW, False, 30) == "ok"
