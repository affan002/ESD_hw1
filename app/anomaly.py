"""Anomaly rule and device status.

A reading that passed validation (physical limits, see app/models.py) is an
*anomaly* when any value is outside the expected operating range below.
Bounds are inclusive: a value exactly on a bound is normal.
"""

from datetime import datetime

TEMPERATURE_MIN_C = 10.0
TEMPERATURE_MAX_C = 35.0
HUMIDITY_MIN_PCT = 20.0
HUMIDITY_MAX_PCT = 80.0
BATTERY_MIN_PCT = 15.0

REASON_CODES = (
    "temperature_low",
    "temperature_high",
    "humidity_low",
    "humidity_high",
    "battery_low",
)


def evaluate(temperature_c: float, humidity_pct: float, battery_pct: float) -> list[str]:
    """Return the anomaly reasons for a reading, in fixed order (empty list = normal)."""
    reasons = []
    if temperature_c < TEMPERATURE_MIN_C:
        reasons.append("temperature_low")
    elif temperature_c > TEMPERATURE_MAX_C:
        reasons.append("temperature_high")
    if humidity_pct < HUMIDITY_MIN_PCT:
        reasons.append("humidity_low")
    elif humidity_pct > HUMIDITY_MAX_PCT:
        reasons.append("humidity_high")
    if battery_pct < BATTERY_MIN_PCT:
        reasons.append("battery_low")
    return reasons


def compute_status(last_seen: datetime, now: datetime, latest_is_anomaly: bool, stale_after_s: float) -> str:
    """Device status: stale (silent too long) > anomaly (latest reading anomalous) > ok."""
    if (now - last_seen).total_seconds() > stale_after_s:
        return "stale"
    if latest_is_anomaly:
        return "anomaly"
    return "ok"
