"""Experiment E2: a throwaway exporter that shows cardinality explosion.

Runs as its own process with its OWN CollectorRegistry, so it can never add
series to the sensor API's metrics. Exposes one counter, demo_requests_total:

  --mode label    one series per fake request id (req-0000 … req-0099)
  --mode nolabel  the same increments on a single series (the right way)

The number of unique ids is capped at 100 in code: the assignment says
"do not try to crash Prometheus". Serves /metrics on --port until stopped.
"""

import argparse
import logging
import signal
import threading

from prometheus_client import CollectorRegistry, Counter, start_http_server

from app.logging_setup import setup_logging

MAX_IDS = 100  # hard cap, whatever --count says

log = logging.getLogger("cardinality_demo")


def build_registry(mode: str, count: int) -> CollectorRegistry:
    """A fresh registry holding only demo_requests_total, never the app's registry."""
    count = min(count, MAX_IDS)
    registry = CollectorRegistry()  # empty: no process_/python_ collectors
    if mode == "label":
        requests = Counter("demo_requests", "Demo requests, labelled by request id (the WRONG way).",
                           ["request_id"], registry=registry)
        for i in range(count):
            requests.labels(request_id=f"req-{i:04d}").inc()
    else:
        requests = Counter("demo_requests", "Demo requests, no per-request label (the right way).",
                           registry=registry)
        requests.inc(count)
    return registry


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--mode", choices=["label", "nolabel"], required=True)
    p.add_argument("--count", type=int, default=100, help=f"requests to simulate (capped at {MAX_IDS})")
    p.add_argument("--port", type=int, default=8001)
    args = p.parse_args()
    count = min(args.count, MAX_IDS)
    setup_logging("cardinality-demo")

    registry = build_registry(args.mode, count)
    start_http_server(args.port, registry=registry)
    log.info("serving demo metrics", extra={"event": "demo_started",
                                            "config": {"mode": args.mode, "count": count, "port": args.port}})
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    log.info("stopping", extra={"event": "demo_stopped"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
