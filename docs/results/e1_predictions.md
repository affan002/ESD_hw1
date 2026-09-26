# E1 predictions: written before the fault was switched on

Written at **2026-09-26T20:36:33Z**. The fault stage hasn't started yet.

**Fault:** `PUT /admin/faults {"delay_ms":500,"delay_every_n":5,"error_every_n":0}`. Every 5th `POST /readings` sleeps 500 ms before it's stored ([app/faults.py](../../app/faults.py)).
**Load:** the same as the baseline, `scripts/load.py --rate 5 --duration 150` (750 requests, open loop), with the simulator stopped.
**Baseline:** p50 11.9 ms, p95 14.7 ms, p99 15.0 ms, mean 11.1 ms, Apdex 1.00, DB write 4.6 ms (`e1_server_baseline.json`).

## Metrics

| # | Signal | Prediction | Reasoning |
|---|---|---|---|
| P1 | server **p50** | **unchanged, about 12 ms** | 80 % of requests aren't delayed, so the median is still an undelayed request |
| P2 | server **p95 / p99** from `histogram_quantile` | **about 0.875 s / 0.975 s** | A delayed request takes about 512 ms, which lands in the `(0.5, 1.0]` s bucket. The cumulative share at `le=0.5` is 0.80, and interpolating linearly inside that bucket gives p95 = 0.5 + (0.95 − 0.80)/0.20 × 0.5 = **0.875 s** and p99 = 0.5 + (0.99 − 0.80)/0.20 × 0.5 = **0.975 s** |
| P3 | the **true** p95, client-side (`load.py`) | **about 515 ms** | Nearest rank: the 95th percentile falls inside the delayed 20 %, which all take 500 ms plus the normal time. So P2 overstates the true value by about 70 % |
| P4 | server **mean** | **about 111 ms** (+100 ms) | 0.8 × 11 ms + 0.2 × 511 ms |
| P5 | **Apdex** (T = 25 ms) | **0.80** | 80 % are satisfied (≤ 25 ms, like the baseline), none are tolerating (25–100 ms), and 20 % are frustrated (> 100 ms) |
| P6 | share of requests > 500 ms | **0.20** | one in five |
| P7 | `sensor_faults_injected_total{fault="delay"}` | **+150** (about 60 per minute on the panel) | 750 / 5 |
| P8 | `sensor_fault_active{fault="delay"}` | **1** throughout the stage, 0 before and after | set by `PUT`, cleared by `DELETE` |
| P9 | 5xx, `outcome="failed"`, request rate | **0, 0, still 5 /s** | A delay isn't an error, and the open-loop client keeps offering 5 /s |
| P10 | DB write mean (the Summary) | **unchanged, about 4.6 ms** | The sleep happens in `faults.apply()`, *before* `db.insert_reading()` |
| P11 | `sensor_http_requests_in_progress` | often **2** instead of 1 | Little's law: 5 /s × 0.111 s ≈ 0.56 extra requests in flight on average. A scrape lands inside a delayed request's 0.5 s sleep about half the time |

## Logs (Kibana)

| # | Search | Prediction |
|---|---|---|
| P12 | `event : "fault_config_changed"` | **1** when it's switched on (WARNING), then 1 more at recovery |
| P13 | `event : "fault_injected"` | **150** (WARNING, `fault: delay`, `delay_ms: 500`), and **0 at ERROR level** |
| P14 | `event : "http_request" and duration_ms > 400` | **150** (20 %) during the fault, **0** in the baseline and recovery stages |
| P15 | which requests are delayed | Every 5th request after the `PUT` resets the counter: `load-fault-00004`, `-00009`, `-00014`, … (**IDs ending in 4 or 9**), as long as no other `POST /readings` slips in |

## Recovery

| # | Prediction |
|---|---|
| P16 | After `DELETE /admin/faults`, the recovery stage matches the baseline to within noise: p95 around 15 ms, Apdex about 1.0, 0 slow requests, 0 `fault_injected` |
| P17 | Because of the 1-minute window, Grafana's p95 panel takes **about 60 s** to fall back after the `DELETE` |
