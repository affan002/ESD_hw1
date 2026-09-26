"""The E2 demo must stay bounded and must never touch the app's metrics registry."""

import importlib.util
from pathlib import Path

from prometheus_client import REGISTRY

spec = importlib.util.spec_from_file_location(
    "cardinality_demo", Path(__file__).parent.parent / "scripts" / "cardinality_demo.py"
)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def series(registry, name="demo_requests_total"):
    return [s for m in registry.collect() for s in m.samples if s.name == name]


def test_label_mode_is_capped_at_100_series():
    assert len(series(demo.build_registry("label", 5000))) == demo.MAX_IDS == 100


def test_nolabel_mode_is_one_series_with_the_same_total():
    s = series(demo.build_registry("nolabel", 100))
    assert len(s) == 1 and s[0].value == 100


def test_demo_never_registers_in_the_app_registry():
    demo.build_registry("label", 100)
    assert REGISTRY.get_sample_value("demo_requests_total", {"request_id": "req-0000"}) is None
    assert not [m for m in REGISTRY.collect() if m.name.startswith("demo_")]
