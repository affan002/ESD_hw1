"""Prometheus metrics: every metric the API exposes is defined here.

Uses prometheus_client's default registry, which also provides process_* and
python_* metrics for free. The API runs a single uvicorn worker, so no
multiprocess mode is needed (see CLAUDE.md).

Naming: `sensor_` prefix, snake_case, base-unit suffix (_seconds, _total).
Labels only ever take values from small fixed sets; never device_id or request_id.
"""

import logging
import sqlite3

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, Summary, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from app import db
from app.anomaly import REASON_CODES, compute_status
from app.timeutil import parse_iso, utc_now

log = logging.getLogger("app.metrics")

# ---------- HTTP (recorded in app/middleware.py for every request) ----------

HTTP_REQUESTS = Counter(
    "sensor_http_requests_total",
    "HTTP requests handled, by route template and status code.",
    ["method", "route", "status_code"],
)

# Bucket bounds (seconds). histogram_quantile() interpolates linearly inside a
# bucket, so bounds are dense where normal POST /readings latency sits
# (10-40 ms) and sparse above, but still bracket the 500 ms injected delay.
# With only 10/25/50 ms bounds, p99 was estimated at 44 ms when the true value
# was 25 ms (see docs/BUILD_LOG.md, stage B7).
HTTP_DURATION = Histogram(
    "sensor_http_request_duration_seconds",
    "Time from receiving a request to finishing its response.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

HTTP_IN_PROGRESS = Gauge(
    "sensor_http_requests_in_progress",
    "Requests currently being handled.",
)

# ---------- Ingestion (recorded in app/main.py, POST /readings) ----------

READING_OUTCOMES = ("normal", "anomaly", "rejected", "failed")
READINGS = Counter(
    "sensor_readings_total",
    "POST /readings attempts by outcome: normal/anomaly = stored (201), "
    "rejected = invalid (422, not stored), failed = server error (5xx, not stored).",
    ["outcome"],
)

VALIDATION_FIELDS = ("device_id", "timestamp", "temperature_c", "humidity_pct", "battery_pct")
VALIDATION_ERRORS = Counter(
    "sensor_reading_validation_errors_total",
    "Field-level validation failures on POST /readings. One rejected reading can fail several fields.",
    ["field", "reason"],
)

ANOMALIES = Counter(
    "sensor_anomalies_total",
    "Anomaly reasons found on stored readings. One reading can have several reasons.",
    ["reason"],
)

DB_WRITE = Summary(
    "sensor_db_write_duration_seconds",
    "Time to store one reading and update its device row (one SQLite transaction).",
)

# ---------- Fault injection (recorded in app/faults.py) ----------

FAULT_TYPES = ("delay", "error")
FAULTS_INJECTED = Counter(
    "sensor_faults_injected_total",
    "Faults injected into POST /readings requests.",
    ["fault"],
)
FAULT_ACTIVE = Gauge(
    "sensor_fault_active",
    "1 while this fault type is switched on via /admin/faults, else 0.",
    ["fault"],
)

# Create every known label combination up front so each series exists at 0
# from the first scrape; rate() and dashboards then work before the first event.
for _outcome in READING_OUTCOMES:
    READINGS.labels(_outcome)
for _reason in REASON_CODES:
    ANOMALIES.labels(_reason)
for _fault in FAULT_TYPES:
    FAULTS_INJECTED.labels(_fault)
    FAULT_ACTIVE.labels(_fault).set(0)


def validation_field(loc: tuple) -> str:
    """Map a Pydantic error location to a bounded label value."""
    parts = [str(p) for p in loc if p != "body"]
    return parts[0] if parts and parts[0] in VALIDATION_FIELDS else "body"


# ---------- Device status (computed at scrape time) ----------


class DeviceStatusCollector(Collector):
    """Exposes sensor_devices{status}: how many devices are ok / anomaly / stale right now.

    Status is never stored; GET /devices computes it on read. This collector does
    the same on every scrape, with the same compute_status() rule, so the metric
    always agrees with the API and the dashboard.
    """

    def __init__(self) -> None:
        self._settings = None

    def configure(self, settings) -> None:
        self._settings = settings

    def collect(self):
        family = GaugeMetricFamily(
            "sensor_devices",
            "Devices by current status, computed at scrape time with the same rule as GET /devices.",
            labels=["status"],
        )
        if self._settings is not None:
            counts = {"ok": 0, "anomaly": 0, "stale": 0}
            try:
                conn = db.connect(self._settings.db_path)
                try:
                    now = utc_now()
                    for device in db.list_devices(conn):
                        status = compute_status(
                            parse_iso(device["last_seen"]),
                            now,
                            device["latest_reading"]["is_anomaly"],
                            self._settings.stale_after_s,
                        )
                        counts[status] += 1
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                # Skip the series rather than fail the whole scrape.
                log.warning("device status collection failed", extra={"event": "metrics_collect_failed", "error": str(exc)})
                counts = None
            if counts is not None:
                for status, value in counts.items():
                    family.add_metric([status], value)
        yield family


DEVICE_STATUS = DeviceStatusCollector()
REGISTRY.register(DEVICE_STATUS)


def render() -> tuple[bytes, str]:
    """Current metrics in the Prometheus text format, and its content type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
