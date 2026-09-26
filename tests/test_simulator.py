from datetime import datetime, timezone

from app.anomaly import evaluate
from app.models import ReadingIn
from simulator.simulate import INVALID_KINDS, assign_profiles, build_fleet

NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def by_profile(profile, seed=42):
    return next(d for d in build_fleet(20, seed) if d.profile == profile)


def test_last_four_devices_are_faulty():
    profiles = assign_profiles(20)
    assert profiles[:16] == ["normal"] * 16
    assert profiles[16:] == ["overheat", "low_battery", "invalid", "dropout"]
    assert [d.device_id for d in build_fleet(20, 42)][16:] == ["dev-017", "dev-018", "dev-019", "dev-020"]


def test_same_seed_same_values():
    a = [d.next_payload(NOW, 0)[0] for d in build_fleet(20, 7)]
    b = [d.next_payload(NOW, 0)[0] for d in build_fleet(20, 7)]
    assert a == b


def test_normal_devices_stay_valid_and_normal():
    for device in build_fleet(20, 42)[:16]:
        for step in range(200):
            payload, invalid = device.next_payload(NOW, step * 5.0)
            assert invalid is None
            reading = ReadingIn(**payload)
            assert evaluate(reading.temperature_c, reading.humidity_pct, reading.battery_pct) == []


def test_overheat_crosses_35_by_reading_30_and_resets():
    for seed in range(10):
        device = by_profile("overheat", seed)
        temps = [device.next_payload(NOW, 0)[0]["temperature_c"] for _ in range(80)]
        assert max(temps[:30]) > 35.0
        assert max(temps) <= 45.6  # resets once above 45
        assert any(b < a for a, b in zip(temps, temps[1:]))  # it did reset


def test_low_battery_below_15_by_reading_25():
    device = by_profile("low_battery")
    batteries = [device.next_payload(NOW, 0)[0]["battery_pct"] for _ in range(25)]
    assert batteries[-1] < 15.0
    assert min(device.next_payload(NOW, 0)[0]["battery_pct"] for _ in range(200)) == 1.0


def test_invalid_device_rotates_three_kinds():
    device = by_profile("invalid")
    kinds = [k for k in (device.next_payload(NOW, 0)[1] for _ in range(100)) if k]
    assert 15 <= len(kinds) <= 45  # ~30%
    assert kinds[:6] == list(INVALID_KINDS) * 2


def test_dropout_online_then_silent():
    device = by_profile("dropout")
    assert device.is_online(0) and device.is_online(59.9)
    assert not device.is_online(60) and not device.is_online(119.9)
    assert device.is_online(120)
