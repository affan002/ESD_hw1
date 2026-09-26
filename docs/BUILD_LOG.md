# Build & verification log

The stage-by-stage evidence behind [REPORT.md](../REPORT.md): what was built at each stage, and the exact command and real output that proved it worked. The report summarises these results; this file keeps the raw evidence.

- [Part A: the app](#part-a-the-app) (stages 0–9)
- [Part B: metrics](#part-b-metrics) (stages B0–B9)
- [Part C: logs](#part-c-logs) (stages C0–C7)

---

## Part A: the app

Each entry records what was built and the command and output that proved it worked.

#### Stage 0 — Scaffold

**Built:**
- A FastAPI app factory (`app/main.py`) with a `GET /health` endpoint.
- Settings read from environment variables (`app/config.py`).
- The `python -m app` entry point, which runs a single uvicorn worker.
- A Dockerfile (`python:3.12-slim`, non-root user) and a `docker-compose.yml` with the `api` service and the `sensor-data` volume.
- A pytest setup.

**Verified:**
```
$ .venv/bin/pytest -q
1 passed in 0.07s

$ docker compose up -d --build api && curl -s localhost:8000/health
{"status":"ok"}

$ docker compose ps --format '{{.Service}} {{.Status}}'
api Up 15 seconds (healthy)
```

#### Stage 1 — Structured logging and request IDs

**Built:**
- `app/logging_setup.py`: a JSON formatter that writes one object per line to stdout. Every line has the fields `timestamp` (UTC, ms, `Z`), `level`, `service`, `logger`, `event`, `message` and `request_id`. Extra fields are only emitted if they're on a fixed whitelist, so request bodies or secrets can't end up in logs by accident.
- `app/middleware.py`: a pure ASGI middleware that does three things:
  - Accepts a valid incoming `X-Request-ID` or generates a UUID4, and echoes it on every response.
  - Writes one `http_request` log line per request with `method`, the route *template* as `path`, `status_code` and `duration_ms`.
  - Turns crashes into a JSON 500 that keeps the request ID, and logs them as `unhandled_exception`.
- Uvicorn runs with `log_config=None` and its access log turned off, so its own messages come out in the same JSON format.

**Verified:**
```
$ .venv/bin/pytest -q
8 passed in 0.15s

$ curl -si -H 'X-Request-ID: test-123' localhost:8000/health | grep -i x-request-id
x-request-id: test-123

$ docker compose logs api --no-log-prefix | jq -c . >/dev/null && echo OK     # every line is valid JSON
OK

$ docker compose logs api --no-log-prefix | jq -c 'select(.event=="http_request")' | tail -2
{"timestamp":"2026-09-25T20:35:04.326Z","level":"INFO","service":"sensor-api","logger":"app.http","event":"http_request","message":"GET /health -> 200","request_id":"test-123","method":"GET","path":"/health","status_code":200,"duration_ms":1.31}
{"timestamp":"2026-09-25T20:35:04.348Z","level":"INFO","service":"sensor-api","logger":"app.http","event":"http_request","message":"GET unmatched -> 404","request_id":"1eb0e5673c15489ea0b18dcca4d34671","method":"GET","path":"unmatched","status_code":404,"duration_ms":0.43}
```

#### Stage 2 — Data model and storage

**Built:**
- `app/db.py`: SQLite (stdlib `sqlite3`, WAL journal, `busy_timeout=5000`) with two tables:
  - `readings`: one row per accepted reading, with both the device timestamp (`recorded_at`) and the server timestamp (`received_at`).
  - `devices`: one row per device, updated on every reading (`first_seen`, `last_seen`, `reading_count`, `anomaly_count`, `last_reading_id`). Devices register themselves automatically on their first reading.

  Inserting a reading and updating its device happen in one `BEGIN IMMEDIATE` transaction. Each request gets its own connection.
- `app/models.py`: Pydantic models. `ReadingIn` enforces the physical sensor limits; the output models are `ReadingOut`, `DeviceStatus` and the list wrappers.
- `/health` now checks the database with `SELECT 1`. It returns `503 {"status":"degraded","db":"error"}` if the database is unreachable.

**Why SQLite:** a single-file database on a Docker named volume. It needs no extra service, survives container restarts and easily handles the ~4 writes/second the simulator produces.

**Verified:**
```
$ .venv/bin/pytest -q
12 passed in 0.27s

$ curl -s localhost:8000/health
{"status":"ok","db":"ok"}

$ docker compose exec api python -c "import sqlite3;print(sqlite3.connect('/data/sensors.db').execute(\"select name from sqlite_master where type='table'\").fetchall())"
[('devices',), ('readings',), ('sqlite_sequence',)]

$ docker compose exec api ls -l /data
-rw-r--r-- 1 app app 28672 Sep 25 20:36 sensors.db
```

#### Stage 3 — Anomaly rule

**Built:** `app/anomaly.py`, which contains two pure functions:
- `evaluate()` returns a list of reason codes in a fixed order.
- `compute_status()` works out each device's status.

The service separates **impossible** data from **concerning** data:

| Field | Physical range (else `422`, not stored) | Expected range, inclusive (else stored as anomaly) | Reason codes |
|---|---|---|---|
| `temperature_c` | −40 … 85 °C | 10.0 … 35.0 | `temperature_low`, `temperature_high` |
| `humidity_pct` | 0 … 100 %RH | 20.0 … 80.0 | `humidity_low`, `humidity_high` |
| `battery_pct` | 0 … 100 % | ≥ 15.0 | `battery_low` |

A reading is an anomaly if it has at least one reason code. Device status is checked in this order:
1. `stale`: no reading for more than `STALE_AFTER_S` (30 s by default), based on server receive time.
2. `anomaly`: the latest reading is anomalous.
3. `ok`: everything else.

**Verified:** boundary tests confirm that 10.0 and 35.0 °C are normal while 9.99 and 35.01 are not, and likewise for the humidity and battery bounds. Other tests cover several reasons at once and the status order.
```
$ .venv/bin/pytest -q tests/test_anomaly.py
16 passed in 0.03s
```

#### Stage 4 — Ingestion endpoint `POST /readings`

**Built:** `POST /readings` handles each reading in this order:
1. Validates it against the physical limits. The device timestamp must include a timezone and be no more than 60 s in the future.
2. Normalises timestamps to UTC.
3. Runs `evaluate()`.
4. Stores the reading and updates the device in one transaction.
5. Returns `201` with the stored reading.

Log events: `device_registered` (a device's first reading), `reading_stored` (every accepted reading) and `anomaly_detected` (WARNING, with reasons and values).

A custom `RequestValidationError` handler logs `reading_rejected` (WARNING) with only the failing field and error type, never the submitted values. It then returns FastAPI's standard `422` body.

**Verified:**
```
$ .venv/bin/pytest -q
41 passed in 0.67s

$ curl -s -XPOST localhost:8000/readings -H 'content-type: application/json' \
    -d '{"device_id":"dev-001","timestamp":"2026-09-25T20:39:43Z","temperature_c":22.5,"humidity_pct":45.0,"battery_pct":88.0}'
{"id":1,"device_id":"dev-001","timestamp":"2026-09-25T20:39:43.000Z","received_at":"2026-09-25T20:39:43.421Z","temperature_c":22.5,"humidity_pct":45.0,"battery_pct":88.0,"is_anomaly":false,"anomaly_reasons":[]}   [201]

$ curl ... -d '{"device_id":"dev-002", ..., "temperature_c":40.0, ...}'
{"id":2,"device_id":"dev-002",...,"is_anomaly":true,"anomaly_reasons":["temperature_high"]}   [201]

$ curl ... -d '{"device_id":"dev-003", ..., "humidity_pct":150, ...}'
{"detail":[{"type":"less_than_equal","loc":["body","humidity_pct"],"msg":"Input should be less than or equal to 100","input":150,"ctx":{"le":100.0}}]}   [422]

$ docker compose logs api --no-log-prefix | jq -c 'select(.event|test("anomaly|rejected"))'
{"timestamp":"2026-09-25T20:39:43.437Z","level":"WARNING","service":"sensor-api","logger":"app","event":"anomaly_detected","message":"anomaly detected: temperature_high","request_id":"9314e35473544746958428d4c0bcd101","device_id":"dev-002","reading_id":2,"anomaly_reasons":["temperature_high"],"temperature_c":40.0,"humidity_pct":45.0,"battery_pct":88.0}
{"timestamp":"2026-09-25T20:39:43.446Z","level":"WARNING","service":"sensor-api","logger":"app","event":"reading_rejected","message":"reading rejected","request_id":"3df52705abb74515a4e3a78d8bedfaaf","device_id":"dev-003","errors":[{"field":"humidity_pct","type":"less_than_equal"}]}
```

#### Stage 5 — Query endpoints

**Built:**

| Route | Returns |
|---|---|
| `GET /devices?status=ok\|anomaly\|stale` | `{"count", "devices":[DeviceStatus]}`, sorted by `device_id`, optionally filtered by status |
| `GET /devices/{device_id}` | one `DeviceStatus`: `status`, `first_seen`, `last_seen`, `reading_count`, `anomaly_count` and `latest_reading`; `404` if the device is unknown |
| `GET /devices/{device_id}/readings?limit=1..500&anomalies_only=` | that device's history, newest first |
| `GET /anomalies?limit=1..500` | the most recent anomalous readings across the whole fleet, newest first |

Status is computed at query time with `compute_status()`, so a device becomes `stale` just by staying silent. No background job is needed.

**Verified:** the three devices below show all three statuses. dev-002 last reported in Stage 4, more than 30 s earlier. dev-001's data from Stage 4 survived the container rebuild, which confirms the volume persists.
```
$ .venv/bin/pytest -q
51 passed in 1.24s

$ curl -s localhost:8000/devices | jq -c '.count, [.devices[]|{device_id,status,reading_count}]'
3
[{"device_id":"dev-001","status":"ok","reading_count":3},{"device_id":"dev-002","status":"stale","reading_count":1},{"device_id":"dev-004","status":"anomaly","reading_count":1}]

$ curl -s 'localhost:8000/devices/dev-001/readings?limit=2' | jq '.readings|length'
2

$ curl -s 'localhost:8000/anomalies?limit=5' | jq -c '[.readings[]|{id,device_id,anomaly_reasons}]'
[{"id":5,"device_id":"dev-004","anomaly_reasons":["battery_low"]},{"id":2,"device_id":"dev-002","anomaly_reasons":["temperature_high"]}]

$ curl -s -w ' [%{http_code}]\n' localhost:8000/devices/dev-999
{"detail":"device not found"} [404]

$ curl -s 'localhost:8000/devices?status=stale' | jq -c '[.devices[].device_id]'
["dev-002"]
```

#### Stage 6 — Fault-injection hook (for Part E)

**Built:** `app/faults.py` and three operator routes: `GET`, `PUT` and `DELETE /admin/faults`. The config is `{"delay_ms": 0..5000, "delay_every_n": ≥1, "error_every_n": ≥0}`.
- The fault applies only to `POST /readings`, and only after the body has passed validation.
- It's deterministic: it hits every Nth request, so an experiment can be repeated exactly.
- The delay uses `time.sleep` inside the sync handler's threadpool thread, so only the affected request slows down.
- An injected error returns `500 {"detail":"injected fault"}` before anything is stored.
- The state is in memory and off by default. `DELETE` or a restart undoes it.
- Every change is logged as `fault_config_changed`, and every injected fault as `fault_injected` (`fault=delay|error`).
- The admin routes have no authentication. They're meant only for local experiments, and a real deployment would put them behind auth or remove them.

**Verified:**
```
$ .venv/bin/pytest -q
57 passed in 2.13s

baseline POST                                   → 201 0.019539s
$ curl -s -XPUT localhost:8000/admin/faults -H 'content-type: application/json' -d '{"delay_ms":500,"delay_every_n":1,"error_every_n":0}'
{"delay_ms":500,"delay_every_n":1,"error_every_n":0}
POST, POST                                      → 201 0.522141s, 201 0.523538s
$ curl -s -XPUT ... -d '{"delay_ms":0,"delay_every_n":1,"error_every_n":2}'
POST, POST                                      → 201 0.006577s, 500 0.003602s
$ curl -s -XDELETE localhost:8000/admin/faults
{"delay_ms":0,"delay_every_n":1,"error_every_n":0}
POST, POST                                      → 201 0.006148s, 201 0.004985s

$ docker compose logs api --no-log-prefix | jq -c 'select(.event|test("fault"))'   (trimmed)
{"level":"WARNING","event":"fault_config_changed","config":{"delay_ms":500,"delay_every_n":1,"error_every_n":0},...}
{"level":"WARNING","event":"fault_injected","fault":"delay","delay_ms":500,"request_id":"cf11b6d8264f409ab3d7d95f1bf2f7ad",...}
{"level":"ERROR","event":"fault_injected","fault":"error","request_id":"02fcbc9e53624dcda8762513eeaa7bdc",...}
```

#### Stage 7 — Device simulator

**Built:** `simulator/simulate.py`, a single-process fleet simulator that runs in the same image as the compose service `simulator`.
- Defaults: 20 devices (`dev-001`…`dev-020`), one reading per device every 5 s, spread evenly. That's about 4 requests/second in a fixed round-robin order.
- Each device's randomness is seeded with `seed + index`, so a run can be repeated exactly.
- Every request carries `X-Request-ID: sim-<12 hex>`, and the simulator logs the same ID. One reading can therefore be traced across both services' logs.
- It writes a `sim_summary` log line every 30 s and on exit.

| Devices | Profile | Behaviour | Effect on the API |
|---|---|---|---|
| dev-001…016 | normal | temp = base U(19,25) + 1.5·sin(2πt/600) + N(0,0.3); humidity = base U(35,60) + N(0,1); battery U(60,100) drains 0.002/reading | `201`, `ok` |
| dev-017 | overheat | temp climbs 0.6 °C per reading from base, resets once above 45 °C | `201` with `temperature_high` after ~17–27 readings; cycles |
| dev-018 | low_battery | battery starts at 20 %, drains 0.25/reading, floor 1 % | `201` with `battery_low` from reading ~21 |
| dev-019 | invalid | 30 % of sends are invalid, rotating through humidity 150 → missing `temperature_c` → timestamp +1 h | `422` (not stored) |
| dev-020 | dropout | online for 60 s, silent for 60 s, repeating | `stale` for ~30 s of every 2 minutes |

**Verified:** a 30 s local run against the Docker API, with `--interval 1`, which is 5× faster than the default.
```
$ .venv/bin/pytest -q
64 passed in 2.35s

$ .venv/bin/python -m simulator.simulate --interval 1 --duration 30 > sim.log; echo "exit=$?"
exit=0
$ jq -c 'select(.event=="sim_summary")|.stats' sim.log | tail -1
{"sent":600,"status_201":591,"status_422":9,"status_5xx":0,"status_other":0,"conn_errors":0}
$ jq -c 'select(.event=="sim_send_failed")|{device_id,status_code,fault,request_id}' sim.log | head -3
{"device_id":"dev-019","status_code":422,"fault":"humidity_out_of_range","request_id":"sim-49d02c2010c2"}
{"device_id":"dev-019","status_code":422,"fault":"missing_temperature","request_id":"sim-18b7042d2c40"}
{"device_id":"dev-019","status_code":422,"fault":"future_timestamp","request_id":"sim-e7f1ee29eede"}

$ curl -s localhost:8000/devices | jq -c '.count, [.devices[]|select(.device_id>="dev-017")|{device_id,status,anomaly_count}]'
20
[{"device_id":"dev-017","status":"anomaly","anomaly_count":9},{"device_id":"dev-018","status":"anomaly","anomaly_count":10},{"device_id":"dev-019","status":"ok","anomaly_count":0},{"device_id":"dev-020","status":"ok","anomaly_count":0}]

# the same request ID, found in the API's logs
$ docker compose logs api --no-log-prefix | jq -c 'select(.request_id=="sim-49d02c2010c2")|{service,event,path,status_code,errors}'
{"service":"sensor-api","event":"reading_rejected","path":null,"status_code":null,"errors":[{"field":"humidity_pct","type":"less_than_equal"}]}
{"service":"sensor-api","event":"http_request","path":"/readings","status_code":422,"errors":null}
```

#### Stage 8 — End-to-end demo in Docker

**Built:** no new features. The full stack was run from a clean state with default settings (20 devices, a reading every 5 s).

**Verified:** stack started at 20:44:54 UTC and was checked at 20:47:57 UTC.
```
$ docker compose down -v && docker compose up -d --build
$ docker compose ps --format '{{.Service}} {{.Status}}'
api Up 13 seconds (healthy)
simulator Up 8 seconds

# 1. all devices registered; the faulty ones are flagged
$ curl -s localhost:8000/devices | jq -c '.count, [.devices[]|select(.device_id>="dev-017")|{device_id,status,reading_count,anomaly_count}]'
20
[{"device_id":"dev-017","status":"anomaly","reading_count":38,"anomaly_count":17},{"device_id":"dev-018","status":"anomaly","reading_count":38,"anomaly_count":18},{"device_id":"dev-019","status":"ok","reading_count":26,"anomaly_count":0},{"device_id":"dev-020","status":"ok","reading_count":24,"anomaly_count":0}]
$ curl -s localhost:8000/devices | jq -c '[.devices[].status]|group_by(.)|map({(.[0]):length})|add'
{"anomaly":2,"ok":18}

# dev-020 goes stale ~30 s after its dropout starts
20:48:14 dev-020 ok    last_seen=2026-09-25T20:47:46.618Z
20:48:17 dev-020 stale last_seen=2026-09-25T20:47:46.618Z
$ curl -s 'localhost:8000/devices?status=stale' | jq -c '[.devices[].device_id]'
["dev-020"]

# 2. fleet anomalies
$ curl -s 'localhost:8000/anomalies?limit=5' | jq -c '[.readings[]|{device_id,anomaly_reasons}]'
[{"device_id":"dev-018","anomaly_reasons":["battery_low"]},{"device_id":"dev-017","anomaly_reasons":["temperature_high"]},{"device_id":"dev-018","anomaly_reasons":["battery_low"]},...]

# 3. the simulator's view: invalid readings rejected, no server errors
$ docker compose logs simulator --no-log-prefix | jq -c 'select(.event=="sim_summary")|.stats' | tail -1
{"sent":713,"status_201":701,"status_422":12,"status_5xx":0,"status_other":0,"conn_errors":0}

# 4. restart: data persists, and the simulator survives the gap
$ docker compose restart api
dev-001 reading_count: before=43, after=44   (counts continued, not reset)
$ docker compose logs simulator --no-log-prefix | jq -c 'select(.event=="sim_send_failed" and .fault==null)|{error,status_code}' | sort | uniq -c
      5 {"error":"ConnectError","status_code":null}

# 5. test suite
$ .venv/bin/pytest -q
64 passed in 2.33s

# 6. clean-up
$ docker compose down        → containers: 0, volume esd_hw1_sensor-data still present
$ docker compose down -v     → Volume esd_hw1_sensor-data Removed
```

#### Stage 9 — Operator dashboard

**Built:**
- `app/static/index.html`, `styles.css` and `app.js`: plain HTML, CSS and JavaScript with no framework and no build step. `GET /` serves the page and `/static` serves the assets, from the same container. The page supports light and dark mode and adapts down to phone width.
- The page shows summary cards, a device table you can filter, a device detail panel with SVG charts of the last 50 readings, a feed of recent anomalies, a form for sending test readings, and fault-injection controls with a warning banner.

Design rules:
- The page calls only the public API, and adds no UI-specific endpoints.
- It never duplicates the thresholds: highlights and chart dots come from `anomaly_reasons`.
- All server data is inserted with `textContent`, so it can't inject HTML.
- Every UI request sends `X-Request-ID: ui-<12 hex>`.
- The access-log middleware now also labels requests to mounted apps with their mount prefix (`/static`) instead of `unmatched`, so the `path` field stays a small, fixed set of values.

**Verified** in a browser against the Docker stack, and with tests:
```
$ .venv/bin/pytest -q
69 passed

GET /                      → 200 text/html, no console errors
Click dev-017              → detail panel: 9 readings, temperature chart climbing 22.5 → 27.9 °C
Preset "Too hot" + Send    → "201 Stored as anomaly (reading #468): temperature_high"  request_id: ui-59e21fbc176a
Preset "Impossible humidity" + Send
                           → "422 Rejected, not stored · humidity_pct: Input should be less than or equal to 100"
Fault form: delay 300 ms   → banner "Fault injection active on POST /readings: 300 ms delay on every request."
                             API log: /readings duration_ms 319.26 320.12 318.76 320.32 320.42
Banner "Turn off"          → banner hidden, form reset to 0, "All faults off"
375 px viewport            → page scrollWidth 375 (no horizontal scroll); cards 2 per row; panels stacked

# the UI's traffic can be told apart in the logs by request ID prefix (last 60 s)
$ docker compose logs api --no-log-prefix --since 60s | jq -r 'select(.event=="http_request")|.request_id[0:3]' | sort | uniq -c
    208 sim
     53 ui-
     16 <uuid>   (Docker healthchecks, no header)
```

Bugs found and fixed during this check:
- The battery bar fill didn't show, because an inline `<span>` ignores its width.
- `replaceChildren(null)` rendered the literal text "null".
- A 0.02 % battery change was stretched to the full height of its chart. Charts now keep a minimum 2-unit vertical range.
- The table needed a horizontal scrollbar at 1024 px. The layout now stacks below 1180 px.

---

## Part B: metrics

#### Stage B0 — `prometheus_client` and `/metrics`

**Built:**
- `prometheus_client==0.26.0` added to `requirements.txt`.
- `app/metrics.py`, with a `render()` function.
- `GET /metrics`, kept out of the OpenAPI docs.

**Verified:**
```
$ .venv/bin/pytest -q
71 passed in 2.51s

$ curl -s localhost:8000/metrics | grep -E '^(process_resident_memory_bytes|process_cpu_seconds_total|process_open_fds|python_info)'
python_info{implementation="CPython",major="3",minor="12",patchlevel="14",version="3.12.14"} 1.0
process_resident_memory_bytes 5.7769984e+07
process_cpu_seconds_total 1.24
process_open_fds 16.0
```

#### Stage B1 — HTTP metrics

**Built:** three metrics, recorded in the existing request middleware (`app/middleware.py`), so every route is covered without touching any handler:

| Metric | Type | Labels |
|---|---|---|
| `sensor_http_requests_total` | Counter | `method`, `route`, `status_code` |
| `sensor_http_request_duration_seconds` | Histogram | `method`, `route` |
| `sensor_http_requests_in_progress` | Gauge | none |

- The histogram buckets are 5 ms, 10, 25, 50, 100, 250, 500 ms, 1, 2.5 and 5 s.
- The gauge is incremented when a request arrives and decremented when it finishes.
- `route` is the same bounded route template the access log already uses: `/devices/{device_id}`, `/static`, or `unmatched`. It's never the raw URL, so `GET /devices/dev-017` and `GET /devices/dev-018` share one series.

**Verified:**
```
$ .venv/bin/pytest -q
75 passed in 2.82s

$ curl -s localhost:8000/metrics | grep '^sensor_http_requests_total'
sensor_http_requests_total{method="POST",route="/readings",status_code="201"} 32.0
sensor_http_requests_total{method="POST",route="/readings",status_code="422"} 1.0
sensor_http_requests_total{method="GET",route="/devices/{device_id}",status_code="200"} 1.0
sensor_http_requests_total{method="GET",route="/health",status_code="200"} 3.0
...

$ curl -s localhost:8000/metrics | grep 'duration_seconds_bucket' | grep 'POST' | grep 'readings"'     (cumulative)
..._bucket{le="0.005",method="POST",route="/readings"} 1.0
..._bucket{le="0.01",method="POST",route="/readings"} 11.0
..._bucket{le="0.025",method="POST",route="/readings"} 67.0
..._bucket{le="0.1",method="POST",route="/readings"} 68.0
..._bucket{le="+Inf",method="POST",route="/readings"} 68.0

$ curl -s localhost:8000/metrics | grep '^sensor_http_requests_in_progress'
sensor_http_requests_in_progress 1.0        ← the /metrics request itself
```

#### Stage B2 — Business metrics

**Built:** metrics for the app's real events, recorded where each event happens:

| Metric | Type | Labels | Recorded in |
|---|---|---|---|
| `sensor_readings_total` | Counter | `outcome` = `normal` \| `anomaly` \| `rejected` \| `failed` | `POST /readings` handler (stored or failed) and the validation handler (rejected) |
| `sensor_reading_validation_errors_total` | Counter | `field`, `reason` (the Pydantic error type) | validation handler, once per failing field |
| `sensor_anomalies_total` | Counter | `reason` = one of the 5 anomaly codes | `POST /readings`, once per reason |
| `sensor_db_write_duration_seconds` | **Summary** | none | wraps `db.insert_reading()` |
| `sensor_faults_injected_total` | Counter | `fault` = `delay` \| `error` | `FaultState.apply()` |
| `sensor_fault_active` | Gauge | `fault` | set on every change to the fault config |
| `sensor_devices` | Gauge | `status` = `ok` \| `anomaly` \| `stale` | a custom collector that runs at scrape time |

Design notes:
- **Readings get one counter with an `outcome` label**, not one counter per outcome. The total, the share stored and the share rejected all come from a single metric, and the four outcomes add up to every `POST /readings` attempt.
- **`sensor_devices` is computed during each scrape, not updated on each event.** A device becomes stale by *not* sending anything, so there's no event to count. The collector runs the same `compute_status()` rule that `GET /devices` uses, so the metric and the API can't disagree.
- **Label values are bounded.** `field` is limited to the 5 known fields plus `body` (a malformed JSON body reports a character position as its location, which could be anything). `reason` values come from Pydantic's fixed list of error types.
- Every known label combination is created at 0 at startup, so `rate()` and the dashboard panels have data before the first event.
- The Dockerfile sets `PROMETHEUS_DISABLE_CREATED_SERIES=True`, which removes the library's extra `*_created` series.

**Verified:** after about 40 s of simulator traffic.
```
$ .venv/bin/pytest -q
83 passed in 3.29s

$ curl -s localhost:8000/metrics | grep -E '^(sensor_readings_total|sensor_anomalies_total|sensor_reading_validation_errors_total|sensor_db_write|sensor_devices|sensor_fault)'
sensor_readings_total{outcome="normal"} 140.0
sensor_readings_total{outcome="anomaly"} 16.0
sensor_readings_total{outcome="rejected"} 2.0
sensor_readings_total{outcome="failed"} 0.0
sensor_reading_validation_errors_total{field="humidity_pct",reason="less_than_equal"} 1.0
sensor_reading_validation_errors_total{field="temperature_c",reason="missing"} 1.0
sensor_anomalies_total{reason="temperature_high"} 8.0          ← dev-017 overheating
sensor_anomalies_total{reason="battery_low"} 8.0               ← dev-018 battery
sensor_db_write_duration_seconds_count 156.0                    ← = 140 normal + 16 anomaly (every stored reading)
sensor_db_write_duration_seconds_sum 1.4799524540030689         ← average ≈ 9.5 ms per write
sensor_fault_active{fault="delay"} 0.0
sensor_devices{status="ok"} 18.0
sensor_devices{status="anomaly"} 2.0
sensor_devices{status="stale"} 1.0

$ curl -s localhost:8000/devices | jq -c '[.devices[].status]|group_by(.)|map({(.[0]):length})|add'
{"anomaly":2,"ok":18,"stale":1}                                 ← same as the gauge
```

#### Stage B3 — Prometheus

**Built:**
- `monitoring/prometheus/prometheus.yml` (scrape every 5 s; jobs `sensor-api` and `prometheus`).
- A `prometheus` service in `docker-compose.yml`: port 9090, volume `prometheus-data`, 7-day retention.

**Verified:**
```
$ curl -s localhost:9090/-/ready
Prometheus Server is Ready.

$ curl -s localhost:9090/api/v1/targets | jq -r '.data.activeTargets[] | "\(.labels.job) \(.scrapeUrl) \(.health)"'
prometheus  http://localhost:9090/metrics  up
sensor-api  http://api:8000/metrics        up

# PromQL through the HTTP API (the same queries work at http://localhost:9090/query)
sum by (outcome) (rate(sensor_readings_total[1m]))
  normal 2.83/s   anomaly 0.197/s   rejected 0.036/s   failed 0
sum by (route) (rate(sensor_http_requests_total[1m]))
  /readings 3.06/s   /health 0.32/s   /metrics 0.18/s   /devices 0.16/s (the open dashboard, polling every 5 s) ...
histogram_quantile(0.95, sum by (le) (rate(sensor_http_request_duration_seconds_bucket{route="/readings"}[1m])))
  0.0246   (p95 ≈ 25 ms)
histogram_quantile(0.99, ...same...)
  0.0414   (p99 ≈ 41 ms)
rate(sensor_db_write_duration_seconds_sum[1m]) / rate(sensor_db_write_duration_seconds_count[1m])
  0.0083   (average DB write ≈ 8.3 ms)
count({job="sensor-api"})
  156      (series exposed by the app)
```

**Something I learned here:** straight after Prometheus started, `sum(rate(sensor_readings_total[1m]))` reported only **0.56/s**, then 1.83/s, then about 3/s, even though the simulator's own log showed a steady 3.9/s. `rate()` divides the increase by the *whole* 60 s window, so until the window holds a full minute of samples it underestimates. The same thing happens after an app restart, when the counter resets to 0. That's why each experiment phase needs to run for longer than the rate window.

#### Stage B4 — Node Exporter

**Built:**
- A `node-exporter` service using the host network, the host PID namespace and a read-only mount of `/`.
- Prometheus job `node` → `host.docker.internal:9100`, plus `extra_hosts: host-gateway` on the `prometheus` service.

**Verified:** see the comparison table in [REPORT.md § B.5](../REPORT.md#b5-node-exporter).
```
$ curl -s -o /dev/null -w '%{http_code}' localhost:9100/metrics
200
$ curl -s localhost:9090/api/v1/targets | jq -r '.data.activeTargets[] | "\(.labels.job) \(.scrapeUrl) \(.health)"'
node        http://host.docker.internal:9100/metrics  up
prometheus  http://localhost:9090/metrics             up
sensor-api  http://api:8000/metrics                   up
```
Right after startup, CPU busy % read 68 % while the load average was only 0.78.

#### Stage B5 — Grafana (provisioned) and the app dashboard

**Built:**
- The Grafana service.
- Datasource and dashboard provisioning files.
- `sensor-service.json`, with four rows:
  1. **Overview**: stat panels for stored readings/s, p95, 5xx share, 422 share, devices by status, and fault switches.
  2. **Application**: request rate by route, p50/p95/p99, responses by status code, Apdex, and average DB write time from the Summary.
  3. **Ingestion (business)**: readings by outcome, devices by status over time, anomalies per minute by reason, validation errors per minute by field, and anomaly share.
  4. **Faults and API process**: fault switches, faults injected per minute, requests in flight, and API CPU/memory.

Every panel has a description (the ⓘ icon) explaining what it shows.

**Verified:**
```
$ curl -s localhost:3000/api/health
{"database":"ok","version":"13.2.2",...}
$ curl -s localhost:3000/api/datasources/uid/prometheus | jq -c '{name,uid,url,readOnly}'
{"name":"Prometheus","uid":"prometheus","url":"http://prometheus:9090","readOnly":true}
$ curl -s 'localhost:3000/api/search?type=dash-db' | jq -c '.[] | {uid,title,folderTitle}'
{"uid":"node-host","title":"Host (Node Exporter)","folderTitle":"Sensor Ingestion"}
{"uid":"sensor-service","title":"Sensor Ingestion Service","folderTitle":"Sensor Ingestion"}
```

Every panel query was run through Grafana's own query API (`POST /api/ds/query`, the same path the browser uses) over the last 5 minutes, and all **23 queries in 20 panels returned data**:
```
ok  Stored readings / s                        last=3.87
ok  p95 latency · POST /readings               last=0.0249
ok  Server errors (5xx)                        last=0
ok  Rejected readings (422)                    last=0.0184
ok  Devices by status                          3 series  [anomaly 2, ok 18, stale 1]
ok  Latency percentiles · POST /readings       p50 0.0152 · p95 0.0249 · p99 0.0467
ok  Apdex · POST /readings (T = 25 ms)         last=0.977
ok  DB write time (average) · Summary          last=0.0089
ok  Readings by outcome                        4 series
ok  Anomalies per minute by reason             5 series  [temperature_high 12, battery_low 5.45, ...]
... (23/23 ok)
```
In the browser, the dashboard rendered the same values: *Stored readings / s 3.82 · p95 24.606 ms · Server errors 0.0% · Rejected 1.4% · Devices anomaly 2 / ok 17 / stale 2 · Fault injection delay off / error off*.

**Reproducibility:**
```
$ docker compose rm -sf grafana && docker volume rm esd_hw1_grafana-data && docker compose up -d grafana
$ curl -s localhost:3000/api/dashboards/uid/sensor-service | jq -c '{title: .dashboard.title, panels: ..., provisioned: .meta.provisioned, canSave: .meta.canSave}'
{"title":"Sensor Ingestion Service","panels":20,"provisioned":true,"canSave":false}
```

#### Stage B6 — Host dashboard

**Built:** `node-host.json` with 12 panels in three rows:
1. **Machine**: hostname, hardware, kernel, CPU count, RAM and uptime, as stat panels.
2. **CPU and memory**: CPU busy by mode (stacked), load average against the CPU count, memory used vs available, and a bar gauge of filesystem usage.
3. **Disk and network**: NVMe read/write throughput, and network receive/transmit throughput on the physical interfaces (loopback and Docker bridges excluded).

**Verified:** all 18 queries return data through `POST /api/ds/query`. The dashboard renders:
```
Hostname  muhammad-affan-Latitude-5400      Hardware  Dell Inc. Latitude 5400
Kernel    7.0.0-34-generic                  CPUs 8    Memory 7.6 GiB
Filesystem used   / (nvme0n1p5) 46.5%   /boot/efi (nvme0n1p1) 51.7%
Load 1m 0.90 vs 8 CPUs    Network rx on wlo1 ≈ 2.2 MB/s
```
CPU busy read 12 % averaged over 1 minute, while `top`'s one-second sample read about 4 %. They measure different windows, and the minute included Grafana starting up. The 68 % seen right after Node Exporter started (B4) had disappeared once the rate window filled.

#### Stage B7 — Checking the p95 against the logs

**Method:** a script took every `POST /readings` access-log line from the 60 s before time T, and calculated exact percentiles from their `duration_ms`. It then asked Prometheus for `histogram_quantile(q, sum by (le) (rate(..._bucket{route="/readings",method="POST"}[1m])))` evaluated at the same T (`/api/v1/query?time=T`), so both describe exactly the same requests.

**Run 1, original buckets (5, 10, 25, 50, 100, 250, 500 ms, …), 234 requests:**
```
p50  logs(exact) =   15.64 ms   histogram_quantile =   15.65 ms
p95  logs(exact) =   20.86 ms   histogram_quantile =   24.56 ms
p99  logs(exact) =   25.43 ms   histogram_quantile =   44.30 ms
share <= 0.01s:  logs 0.214   prometheus 0.215
share <= 0.025s: logs 0.974   prometheus 0.972
share <= 0.05s:  logs 0.996   prometheus 0.995
```
The bucket shares agree to within 0.002, so the counts are right. The p95 and p99 errors come from interpolating inside wide buckets.

**Change:** I added bucket bounds at 15, 20, 30, 40 and 75 ms in `app/metrics.py`, and rebuilt the API.

**Run 2, finer buckets, 231 requests, window ending 10:34:41 UTC** (taken after a full minute of post-restart data):
```
p50  logs(exact) =   16.06 ms   histogram_quantile =   16.38 ms
p95  logs(exact) =   18.91 ms   histogram_quantile =   19.94 ms
p99  logs(exact) =   24.93 ms   histogram_quantile =   23.82 ms
```
p95 is now within 5 % and p99 within 4 % (it was 74 % too high before). Series from the app went from about 150 to 196, which is an acceptable cost. The full explanation is in [REPORT.md § B.3](../REPORT.md#b3-percentiles-and-time-window).

#### Stage B8 — Whole stack from one command

**Verified:** `docker compose down`, then `docker compose up -d --build`. All five services came back, with no manual steps:
```
$ docker compose ps --format '{{.Service}}\t{{.Status}}\t{{.Ports}}'
api            Up 17 seconds (healthy)   0.0.0.0:8000->8000/tcp
grafana        Up 17 seconds             0.0.0.0:3000->3000/tcp
node-exporter  Up 17 seconds             (host network, :9100)
prometheus     Up 17 seconds             0.0.0.0:9090->9090/tcp
simulator      Up 12 seconds

$ curl -s localhost:9090/api/v1/targets | jq -r '.data.activeTargets[] | "\(.labels.job) \(.health)"'
node up
prometheus up
sensor-api up

$ curl -s 'localhost:3000/api/search?type=dash-db' | jq -r '.[] | "\(.uid) \(.title)"'
node-host Host (Node Exporter)
sensor-service Sensor Ingestion Service

# metrics history survived down/up (prometheus-data volume)
$ curl -s --get localhost:9090/api/v1/query --data-urlencode 'query=count_over_time(up{job="sensor-api"}[30m])'
165 samples of up{job=sensor-api} in the last 30 min

$ docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}'
grafana 241MiB · api 42MiB · prometheus 38MiB · simulator 23MiB · node-exporter 9MiB
```
The monitoring stack adds about 290 MB of RAM. `.venv/bin/pytest -q` passes: 83 tests (12 of them for metrics). That's the `rate()` warm-up effect from B3 again: `node_cpu_seconds_total` had only a few seconds of samples in its 1-minute window.

$ curl -s localhost:8000/metrics | grep '^# TYPE sensor_'
# TYPE sensor_http_requests_total counter
# TYPE sensor_http_request_duration_seconds histogram
# TYPE sensor_http_requests_in_progress gauge
# TYPE sensor_readings_total counter
# TYPE sensor_reading_validation_errors_total counter
# TYPE sensor_anomalies_total counter
# TYPE sensor_db_write_duration_seconds summary
# TYPE sensor_faults_injected_total counter
# TYPE sensor_fault_active gauge
# TYPE sensor_devices gauge
```

#### Stage B9 — Screenshots for the report

**Built:**
- `scripts/capture_screenshots.sh`. It captures the Grafana dashboards, the single panels, the Prometheus target page and the operator dashboard with headless Chrome, using a throwaway profile, then crops away empty background.
- It uses a fixed, absolute time window. The first attempt used `from=now-30m&to=now`, and every `rate()` panel dipped at the right-hand edge. Headless Chrome's `--virtual-time-budget` runs the page clock ahead of real time, so "now" was past the last scrape. Prometheus confirmed the real rate was a steady 3.95/s at that moment.
- A dashboard fix found while reviewing the screenshots: *Fault switches*, *Faults injected per minute* and *Requests in flight* showed a meaningless 0–100 axis when every value was 0. They now have `axisSoftMax` values of 1, 10 and 3. The latency panel's description also listed the old bucket bounds, and now lists the current ones.

**Verified:**
```
$ scripts/capture_screenshots.sh 30 "2026-09-26 11:43:26 UTC"
window: 2026-09-26 16:13:26 PKT -> 16:43:26 PKT
  docs/screenshots/b_app_dashboard.png
  ... (9 files)
```
The spikes between 16:34 and 16:37 PKT in the screenshots are manual test readings sent from the operator dashboard. The logs confirm them by their request-ID prefix:
```
$ docker compose logs api --no-log-prefix --since 20m | jq -r 'select(.event=="reading_rejected" and (.request_id|startswith("ui-"))) | "\(.timestamp) \(.errors|map(.field+":"+.type)|join(","))"'
2026-09-26T11:35:30.542Z device_id:string_pattern_mismatch
2026-09-26T11:36:22.554Z humidity_pct:less_than_equal
...
$ ... select(.event=="anomaly_detected" and (.request_id|startswith("ui-"))) ...
2026-09-26T11:34:28.636Z dev-100 temperature_high
2026-09-26T11:35:23.804Z dev-100 temperature_high
...
```

---

## Part C: logs

#### Stage C0 — Prerequisites and Docker log rotation

**Checked:**
- 2.5 GiB of RAM was available.
- `vm.max_map_count=1048576`, which is at least the 262,144 Elasticsearch needs, so no `sudo` was required.
- Latest Elastic 9.x images: `elasticsearch:9.5.3`, `kibana:9.5.3` and `elastic/filebeat:9.5.3`. All three are pinned to the same version.

**Built:** an `x-app-logging` anchor in `docker-compose.yml` (`json-file`, `max-size: 10m`, `max-file: 3`), applied to `api` and `simulator`. Before this, Docker's log files had no size limit.
```
$ docker inspect <api> --format '{{.HostConfig.LogConfig.Type}} {{json .HostConfig.LogConfig.Config}}'
json-file {"max-file":"3","max-size":"10m"}   path=/var/lib/docker/containers/aff99987…/aff99987…-json.log
```

#### Stage C1 — Elasticsearch

**Built:** service `elasticsearch`:
- single node, security off, heap fixed at 512 MB,
- volume `es-data`,
- port **127.0.0.1**:9200 only,
- a healthcheck waiting for `yellow`.
```
$ curl -s localhost:9200 | jq -c '{cluster_name, version: .version.number}'
{"cluster_name":"sensor-logs","version":"9.5.3"}
$ curl -s localhost:9200/_cluster/health | jq -c '{status, number_of_nodes}'
{"status":"green","number_of_nodes":1}
heap_max_mb=512 · container memory 844 MiB · docker port → 127.0.0.1:9200
```

#### Stage C2 — Retention policy and index template

**Built:**
- `monitoring/elasticsearch/ilm-policy.json`: hot phase rolls over at `max_age: 10m` or 1 GB; delete phase deletes 1 h after rollover.
- `monitoring/elasticsearch/index-template.json`: pattern `sensor-logs*`, a data stream, 1 shard / 0 replicas, and `dynamic: false` with 29 explicitly typed fields.
- The ILM poll interval is set to 1 m.

**Verified** on a throwaway data stream, `sensor-logs-test`:
```
policy: {"hot":{"max_age":"10m","max_primary_shard_size":"1gb"},"delete_after":"1h"}
template: {"patterns":["sensor-logs*"],"lifecycle":"sensor-logs-policy","dynamic":false,"fields":29}
POST sensor-logs-test/_doc {…,"errors":[{"field":"humidity_pct",…}],"password":"hunter2"}
  → created in .ds-sensor-logs-test-2026.09.26-000001, policy sensor-logs-policy attached
mapping has "password"?                     false
term errors.field:humidity_pct              1 hit
match password:hunter2                      0 hits   (not indexed)
_source.password                            "hunter2" (but still STORED)
```
**Lesson:** `dynamic: false` limits what is *searchable*, not what is *stored*. Keeping secrets out is the app's job: its field whitelist means they're never written.

#### Stage C3 — Filebeat

**Built:** `monitoring/filebeat/filebeat.yml`:
- Docker autodiscover plus a `filestream` input per matching container, using the `container` parser.
- Four processors: `copy_fields` (message → `log.original`), `decode_json_fields` (to the root, `overwrite_keys`, `add_error_key: false`), `timestamp` (from our `timestamp`), and `drop_fields`.
- Output to the `sensor-logs` data stream; Filebeat's own template and ILM are switched off.
- The service runs as root with read-only mounts of `/var/lib/docker/containers` and `docker.sock`, plus volume `filebeat-data` for the registry.

**Problems found and fixed:**
1. **No inputs started.** A condition on `docker.container.labels.com_docker_compose_service` never matched. A debug run (`-d autodiscover`) showed that Filebeat nests the dotted label keys (`container.labels.com.docker.compose.project.value`), and `labels.dedot: true` didn't change that. The fix was to match on the name Compose gives each container: `regexp: container.name: "^esd_hw1-(api|simulator)-[0-9]+$"`. Then: `Input 'filestream' starting` ×2, and `Connection to backoff(elasticsearch(http://elasticsearch:9200)) established`.
2. **Personal data in every document.** Autodiscover attached `docker.container.labels`, including `com_docker_compose_project_working_dir: /home/muhammad-affan/…`, which is the user's name. I added `docker` to `drop_fields`, then deleted the data stream and the Filebeat registry so everything was re-ingested from Docker's log files.

   I also found that a check using `exists`/`query_string` returned 0 even while the field was present, because those queries only see *indexed* fields. The reliable check is to scan `_source`:
```
docs: 9248 · sources with a docker key: 0 · sources mentioning /home/: 0
distinct top-level keys: @timestamp anomaly_reasons battery_pct config container device_id duration_ms errors event fault
                         host humidity_pct is_anomaly level log logger message method path reading_id request_id service
                         stats status_code stream temperature_c
docs by container: esd_hw1-api-1 8865 · esd_hw1-simulator-1 99      (only our two services)
docs by event: http_request 4528, reading_stored 3991, anomaly_detected 277, reading_rejected 64, sim_send_failed 64, sim_summary 34, …
```

#### Stage C4 — Kibana, provisioned

**Built:**
- Service `kibana` (127.0.0.1:5601, with a healthcheck on `/api/status`).
- One-shot service `logs-setup` (`curlimages/curl:8.16.0`) running `monitoring/logs-setup.sh`: poll interval, policy, template, then an import of `monitoring/kibana/saved-objects.ndjson` (the data view `sensor-logs` plus 7 saved searches) and setting the default data view.
- `filebeat` has `depends_on: logs-setup: service_completed_successfully`.
```
[logs-setup] importing Kibana saved objects (data view + saved searches)
{"successCount":8,"success":true,"warnings":[],…}
[logs-setup] done                     exit code: 0
data views: {"id":"sensor-logs","name":"Sensor logs","title":"sensor-logs"} · default: sensor-logs · 37 fields
saved searches (query → hits at the time):
  Errors and warnings        level : ("ERROR" or "WARNING")                          460
  Server errors (5xx)        event : "http_request" and status_code >= 500          0
  Slow requests (> 100 ms)   event : "http_request" and duration_ms > 100           21
  Rejected readings (422)    event : "reading_rejected"                              73
  Anomalies                  event : "anomaly_detected"                              314
  Fault injection            event : ("fault_injected" or "fault_config_changed")    0
  Simulator send failures    service : "simulator" and event : "sim_send_failed"     73
```
Kibana 9 migrated the imported searches to its new `tabs` format; the queries were kept intact.

**A real incident found through the logs:** the "Slow requests" search found 20 `POST /readings` requests of up to 424 ms, all between 17:15 and 17:17 UTC, just after Elasticsearch started and while the Kibana and Filebeat images were being unpacked. Part B's host metrics explain them:
```
UTC       disk write   p95 POST /readings
17:05      1.2 MB/s     19.9 ms
17:15:30  29.5 MB/s     36.1 ms
17:16:30  10.0 MB/s     54.6 ms
17:20      0.7 MB/s     23.7 ms
```
The logs found *which* requests were slow; the metrics showed *why* (disk contention slowing SQLite's flushes to disk).

**Housekeeping:** the "Help us improve the Elastic Stack" banner was hidden in the screenshots. `TELEMETRY_BANNER` isn't among the settings Kibana's Docker image accepts from the environment; `TELEMETRY_ALLOWCHANGINGOPTINSTATUS=false` (with `TELEMETRY_OPTIN=false`) is.

#### Stage C5 — Worked example: raw line → stored document → Kibana search

I sent `POST /readings` with `X-Request-ID: c5-demo-1` and 41.5 °C, which returned `201` with `["temperature_high"]`.
```
1. app stdout (docker compose logs api):
{"timestamp": "2026-09-26T17:28:33.009Z", "level": "WARNING", "service": "sensor-api", "logger": "app", "event": "anomaly_detected", "message": "anomaly detected: temperature_high", "request_id": "c5-demo-1", "device_id": "dev-100", "reading_id": 116760, "anomaly_reasons": ["temperature_high"], "temperature_c": 41.5, "humidity_pct": 45.0, "battery_pct": 88.0}

2. Docker's file /var/lib/docker/containers/aff99987…/aff99987…-json.log (read through filebeat's read-only mount):
{"log":"{\"timestamp\": \"2026-09-26T17:28:33.009Z\", … \"battery_pct\": 88.0}\n","stream":"stdout","time":"2026-09-26T17:28:33.01002747Z"}

3. stored document (.ds-sensor-logs-2026.09.26-000001), _source abridged:
{"@timestamp":"2026-09-26T17:28:33.009Z","level":"WARNING","service":"sensor-api","event":"anomaly_detected",
 "message":"anomaly detected: temperature_high","request_id":"c5-demo-1","device_id":"dev-100","reading_id":116760,
 "anomaly_reasons":["temperature_high"],"temperature_c":41.5,"humidity_pct":45,"battery_pct":88,"stream":"stdout",
 "container":{"name":"esd_hw1-api-1","image":{"name":"sensor-api:dev"},"id":"aff99987…"},"host":{"name":"911f183df3ff"},
 "log":{"original":"{\"timestamp\": \"2026-09-26T17:28:33.009Z\", … }\n"}}
field types: request_id keyword · level keyword · event keyword · temperature_c float · anomaly_reasons keyword · log.original keyword (not indexed)

4. all documents for the request (request_id : "c5-demo-1"): reading_stored (INFO) · anomaly_detected (WARNING) · http_request (INFO, duration_ms 6.53)
```
`@timestamp` is the app's time (…33.009Z), not Docker's (…33.010Z). The screenshots are `docs/screenshots/c_kibana_request_id.png`, `c_kibana_document.png`, `c_kibana_warnings.png` and `c_kibana_rejected_saved_search.png`.

#### Stage C6 — No secrets or personal data in the pipeline

I planted fake values in every place a client can put them:
- an `Authorization: Bearer SECRETTOKEN-c6` header,
- a `Cookie: session=SECRETCOOKIE-c6` header,
- extra body fields `"password":"SECRETPASSWORD-c6"` and `"owner_email":"jane.doe@example.com"` (returned 201),
- a password inside a body rejected with 422,
- `GET /devices?token=SECRETQUERY-c6`.
```
stored for these requests: reading_stored · http_request path=/readings 201 · reading_rejected errors=[{humidity_pct, less_than_equal}] ·
                           http_request 422 · http_request path=/devices 200   (route template only: no query string)
scanned 11343 documents' _source incl. log.original:
{'SECRETTOKEN': 0, 'SECRETCOOKIE': 0, 'SECRETPASSWORD': 0, 'SECRETQUERY': 0, 'jane.doe': 0, 'example.com': 0,
 'Bearer': 0, 'password': 0, 'owner_email': 0, '/home/': 0}
raw Docker logs of api: 0 for each value
```
None of the Filebeat processors adds data: `copy_fields` copies our own line, `decode_json_fields` parses it, `timestamp` re-uses a field, and `drop_fields` only removes. Autodiscover's Docker labels, which did contain a home path (C3), are dropped.

#### Stage C7 — What survives what, and retention

| Action | Docker log (`docker compose logs api`) | Elasticsearch | Notes |
|---|---|---|---|
| `docker compose restart api` | c5-demo-1: 3 → 3 | 3 → 3 | same container, same log file |
| `docker compose up -d --force-recreate api` | 3 → **0** | 3 → **3** | the new container starts a new, empty log file. Autodiscover started an input for it within about 6 s (165 documents) |
| `docker compose restart filebeat` | — | c5-demo-1 3 → 3 | **no duplicates**: the registry (`filebeat-data`) remembers the read position |
| Filebeat stopped for 20 s while the app logged `c7-while-down` | 2 lines | 0 while stopped → **2** after start | nothing lost; Filebeat resumed from its saved position. (The total rose 12038 → 12083 at first: documents already in flight became searchable after the 1 s refresh.) |
| `docker compose restart elasticsearch` | — | 12350 → 12350 | `es-data` volume |
| `docker compose down` then `up -d --build` | recreated (empty) | 18851 → 18908 and growing; c5-demo-1 still 3 | about 2 min to be fully up. Order: elasticsearch healthy → logs-setup (exit 0) → filebeat |

**Retention (ILM):**
```
.ds-sensor-logs-2026.09.26-000001  created 17:25:43Z · rolled over 17:36:20Z (10.6 min: 10m + ≤1m poll) · 14652 docs
.ds-sensor-logs-2026.09.26-000002  created 17:36:20Z · rolled over 17:46:23Z
.ds-sensor-logs-2026.09.26-000003  created 17:46:23Z (current write index)
explain -000001: {"phase":"hot","action":"complete","age":"10.93m", since rollover: 10 min} → delete due ≈ 18:36–18:37Z
```

**The deletion, observed** by a watcher polling `_cat/indices` every 10 s and `_ilm/explain` every 10 min. It skips any check where Elasticsearch doesn't answer:
```
{"t":"2026-09-26T18:28:06Z","phase":"hot","action":"complete","age":"51.76m"}
{"t":"2026-09-26T18:38:09Z","phase":"delete","action":"delete","age":"1.03h"}
000001 GONE at 2026-09-26T18:38:29Z
index                             docs.count creation.date.string
.ds-sensor-logs-2026.09.26-000002       4256 2026-09-26T17:36:20.748Z
.ds-sensor-logs-2026.09.26-000003       6429 2026-09-26T17:46:23.333Z
.ds-sensor-logs-2026.09.26-000004       5729 2026-09-26T17:57:23.287Z
.ds-sensor-logs-2026.09.26-000005       5707 2026-09-26T18:08:23.310Z
.ds-sensor-logs-2026.09.26-000006       5714 2026-09-26T18:19:23.242Z
.ds-sensor-logs-2026.09.26-000007       3723 2026-09-26T18:30:23.281Z
{"generation":8,"indices":[…-000002 … -000007]}
c5-demo-1 docs now: 0
```
The rollovers came every 10–11 min (17:36, 17:46, 17:57, 18:08, 18:19, 18:30), which is `max_age: 10m` plus up to one 1-minute ILM check. `-000001` lived 17:25:43 → 18:38:29, i.e. 1 h 13 min.
**Memory with the whole stack running:** Kibana 1.73 GiB, Elasticsearch 1.01 GiB, Grafana 403 MiB, Filebeat 92 MiB, Prometheus 65 MiB, API 42 MiB. Host RAM available: 939 MiB.

**A watcher mistake worth recording:** my first deletion watcher reported "000001 GONE at 17:44:36Z". That was during `docker compose down`, when Elasticsearch was unreachable, and the check "index not listed" can't tell "deleted" from "no answer". The index was still there after `up`. The watcher now skips a check when Elasticsearch doesn't answer.

