import pytest

from app import db


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "db.sqlite")
    db.init_schema(path)
    c = db.connect(path)
    yield c
    c.close()


def _insert(conn, device_id="dev-001", reasons=(), received_at="2026-09-26T10:00:00.000Z", temp=22.5):
    return db.insert_reading(
        conn,
        device_id=device_id,
        recorded_at="2026-09-26T09:59:59.000Z",
        received_at=received_at,
        temperature_c=temp,
        humidity_pct=45.0,
        battery_pct=88.0,
        anomaly_reasons=list(reasons),
    )


def test_insert_and_fetch_round_trip(conn):
    reading_id, is_new = _insert(conn)
    assert is_new is True
    device = db.get_device(conn, "dev-001")
    assert device["reading_count"] == 1
    assert device["anomaly_count"] == 0
    assert device["first_seen"] == device["last_seen"] == "2026-09-26T10:00:00.000Z"
    latest = device["latest_reading"]
    assert latest["id"] == reading_id
    assert latest["timestamp"] == "2026-09-26T09:59:59.000Z"
    assert latest["temperature_c"] == 22.5
    assert latest["is_anomaly"] is False
    assert latest["anomaly_reasons"] == []


def test_counts_increment_and_new_device_flag_only_first_time(conn):
    _insert(conn)
    rid2, is_new2 = _insert(conn, reasons=["temperature_high"], received_at="2026-09-26T10:00:05.000Z", temp=40.0)
    assert is_new2 is False
    device = db.get_device(conn, "dev-001")
    assert device["reading_count"] == 2
    assert device["anomaly_count"] == 1
    assert device["first_seen"] == "2026-09-26T10:00:00.000Z"
    assert device["last_seen"] == "2026-09-26T10:00:05.000Z"
    assert device["latest_reading"]["id"] == rid2
    assert device["latest_reading"]["anomaly_reasons"] == ["temperature_high"]


def test_list_queries_newest_first(conn):
    _insert(conn, "dev-002")
    r2, _ = _insert(conn, "dev-001", reasons=["battery_low"])
    r3, _ = _insert(conn, "dev-001")
    assert [d["device_id"] for d in db.list_devices(conn)] == ["dev-001", "dev-002"]
    assert [r["id"] for r in db.list_readings(conn, "dev-001", limit=10)] == [r3, r2]
    assert [r["id"] for r in db.list_readings(conn, "dev-001", limit=10, anomalies_only=True)] == [r2]
    assert [r["id"] for r in db.list_anomalies(conn, limit=10)] == [r2]
    assert db.get_device(conn, "dev-999") is None
    assert db.count_readings(conn) == 3


def test_health_reports_db_ok(client):
    assert client.get("/health").json() == {"status": "ok", "db": "ok"}
