#!/usr/bin/env python3
"""Server-side numbers for one E1 stage, from Prometheus and Elasticsearch.

Given a stage's UTC start and end (from scripts/load.py's summary), prints
POST /readings percentiles, mean, Apdex, 5xx, injected faults and the matching
log counts, all over exactly that window. Standard library only.

    python3 scripts/e1_stage_stats.py <stage> <start_utc> <end_utc>
"""

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime

PROM = "http://localhost:9090/api/v1/query"
ES = "http://localhost:9200/sensor-logs/_count"
SEL = 'route="/readings",method="POST"'


def ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def prom(expr: str, at: float) -> float | None:
    url = PROM + "?" + urllib.parse.urlencode({"query": expr, "time": at})
    result = json.load(urllib.request.urlopen(url))["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def es_count(query: dict) -> int:
    req = urllib.request.Request(ES, json.dumps({"query": query}).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))["count"]


def main() -> int:
    stage, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    t0, t1 = ts(start), ts(end)
    # Evaluate at the stage end with a range covering the stage, minus a 15 s
    # margin at the start so samples from the previous stage are excluded.
    w = f"{int(t1 - t0 - 15)}s"
    inc = lambda m: f"increase({m}[{w}])"
    b = f"sensor_http_request_duration_seconds_bucket{{{SEL}}}"
    q = lambda p: prom(f"histogram_quantile({p}, sum by (le) ({inc(b)}))", t1)
    count = prom(f"sum({inc(f'sensor_http_request_duration_seconds_count{{{SEL}}}')})", t1) or 0
    total = prom(f"sum({inc(f'sensor_http_request_duration_seconds_sum{{{SEL}}}')})", t1) or 0
    le = lambda bound: prom(f'sum({inc(b[:-1] + f",le=\"{bound}\"}}")})', t1) or 0
    rng = {"range": {"@timestamp": {"gte": start, "lte": end}}}
    out = {
        "stage": stage,
        "window_utc": [start, end],
        "promql_range": w,
        "server_requests": round(count),
        "server_p50_s": round(q(0.50), 4),
        "server_p95_s": round(q(0.95), 4),
        "server_p99_s": round(q(0.99), 4),
        "server_mean_s": round(total / count, 4) if count else None,
        "apdex_T25ms": round((le("0.025") + le("0.1")) / 2 / count, 3) if count else None,
        "share_over_500ms": round(1 - le("0.5") / count, 3) if count else None,
        "http_5xx": round(prom(f'sum({inc("sensor_http_requests_total{route=\"/readings\",status_code=~\"5..\"}")})', t1) or 0),
        "faults_injected_delay": round(prom(f'sum({inc("sensor_faults_injected_total{fault=\"delay\"}")})', t1) or 0),
        "fault_active_delay_max": prom(f'max_over_time(sensor_fault_active{{fault="delay"}}[{w}])', t1),
        "db_write_mean_s": round((prom(f"{inc('sensor_db_write_duration_seconds_sum')}", t1) or 0)
                                 / (prom(f"{inc('sensor_db_write_duration_seconds_count')}", t1) or 1), 4),
        "logs_this_stage_http_request": es_count({"bool": {"filter": [
            {"prefix": {"request_id": f"load-{stage}-"}}, {"term": {"event": "http_request"}}]}}),
        "logs_fault_injected": es_count({"bool": {"filter": [{"term": {"event": "fault_injected"}}, rng]}}),
        "logs_http_request_over_400ms": es_count({"bool": {"filter": [
            {"term": {"event": "http_request"}}, {"range": {"duration_ms": {"gt": 400}}}, rng]}}),
        "logs_level_error": es_count({"bool": {"filter": [{"term": {"level": "ERROR"}}, rng]}}),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
