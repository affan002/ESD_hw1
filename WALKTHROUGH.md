# Learning guide: what we built, and what it's teaching you

This guide is for **understanding** the project, not just running it. By the end you should be able to:
- explain every part of the system in your own words,
- find the line of code behind any number on a dashboard,
- check every claim in [REPORT.md](REPORT.md) yourself, and change the report with confidence.

Each section follows the same pattern:

> **Concept**: the idea the course wants you to learn.
> **See it**: where it lives in this project (code links like [app/main.py:119](app/main.py#L119)).
> **Try it**: a hands-on exercise. **Predict the result before you run it.** Being wrong is where you learn.
> **Check yourself**: questions with hidden answers.

Everything below was run against the live stack on 2026-09-26. Your numbers will differ slightly because the simulator never stops.

**Contents:**
- [0. What this assignment is really teaching](#0-what-this-assignment-is-really-teaching)
- [1. The system at a glance](#1-the-system-at-a-glance)
- **Part A: the app**
  - [A1. What it does](#a1-what-the-app-does)
  - [A2. Code map](#a2-code-map)
  - [A3. Follow one reading through the code](#a3-follow-one-reading-through-the-code)
  - [A4. Use it yourself](#a4-use-it-yourself)
  - [A5. Design decisions](#a5-design-decisions-worth-understanding)
  - [A6. Check yourself](#a6-check-yourself)
- **Part B: metrics**
  - [B1. Concepts](#b1-the-concepts-you-need)
  - [B2. Layer 1: the raw `/metrics` page](#b2-layer-1-the-raw-metrics-page)
  - [B3. Layer 2: Prometheus](#b3-layer-2-prometheus)
  - [B4. Layer 3: Grafana](#b4-layer-3-grafana)
  - [B5. Experiments to run yourself](#b5-experiments-to-run-yourself)
  - [B6. From what you see to the report](#b6-from-what-you-see-to-the-report)
  - [B7. Check yourself](#b7-check-yourself)
- **Part C: logs**
  - [C1. Concepts](#c1-the-concepts-you-need)
  - [C2. What the app writes](#c2-layer-1-what-the-app-writes-stdout)
  - [C3. Docker's log file](#c3-layer-2-dockers-log-file-the-buffer)
  - [C4. Filebeat](#c4-layer-3-filebeat-collect-and-parse)
  - [C5. Elasticsearch](#c5-layer-4-elasticsearch-store-index-retain)
  - [C6. Kibana](#c6-layer-5-kibana-find)
  - [C7. Experiments](#c7-experiments-to-run-yourself)
  - [C8. From what you see to the report](#c8-from-what-you-see-to-the-report)
  - [C9. Check yourself](#c9-check-yourself)
- [Glossary](#glossary) · [What Parts D–E will teach](#what-parts-de-will-teach)

---

## 0. What this assignment is really teaching

**Observability** means being able to answer questions about a running system *from the outside*, including questions you didn't think of in advance, without changing code or redeploying. You get it by making the system emit **signals**. This course covers two:

| Signal | What it is | Good at | Bad at | In this project |
|---|---|---|---|---|
| **Metrics** | Numbers sampled over time, with a few labels (e.g. "requests per second, by route") | "Is something wrong? How big? Since when?" They're cheap to store and fast to chart, and you can alert on them | Individual events: *which* request, *which* device | Part B: `prometheus_client`, Prometheus, Grafana |
| **Logs** | One record per event, with as much detail as you like | "What exactly happened to *this* request?" | Seeing trends across millions of events, and cost at volume | Part C: JSON logs, Filebeat, Elasticsearch, Kibana |

The skills behind each part of the assignment:

| Part | Skill | Why it matters in real jobs |
|---|---|---|
| A | Build something worth observing, and **design it to be observable** from the start: request IDs, structured logs, bounded categories | Adding observability afterwards is much harder |
| B | Choose **what to measure**, pick the right metric type, and read percentiles honestly | Most outages are first spotted on a dashboard |
| C | Make logs **searchable**: structure, a pipeline, retention, no personal data | This is how you debug one specific failure |
| D | Explain how the pieces **fit together and fail** | On-call engineers need a mental model of the system, not a list of tools |
| E | **The scientific method on a live system**: baseline, predict, break, observe, explain, recover. Plus the *cost* of metrics (cardinality) | This is what separates guessing from diagnosing |

Keep one question in mind throughout: **"How would I know?"** How would I know the service is slow? That a sensor died? That one integrator sends bad data? Every metric and log line in this project exists to answer a question like that.

---

## 1. The system at a glance

```
        (you)                          ┌──────────────────── Docker Compose ─────────────────────┐
  browser / curl ──HTTP──┐             │                                                          │
                         ▼             │   simulator ──POST /readings (4/s, X-Request-ID)──┐      │
                  ┌──────────────┐◄────┼──────────────────────────────────────────────────┘      │
                  │ api  :8000   │     │                                                          │
                  │ FastAPI      │─────┼──► SQLite file on volume sensor-data                     │
                  │ /metrics     │◄────┼── prometheus :9090 scrapes every 5 s ──► node-exporter   │
                  │ JSON logs    │     │        ▲       (volume prometheus-data)   :9100 (host)   │
                  └──────────────┘     │        │ PromQL                                          │
                         │ stdout      │   grafana :3000 (dashboards provisioned from files)      │
                         ▼             │                                                          │
                  docker logs          │   (Part C will add Filebeat → Elasticsearch → Kibana)    │
                                       └──────────────────────────────────────────────────────────┘
```

| Service | URL | What to do there |
|---|---|---|
| `api` | http://localhost:8000 (operator dashboard) · http://localhost:8000/docs (interactive API) · http://localhost:8000/metrics (raw metrics) | Use the app |
| `simulator` | none (it only sends traffic) | Watch it with `docker compose logs -f simulator --no-log-prefix` |
| `prometheus` | http://localhost:9090 | Query metrics and check the scrape targets |
| `grafana` | http://localhost:3000 (no login needed to view) | The dashboards |
| `node-exporter` | http://localhost:9100/metrics | Raw host metrics |

> **Run every `docker compose …` command from the project folder** (`cd ~/Documents/ESD/ESD_hw1`). Compose finds the stack through `docker-compose.yml` in the current folder. From anywhere else it fails with `no configuration file provided: not found`. `curl` commands work from any folder. To read a container's logs from elsewhere, use its full name, e.g. `docker logs esd_hw1-api-1`.

Start everything:

```bash
docker compose up -d --build
```

Check that it's running:

```bash
docker compose ps
```

Stop it and keep the data:

```bash
docker compose down
```

---

# Part A — the app

## A1. What the app does

A fleet of battery-powered sensors sends readings (temperature, humidity, battery). For every reading, the API makes **one of three decisions**. This is the most important idea in Part A:

| The reading is… | Example | Response | Stored? | Code |
|---|---|---|---|---|
| **Impossible** (outside the sensor's physical range, or malformed) | humidity 150 %, missing field, `device_id` "sensor-1", no timezone, 1 h in the future | `422` | No | [app/models.py:17](app/models.py#L17) (`ReadingIn`), [app/main.py:126](app/main.py#L126) (future check) |
| **Possible but concerning** (outside the expected range) | 40 °C, humidity 90 %, battery 9 % | `201`, `is_anomaly: true` plus reasons | Yes | [app/anomaly.py:25](app/anomaly.py#L25) (`evaluate`) |
| **Normal** | 22.5 °C, 45 %, 88 % | `201`, `is_anomaly: false` | Yes | same |

Separately, each device has a **status**, computed when you ask for it ([app/anomaly.py:41](app/anomaly.py#L41), `compute_status`):
1. `stale` if its last reading arrived more than 30 s ago.
2. otherwise `anomaly` if its latest reading was flagged.
3. otherwise `ok`.

## A2. Code map

| File | Responsibility | Read this first |
|---|---|---|
| [app/main.py](app/main.py) | Builds the app (`create_app`) and holds every route | `create_reading` at [line 119](app/main.py#L119) |
| [app/models.py](app/models.py) | Request and response shapes. `ReadingIn` holds the **physical limits** | lines 17–25 |
| [app/anomaly.py](app/anomaly.py) | The **expected range** (thresholds) and the status rule | the whole file (47 lines) |
| [app/db.py](app/db.py) | All SQL: schema, insert, queries | `insert_reading` at [line 64](app/db.py#L64) |
| [app/middleware.py](app/middleware.py) | Runs around *every* request: request ID, access log, HTTP metrics, crash handling | `__call__` at [line 31](app/middleware.py#L31) |
| [app/logging_setup.py](app/logging_setup.py) | Turns every log call into one JSON line | `JsonFormatter.format` at [line 41](app/logging_setup.py#L41) |
| [app/faults.py](app/faults.py) | The fault-injection switch used by experiments | `apply` at [line 49](app/faults.py#L49) |
| [app/metrics.py](app/metrics.py) | Every Prometheus metric (Part B) | the definitions at lines 26–89 |
| [app/static/app.js](app/static/app.js) | The operator dashboard, which calls the same JSON API you call with curl | `refresh` at [line 308](app/static/app.js#L308) |
| [simulator/simulate.py](simulator/simulate.py) | 20 fake devices, 4 of them faulty on purpose | `Device.next_payload` at [line 64](simulator/simulate.py#L64) |
| [tests/](tests/) | 83 tests, one file per concern | `test_readings.py`, `test_metrics.py` |

## A3. Follow one reading through the code

Here's what happens when the simulator sends `POST /readings {"device_id":"dev-017","temperature_c":40, ...}`. Open each link as you read.

1. **The middleware sees it first** ([app/middleware.py:31](app/middleware.py#L31)).
   - It takes the caller's `X-Request-ID`, or creates one ([line 17](app/middleware.py#L17)).
   - It stores that ID in a context variable ([line 37](app/middleware.py#L37)), so *every* log line written during this request carries it.
   - It starts a timer and increments the in-flight gauge ([line 42](app/middleware.py#L42)).
2. **FastAPI validates the body** against `ReadingIn` ([app/models.py:17](app/models.py#L17)). If validation fails, the handler never runs. `on_validation_error` ([app/main.py:79](app/main.py#L79)) counts the rejection in metrics, logs `reading_rejected`, and returns `422`.
3. **The handler runs** ([app/main.py:119](app/main.py#L119)):
   1. `faults.apply()` ([line 121](app/main.py#L121)) may sleep or fail, but only if an experiment switched a fault on.
   2. It rejects timestamps more than 60 s in the future ([line 126](app/main.py#L126)).
   3. `evaluate()` ([line 139](app/main.py#L139)) returns `["temperature_high"]`.
   4. `db.insert_reading()` ([line 143](app/main.py#L143)) writes the reading **and** updates the device row in **one transaction** (`BEGIN IMMEDIATE`, [app/db.py:80](app/db.py#L80)). The first reading from a device creates its row (the upsert at [app/db.py:94](app/db.py#L94)).
   5. Metrics are recorded: the outcome counter ([line 156](app/main.py#L156)) and the anomaly-reason counter ([line 158](app/main.py#L158)).
   6. It logs `reading_stored` ([line 165](app/main.py#L165)) and `anomaly_detected` ([line 176](app/main.py#L176)).
4. **The middleware finishes**, in its `finally` block ([app/middleware.py:75–103](app/middleware.py#L75)).
   - It turns the URL into a **route template**: `/devices/dev-017` becomes `/devices/{device_id}` ([line 83](app/middleware.py#L83)).
   - It records the request counter and the latency histogram ([lines 85–87](app/middleware.py#L85)).
   - It writes one `http_request` log line, then adds `X-Request-ID` to the response.

**Try it:** put a breakpoint or a temporary `log.info(...)` in `create_reading`, run the app locally with `.venv/bin/python -m app`, and send a reading. Seeing the order for yourself makes it stick. Remove the change afterwards.

## A4. Use it yourself

### A4.1 The operator dashboard: http://localhost:8000

This is the view for *facilities staff*. It refreshes every 5 s ([app/static/app.js:308](app/static/app.js#L308)).

| On screen | What it tells you | Where the data comes from |
|---|---|---|
| Summary cards | Devices by status, and total readings | `GET /devices` |
| Device table | Latest values per device. **Orange values** are flagged, taken from the API's `anomaly_reasons`, not from thresholds copied into JavaScript ([app.js:82](app/static/app.js#L82)) | `GET /devices` |
| Click a row | The detail panel: counters, plus charts of the last 50 readings, with dots on flagged ones | `GET /devices/{id}` and `/readings?limit=50` |
| Recent anomalies | A fleet-wide alarm feed | `GET /anomalies` |
| Send a test reading | Presets for Normal, Too hot, Low battery and Impossible humidity. It shows 201/422 and **the request ID** | `POST /readings` |
| Fault injection | Switch on delay or errors; a yellow banner shows while a fault is on | `PUT/DELETE /admin/faults` |

**Try it (predict first):**
1. Click **dev-017**. Its temperature chart should be a sawtooth: it climbs 0.6 °C per reading and resets above 45 °C. Where is that behaviour defined? *([simulator/simulate.py:64](simulator/simulate.py#L64), and the constants at lines 29–30.)*
2. Send **Too hot** as `dev-100`. What status will dev-100 show now, and in 40 s? *(`anomaly`, then `stale`.)*
3. Send **Impossible humidity**. Does the device table change? *(No. A 422 stores nothing, so there's nothing new to show.)*

### A4.2 The interactive API: http://localhost:8000/docs

FastAPI generates this page from the code (the route definitions and Pydantic models). Open `POST /readings`, then **Try it out**, and edit the JSON. It's the fastest way to explore the validation rules.

### A4.3 Validation and anomalies from the terminal

Set a timestamp variable first; readings must include a timezone and can't be in the future:

```bash
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
```

Then send a reading:

```bash
curl -s -XPOST localhost:8000/readings -H 'content-type: application/json' -H 'X-Request-ID: learn-1' -d "{\"device_id\":\"dev-100\",\"timestamp\":\"$NOW\",\"temperature_c\":22.5,\"humidity_pct\":45,\"battery_pct\":88}"
```

**Predict the response code and `anomaly_reasons` for each body, then run it.** Change one field at a time.

| Change | Expected | Why (the code that decides) |
|---|---|---|
| `temperature_c: 40` | 201, `["temperature_high"]` | `evaluate`, [anomaly.py:25](app/anomaly.py#L25) |
| `temperature_c: 5, humidity_pct: 90, battery_pct: 9` | 201, `["temperature_low","humidity_high","battery_low"]` (always in this order) | same |
| `temperature_c: 35.0, humidity_pct: 20.0, battery_pct: 15.0` | 201, `[]`: the bounds are **inclusive** | [anomaly.py:10–14](app/anomaly.py#L10) |
| `humidity_pct: 150` | 422, `less_than_equal` on `humidity_pct` | `Field(le=100)`, [models.py:17](app/models.py#L17) |
| remove `temperature_c` | 422, `missing` | same model |
| `device_id: "sensor-1"` | 422, `string_pattern_mismatch` | `DEVICE_ID_PATTERN`, [models.py:12](app/models.py#L12) |
| timestamp `2026-09-26T10:00:00` (no timezone) | 422, `timezone_aware` | `AwareDatetime` |
| timestamp 1 hour ahead | 422, `timestamp_in_future` | our own check, [main.py:126](app/main.py#L126) |
| timestamp `…T13:00:00+05:00` | 201; stored as `…T08:00:00.000Z` | normalised to UTC, [app/timeutil.py:10](app/timeutil.py#L10) |

A real 422 response. `loc` says *where* the problem is and `type` says *what kind*:
```json
{"detail":[{"type":"less_than_equal","loc":["body","humidity_pct"],"msg":"Input should be less than or equal to 100","input":150,"ctx":{"le":100.0}}]}
```

### A4.4 Queries

Show one device, with its status, counters and latest reading:

```bash
curl -s localhost:8000/devices/dev-100 | jq
```

Show its history, newest first (ordered by *arrival*, not by device timestamp):

```bash
curl -s 'localhost:8000/devices/dev-100/readings?limit=3' | jq
```

Show only its anomalies:

```bash
curl -s 'localhost:8000/devices/dev-100/readings?anomalies_only=true' | jq
```

Show the fleet-wide alarm feed:

```bash
curl -s 'localhost:8000/anomalies?limit=5' | jq
```

Filter devices by status:

```bash
curl -s 'localhost:8000/devices?status=stale' | jq '.devices[].device_id'
```

Send a bad filter. Predict the response code first:

```bash
curl -s 'localhost:8000/devices?status=broken'
```

### A4.5 Watch a device go stale

Send one reading as `dev-100` (A4.3), then run this every 10 s:

```bash
curl -s localhost:8000/devices/dev-100 | jq -r .status
```

It changes from `ok` to `stale` about 30 s later. **Nothing wrote "stale" anywhere.** Each `GET` compares `last_seen` with the current time ([app/main.py:198](app/main.py#L198) → `compute_status`). Why is that better than a background job that marks devices stale? *(Hint: what would happen if that job crashed?)*

### A4.6 Follow your own request in the logs

You sent `X-Request-ID: learn-1` above. Now find every log line for it:

```bash
docker compose logs api --no-log-prefix | jq -c 'select(.request_id=="learn-1")'
```

You'll see `reading_stored` (plus `anomaly_detected` if it was anomalous) and `http_request` with `duration_ms`, all sharing the same `request_id`.

Now find one of the simulator's requests in **both** services' logs:

```bash
RID=$(docker compose logs simulator --no-log-prefix | jq -r 'select(.event=="sim_send_failed") | .request_id' | tail -1)
```

```bash
docker compose logs simulator api --no-log-prefix | jq -c "select(.request_id==\"$RID\") | {service,event,status_code,errors}"
```

This is the idea Part C builds on: **one ID, many log lines, across services**.

### A4.7 Fault injection

Add 500 ms to every 2nd reading:

```bash
curl -s -XPUT localhost:8000/admin/faults -H 'content-type: application/json' -d '{"delay_ms":500,"delay_every_n":2,"error_every_n":0}'
```

Watch latency in the access log:

```bash
docker compose logs api --no-log-prefix --since 10s | jq -r 'select(.path=="/readings") | "\(.status_code) \(.duration_ms)ms"'
```

Turn it off. **Always do this**, although a restart also resets it:

```bash
curl -s -XDELETE localhost:8000/admin/faults
```

How it works ([app/faults.py:49](app/faults.py#L49)): a counter picks exactly every Nth request, so an experiment can be repeated. The delay is `time.sleep` inside a sync handler, so only that one request waits.

### A4.8 The simulator: why each faulty device exists

| Device | Profile | Code path it exercises | Where |
|---|---|---|---|
| dev-017 | overheat, a sawtooth up to 45 °C | `temperature_high`; status flips between anomaly and ok | [simulate.py:64](simulator/simulate.py#L64), constants lines 29–30 |
| dev-018 | battery drains to 1 % | a permanent `battery_low` | lines 31–32 |
| dev-019 | 30 % invalid, rotating through 3 kinds | the 422 path, and validation-error metrics | lines 27, 34 |
| dev-020 | 60 s on, 60 s off | `stale` | line 35, `is_online` at line 59 |

Each device has its own seeded random generator, so a run can be repeated. That's what makes Part E's "repeatable test" possible.

## A5. Design decisions worth understanding

| Decision | Why | What would go wrong otherwise |
|---|---|---|
| Two layers: *impossible* → 422, *concerning* → anomaly | Garbage must not enter the database, but concerning data is exactly what operators need | Mixed together, you'd either store junk or throw away real alarms |
| Status computed on read | "Stale" means *nothing happened*, so there's no event to react to | A background job could crash, and devices would never go stale |
| Server clock for `last_seen`, device clock kept separately | Device clocks can be wrong or in another timezone | A device with a clock 1 h behind would look stale forever |
| A request ID on every log line | One request produces several lines, so you need something to join them | "What happened to reading X?" becomes unanswerable |
| Route *template* in logs and metrics | `/devices/{device_id}` is 1 value; `/devices/dev-001…dev-999` is 999 | Metrics get expensive (Part E's cardinality lesson) |
| Faults are deterministic (every Nth request) | Experiments must be repeatable | Random faults give different numbers every run |
| One uvicorn worker | `prometheus_client` keeps metrics in process memory | With 4 workers, each scrape would see only one worker's numbers |

## A6. Check yourself

<details><summary>1. A reading has humidity 85 % and battery 150 %. What's the response, and is anything stored?</summary>

`422`. Battery 150 breaks a *physical* limit (`le=100`), so validation fails before the anomaly check ever runs, and nothing is stored. Humidity 85 would have been an anomaly, but it never gets that far.
</details>

<details><summary>2. Why does <code>reading_count</code> for dev-019 grow more slowly than for dev-001?</summary>

About 30 % of dev-019's readings get a 422 and are never stored.
</details>

<details><summary>3. Where would you change the high-temperature threshold, and what else must change with it?</summary>

`TEMPERATURE_MAX_C` in [app/anomaly.py:11](app/anomaly.py#L11). Also update the boundary tests in `tests/test_anomaly.py`, and the README, the report and CLAUDE.md, which all state 35 °C. The dashboard needs no change, because it reads `anomaly_reasons` from the API.
</details>

<details><summary>4. The simulator got 6 connection errors during an API restart. Were those readings stored later?</summary>

No. The simulator doesn't retry ([simulate.py:113](simulator/simulate.py#L113)), so those readings are lost. That's a failure mode worth discussing in Part D.
</details>

---

# Part B — metrics

## B1. The concepts you need

**A time series** is a metric name plus one set of label values, e.g. `sensor_readings_total{outcome="anomaly"}`. Every 5 s, Prometheus **scrapes** (pulls) `GET /metrics` and appends one **sample** (timestamp, value) to each series. Different label values make different series.

**The four metric types**, with our real examples:

| Type | Behaviour | Our example | Code |
|---|---|---|---|
| **Counter** | Only goes up, and resets to 0 when the process restarts. You almost never chart it raw; you chart its `rate()` | `sensor_readings_total{outcome}` | [main.py:156](app/main.py#L156) `.inc()` |
| **Gauge** | Goes up and down; its value *is* the answer | `sensor_devices{status}`, `sensor_http_requests_in_progress` | [metrics.py:125](app/metrics.py#L125), [middleware.py:42](app/middleware.py#L42) |
| **Histogram** | Counts observations into cumulative buckets (`le` = "less than or equal"), plus `_sum` and `_count`. Percentiles are *calculated later* from the buckets | `sensor_http_request_duration_seconds` | [middleware.py:87](app/middleware.py#L87) `.observe()` |
| **Summary** | Only `_sum` and `_count` in Python. That gives you an **average**, not percentiles | `sensor_db_write_duration_seconds` | [main.py:142](app/main.py#L142) `.time()` |

**Four PromQL functions** explain almost everything on the dashboards:

| Function | Meaning | Example |
|---|---|---|
| `rate(c[1m])` | Per-second increase of a counter over the last 1 minute. It handles restarts | readings/s |
| `increase(c[1m])` | Total increase over the last minute (= rate × 60) | anomalies per minute |
| `sum by (label) (…)` | Add series together, keeping one label | readings/s *by outcome* |
| `histogram_quantile(0.95, sum by (le) (rate(h_bucket[1m])))` | The 95th percentile over the last minute | p95 latency |

**Cardinality** is the number of series. Each distinct label combination costs memory and CPU in Prometheus, forever (until retention deletes it). That's why labels only take values from small, fixed sets, and why `device_id` and `request_id` are **never** labels. They go in logs.

## B2. Layer 1: the raw `/metrics` page

Look at what the app hands to Prometheus:

```bash
curl -s localhost:8000/metrics | grep -A5 '^# HELP sensor_readings_total'
```

Real output:
```
# HELP sensor_readings_total POST /readings attempts by outcome: normal/anomaly = stored (201), rejected = invalid (422, not stored), failed = server error (5xx, not stored).
# TYPE sensor_readings_total counter
sensor_readings_total{outcome="normal"} 18292.0
sensor_readings_total{outcome="anomaly"} 1485.0
sensor_readings_total{outcome="rejected"} 318.0
sensor_readings_total{outcome="failed"} 0.0
```

The same for the latency histogram (some buckets omitted):
```
# TYPE sensor_http_request_duration_seconds histogram
sensor_http_request_duration_seconds_bucket{le="0.015",method="POST",route="/readings"} 5579.0
sensor_http_request_duration_seconds_bucket{le="0.02",method="POST",route="/readings"} 19327.0
sensor_http_request_duration_seconds_bucket{le="0.025",method="POST",route="/readings"} 19957.0
sensor_http_request_duration_seconds_bucket{le="+Inf",method="POST",route="/readings"} 20095.0
sensor_http_request_duration_seconds_count{method="POST",route="/readings"} 20095.0
sensor_http_request_duration_seconds_sum{method="POST",route="/readings"} 303.50666047882623
```

**How to read it:**
- **Buckets are cumulative.** 19,327 requests took ≤ 20 ms, and that *includes* the 5,579 that took ≤ 15 ms. So 19,327 − 5,579 = 13,748 took between 15 and 20 ms.
- `+Inf` always equals `_count`.
- `_sum / _count` = 303.5 / 20,095 ≈ **15.1 ms**, the average since the app started.

**Try it:**
1. Note `sensor_anomalies_total{reason="temperature_low"}`. It's probably `0.0`, because no simulated device gets that cold.
2. Send a 5 °C reading (A4.3).
3. Fetch `/metrics` again. It went up by exactly 1. You just watched the `.inc()` at [main.py:158](app/main.py#L158) happen.

## B3. Layer 2: Prometheus

**Check the targets first:** http://localhost:9090/targets. All three should be `UP`. If a target is `DOWN`, every chart that uses it goes blank. That's the first thing to check when a dashboard looks wrong.

Next, open http://localhost:9090/query. Type a query, click **Execute**, and switch between the **Table** tab (the value now) and the **Graph** tab (over time). In Prometheus 3 the **Explain** tab describes any query in plain English, which is useful for learning.

### Exercise 1: build a rate query one layer at a time

Run each line and see how the result changes:

| # | Query | Real result | What changed |
|---|---|---|---|
| 1 | `sensor_readings_total` | normal 18284, anomaly 1485, rejected 318, failed 0 | Raw counters, totals since the app started. Not useful on a chart; they only go up |
| 2 | `rate(sensor_readings_total[1m])` | normal 3.64/s, anomaly 0.20/s, rejected 0, failed 0 | **Per second, over the last minute**. Now it's comparable over time |
| 3 | `sum(rate(sensor_readings_total[1m]))` | 3.84/s | All outcomes added: the total ingest rate (20 devices ÷ 5 s ≈ 4/s) |
| 4 | `sum(rate(sensor_readings_total{outcome="rejected"}[1m])) / sum(rate(sensor_readings_total[1m]))` | 0 at that moment | A **ratio**: the share rejected. It was 0 because dev-019 happened to send only valid readings that minute. Try `[5m]` and see how a longer window smooths it |

### Exercise 2: calculate p95 by hand, then let Prometheus do it

| # | Query | Real result (key buckets) |
|---|---|---|
| 1 | `sensor_http_request_duration_seconds_bucket{route="/readings",method="POST"}` | le=0.015: 5576 · le=0.02: 19321 · +Inf: 20087 (cumulative totals) |
| 2 | `rate(sensor_http_request_duration_seconds_bucket{route="/readings",method="POST"}[1m])` | le=0.015: **1.636/s** · le=0.02: **3.800/s** · +Inf: **3.836/s** |
| 3 | `histogram_quantile(0.95, sum by (le) (rate(…_bucket{…}[1m])))` | **0.01964** (19.64 ms) |

Now do what `histogram_quantile` does, by hand:
1. The 95th percentile is at 0.95 × 3.836 = **3.644** requests/s.
2. Find the first bucket whose rate reaches 3.644. `le=0.015` is only at 1.636, and `le=0.02` is at 3.800, so p95 lies **between 15 and 20 ms**.
3. Assume requests are spread evenly inside that bucket, and interpolate: 15 + (3.644 − 1.636) / (3.800 − 1.636) × 5 = 15 + 0.928 × 5 = **19.64 ms**. ✔ That's exactly Prometheus's answer.

**The lesson:** the answer can only be as precise as the buckets. The real p95 could be anywhere from 15 to 20 ms, and the formula just assumes an even spread. The report ([REPORT.md § B.4](REPORT.md#b4-percentiles-and-time-window)) shows what happened with coarser buckets: p99 read 44 ms when the truth was 25 ms.

### Exercise 3: see the cost of labels

Count the app's series:

```bash
curl -s --get localhost:9090/api/v1/query --data-urlencode 'query=count({job="sensor-api"})' | jq -r '.data.result[0].value[1]'
```

The result was 255. Where do they come from? Try this in the Prometheus UI:

```promql
count by (__name__) ({job="sensor-api", __name__=~"sensor_.*"})
```

The histogram's buckets are **176** of them: 11 method-and-route pairs × 16 (15 buckets plus `+Inf`).

Now send one request with a **new** method-and-route pair:

```bash
curl -s -XDELETE localhost:8000/devices
```

It returns `405`. Wait 5 s and count again. It went from 255 to **274**, i.e. +19: 16 buckets, `_count`, `_sum` and one counter series. Send the same request again, and the count stays at 274.

**Series grow with distinct label combinations, not with traffic.** Now imagine `device_id` as a label with 10,000 devices: that's 10,000 × 16 series *per route*. This is exactly what Part E's cardinality experiment measures.

## B4. Layer 3: Grafana

**Access:**
- http://localhost:3000 opens on the app dashboard; no login is needed to view.
- **Sensor Ingestion Service:** http://localhost:3000/d/sensor-service
- **Host (Node Exporter):** http://localhost:3000/d/node-host
- Live, last 5 minutes, refreshing every 5 s: http://localhost:3000/d/sensor-service?from=now-5m&to=now&refresh=5s

**Navigating:**
- The **time picker** (top right) sets the range. Drag across any chart to zoom in.
- Hover over a chart to see every series' value at that moment.
- The **ⓘ** icon next to a panel title shows its description.
- **To see any panel's query:** hover over the panel, open its menu (⋮ next to the title), then **Inspect → Query**, or **Inspect → Data** for the numbers. If an option is missing, log in as `admin` / `admin` (bottom-left or top-right *Sign in*). Logged in, you can also use **Edit** and **Explore** to try your own queries.
- **Why you can't save edits:** the dashboards are *provisioned from files* ([monitoring/grafana/dashboards/](monitoring/grafana/dashboards/)), so the files stay the source of truth (B6 shows how to change them).

### Reading the Sensor Ingestion Service dashboard, panel by panel

For each panel: the question it answers, what **normal** looks like here, and what a change would mean.

| Panel | Question it answers | Normal | If it changes… |
|---|---|---|---|
| *Stored readings / s* | Is data flowing in? | ≈ 3.8–4.0 | Drops to 0: the simulator or the network is down. A small drop: some devices are silent |
| *p95 latency* | How slow are the slow requests? | ≈ 20 ms | Rises: something is slowing some requests (try the delay fault) |
| *Server errors (5xx)* | Is the server failing? | 0 % | Anything above 0 means real failures. A 422 is the client's fault and isn't counted here |
| *Rejected readings (422)* | Is someone sending bad data? | ≈ 1–2 % (dev-019) | A jump points to a misbehaving integrator; check *Validation errors* to see which field |
| *Devices by status* | Is the fleet healthy? | ~18 ok, 2 anomaly, 1–2 stale | stale rising: devices went silent. anomaly rising: environment problems |
| *Fault injection* | Is an experiment running? | off / off | ACTIVE: explains any latency or error spike |
| *Request rate by route* | Where does the traffic go? | `/readings` ≈ 4/s, the rest ≈ 0.2/s | A new route appearing: a new client or feature |
| *Latency percentiles* | What does the latency *distribution* look like? | p50 ≈ 16, p95 ≈ 20, p99 ≈ 24 ms | p95/p99 rising while p50 stays flat: **only some** requests are slow |
| *Responses by status code* | What mix of outcomes? | almost all 201, a sliver of 422 | 500s appear: failures |
| *Apdex* | One satisfaction score, 0–1 | ≈ 0.98 | Drops as soon as *any* fraction of requests gets slower than 25 ms |
| *DB write time (average)* | Is the database the bottleneck? | ≈ 8 ms | Rises: disk or lock contention. Compare it with p50 to see how much of each request is database time |
| *Readings by outcome* | Business view: what happens to the data | green (normal) with an orange band that steps between 0.2 and 0.4/s | failed (red) appears: data is being lost |
| *Devices by status* (over time) | When did devices go stale or anomalous? | a grey notch every 2 min (dev-020) | wide grey: many devices silent at once, i.e. a network problem, not a sensor problem |
| *Anomalies per minute by reason* | Which alarms are firing? | `battery_low` flat at 12, `temperature_high` in waves | a new reason appearing: a new environmental problem |
| *Validation errors per minute* | *Why* is data rejected? | dev-019's three kinds, ≈ 1/min each | a new field or reason: a new integration bug |
| *Anomaly share* | What share of stored data is flagged? | 5–10 % | — |
| *Fault switches* · *Faults injected* | Experiment on/off, and how many requests it hit | 0 | — |
| *Requests in flight* | Are requests piling up? | 1 (the scrape itself) | Above 1 under a delay fault: requests are waiting |
| *API process CPU and memory* | Is the app itself struggling? | ≈ 0.04 cores, ≈ 50 MiB | Memory climbing forever: a leak |

**Try it:** for three panels, first *predict* the query from the question the panel answers. Then open **Inspect → Query** and compare. If you can predict the queries, you understand the dashboard.

### Reading the Host (Node Exporter) dashboard

This shows the **machine**, not the app. Node Exporter reads the host's `/proc` and `/sys`: it runs with `network_mode: host`, `pid: host` and `/` mounted read-only ([docker-compose.yml](docker-compose.yml), service `node-exporter`).

| Panel | Read it as |
|---|---|
| Hostname, Hardware, Kernel | *Which machine is this?* That's what the report's "name the machine" requirement means |
| CPU busy by mode | `user` = programs, `system` = the kernel, `iowait` = waiting for disk. 3–5 % is idle for this laptop |
| Load average vs CPUs | Load below the dashed CPU line means nothing is queuing |
| Memory | used vs available. "Available" includes cache the kernel can reclaim |
| Filesystem used | per real disk partition |
| Disk / Network throughput | bytes/s. The steady ~1 MB/s of disk writes includes SQLite and Prometheus |

**Try it:** run `nproc`, `free -h` and `cat /proc/loadavg` on the host, and compare the numbers with the dashboard. This is how the report verified that Node Exporter measures the host and not a container.

## B5. Experiments to run yourself

All of these are safe and easy to undo. **Write down your prediction first**, then keep the Grafana dashboard open with `from=now-5m&refresh=5s`. Wait at least 1–2 minutes per phase, because every panel uses a 1-minute window.

| # | Do | Predict: which panels change, and how? | Undo |
|---|---|---|---|
| 1 | Delay 500 ms on every 5th reading: `curl -s -XPUT localhost:8000/admin/faults -H 'content-type: application/json' -d '{"delay_ms":500,"delay_every_n":5,"error_every_n":0}'` | p50? p95? p99? Apdex? Stored readings/s? *Faults injected per minute* ≈ ? (a fifth of about 240) | `curl -s -XDELETE localhost:8000/admin/faults` |
| 2 | Fail every 10th reading: `… -d '{"delay_ms":0,"delay_every_n":1,"error_every_n":10}'` | 5xx stat? The `failed` band? Does *Readings by outcome*'s total change? | same |
| 3 | Stop the devices: `docker compose stop simulator` | Readings/s? After 30 s, *Devices by status*? p95? (Hint: a percentile of *zero* requests is not a number) | `docker compose start simulator` |
| 4 | Restart the app: `docker compose restart api` | Raw counters reset to 0. Do the `rate()` panels drop to 0, or dip briefly? What does Prometheus show for `up{job="sensor-api"}`? | none needed |
| 5 | Stop Prometheus: `docker compose stop prometheus` | Does the *app* notice? What does Grafana show for those minutes after it restarts? | `docker compose start prometheus` |
| 6 | New label combination: `curl -s -XDELETE localhost:8000/devices` (B3, exercise 3) | How many new series? | none |

<details><summary>What to expect (open only after predicting)</summary>

1. **p50 stays about the same**, because 80 % of requests are untouched. **p95 and p99 jump toward 0.5 s**, and Apdex drops. *Faults injected* reads about 48/min. *Requests in flight* often reads 2 (a delayed request plus the scrape itself). Stored readings/s barely moves: the simulator falls behind for a moment and then catches up, because 5 requests take about 0.6 s of its 1.25 s budget. This is the heart of Part E1. Note how it shows up in latency *percentiles*, not in the error rate.
2. A red `failed` band of about 0.4/s appears in *Readings by outcome*, a tenth of the readings. The *Server errors (5xx)* stat rises to about 7 %, not 10 %: it divides by *all* HTTP requests, including the dashboard's polling and the scrapes. The total barely changes, but *stored* readings drop, and those readings are lost. The same failure gives different percentages depending on the denominator, so always check what a ratio divides by.
3. Readings/s decays to 0 over about a minute: the `[1m]` window empties gradually. All devices turn `stale` about 30 s after their last reading. p95 goes blank, because 0/0 is not a number.
4. `up` may show 0 for one scrape. A restart takes about 5 s, so it can fall between two scrapes. The raw counters (`sensor_readings_total` in the Prometheus Table tab) drop back to near 0. `rate()` is designed to handle counter resets, so the rate panels only dip briefly. The simulator logs a few connection errors and carries on.
5. The app doesn't notice at all, because Prometheus *pulls*. Grafana shows a **gap** for the minutes Prometheus was down. Nothing buffers a pull, so that data is simply never collected.
6. +19 series the first time, 0 the second.
</details>

Experiments 3–5 are **Part D's "what happens if a component stops working"** questions. You've now seen the answers yourself instead of reading them.

## B6. From what you see to the report

For each section of [REPORT.md](REPORT.md): where to check it, and how to change it.

| Report section | How to verify it yourself | How to change it |
|---|---|---|
| B.1 Setup (versions, scrape every 5 s, 7-day retention) | `docker compose config`, [monitoring/prometheus/prometheus.yml](monitoring/prometheus/prometheus.yml), http://localhost:9090/targets | Edit the config, then `curl -XPOST localhost:9090/-/reload` |
| B.2 Metric table: *type, labels* | `curl -s localhost:8000/metrics \| grep '^# TYPE sensor_'`, [app/metrics.py](app/metrics.py) | Add or change metrics in `app/metrics.py`, record them where the event happens, add a test in `tests/test_metrics.py`, then update the table |
| B.2 *where recorded* | the links in [A3](#a3-follow-one-reading-through-the-code) | — |
| B.2 *Grafana query / what the chart shows* | Grafana: panel → Inspect → Query; look at the chart | Edit `monitoring/grafana/dashboards/sensor-service.json` (Grafana reloads it within 30 s). Or log in, edit in the UI, then export the dashboard as JSON (*Export → Export as JSON*; the menu name differs slightly between Grafana versions), and replace the file |
| B.3 Screenshots and their interpretations | Open the same panels. The spikes described (16:34–16:37) are ones you can find in the logs by `ui-` request IDs | Re-capture with `scripts/capture_screenshots.sh 30` (or pass an explicit end time), look at them, then rewrite the interpretation to match what *they* show |
| B.4 p95/p99 and the window | Exercise 2 above (by hand), and the logs-vs-histogram check in [docs/BUILD_LOG.md](docs/BUILD_LOG.md) (stage B7) | — |
| B.5 Apdex | Paste the query from the report into Prometheus | Change T by picking two bucket bounds (T and 4T must *be* bucket bounds) |
| B.6 Node Exporter, the machine named | `node_uname_info` in Prometheus, the Host dashboard, `nproc` and `free -h` | — |
| Any number anywhere | [docs/BUILD_LOG.md](docs/BUILD_LOG.md) has the exact command behind it | Re-run the command and update both files |

**A good habit:** before you trust a number in the report, reproduce it once. If you can't, the report is wrong, or you've found something worth understanding.

## B7. Check yourself

<details><summary>1. Why is <code>sensor_readings_total</code> a counter and <code>sensor_devices</code> a gauge?</summary>

Readings only ever accumulate, and what you care about is how *fast* they come (`rate`). Device counts go up *and* down, and the current value is the answer itself.
</details>

<details><summary>2. Why can't we get p95 from <code>sensor_db_write_duration_seconds</code>?</summary>

It's a Python Summary, which exposes only `_sum` and `_count`, so all you can get is an average. For percentiles you need a histogram's buckets.
</details>

<details><summary>3. p50 stays at 16 ms but p99 jumps to 400 ms. What's happening to users?</summary>

Most requests are fine, but about 1 % or more are very slow. Averages and medians hide this; that's why percentiles, and Apdex, exist.
</details>

<details><summary>4. Why is <code>rate(x[1m])</code> wrong for the first minute after a restart?</summary>

`rate()` divides the increase by the whole 60 s window. Until the window holds a full minute of samples, it underestimates. We saw 0.56/s when the true rate was 3.9/s.
</details>

<details><summary>5. Someone proposes adding <code>device_id</code> as a label on the latency histogram "for debugging". What do you say?</summary>

That's 16 series per device per route: 20 devices × 11 routes × 16 is already about 3,500 series, and a real fleet has thousands of devices. Put `device_id` in the logs, where one search finds it, and keep metric labels small and bounded.
</details>

<details><summary>6. Grafana shows a gap from 14:02 to 14:05. The app's logs show normal traffic then. What stopped?</summary>

Prometheus, or its connection to the app. The app was fine, but nothing was scraping it. Check `up{job="sensor-api"}` and `docker compose ps`.
</details>

---

# Part C — logs

## C1. The concepts you need

In Part B, metrics told you *that* something happened ("3 rejections per minute"). **Logs tell you *what* happened to each individual request**: which device, which field, which request ID. Part C turns the log lines the app already writes into something you can **keep, search and deliberately delete**.

**The pipeline**, and what each stage is for:

```
 app/logging_setup.py        Docker json-file driver         Filebeat                    Elasticsearch               Kibana
 one JSON object per   ──►   wraps + saves each line   ──►   collect, parse into   ──►   store + index each   ──►   search with KQL
 line on stdout              /var/lib/docker/containers/     fields, ship (push)         field; delete old          (Discover)
 (WRITE)                     <id>/<id>-json.log (BUFFER)     (COLLECT + PARSE)           data (STORE + RETAIN)      (FIND)
```

| Term | Meaning | Here |
|---|---|---|
| **Structured log** | Each line is data with named fields, not a sentence you have to parse with regexes | Our JSON lines |
| **Document** | One stored log line in Elasticsearch | One `_source` |
| **Index / mapping** | Where documents live; the mapping says each field's **type** (`keyword` = exact value, `text` = full-text words, numbers, dates) | `monitoring/elasticsearch/index-template.json` |
| **Data stream** | A name you append logs to (`sensor-logs`). Behind it sit **backing indices**, `.ds-sensor-logs-<date>-000001`, `-000002`, … | |
| **ILM** (index lifecycle management) | Rules for when to start a new backing index (**rollover**) and when to **delete** old ones: *retention* | `monitoring/elasticsearch/ilm-policy.json` |
| **KQL** | Kibana's query language: `field : value`, `and`/`or`/`not`, `>`/`<` | `request_id : "abc"` |
| **Push vs pull** | Prometheus *pulls* metrics from the app. Filebeat *pushes* logs to Elasticsearch, and the app knows about neither | |

The big lesson: **logs are only as useful as their structure.** Because our lines are JSON with a stable `event` name and a `request_id`, Filebeat needs no parsing rules at all, and every search is a field lookup instead of a text grep.

## C2. Layer 1: what the app writes (stdout)

**Code:**
- The formatter: [app/logging_setup.py:41](app/logging_setup.py#L41).
- The whitelist of extra fields, the only fields besides the base ones that can ever reach a log: [app/logging_setup.py:15](app/logging_setup.py#L15).
- The request ID on every line: [app/logging_setup.py:49](app/logging_setup.py#L49), set by the middleware at [app/middleware.py:37](app/middleware.py#L37).

**Where each event is written:**

| Event | Line |
|---|---|
| `http_request` | [middleware.py:96](app/middleware.py#L96) |
| `reading_stored` | [main.py:165](app/main.py#L165) |
| `anomaly_detected` | [main.py:176](app/main.py#L176) |
| `reading_rejected` | [main.py:90](app/main.py#L90) |
| `fault_injected` | [faults.py:58](app/faults.py#L58) |
| `sim_send_failed` | [simulate.py:126](simulator/simulate.py#L126) |

**Try it:** send a reading with your own ID (e.g. `-H 'X-Request-ID: me-c-1'`, from [A4.3](#a43-validation-and-anomalies-from-the-terminal)), then print its lines:

```bash
docker compose logs api --no-log-prefix | jq -c 'select(.request_id=="me-c-1")'
```

**Predict first:** how many lines will a *valid, anomalous* reading produce? And a *rejected* one? *(3: stored + anomaly + http_request. 2: rejected + http_request.)*

## C3. Layer 2: Docker's log file (the buffer)

Docker saves every stdout line to a file owned by root, wrapped in an **envelope**. You can't read it as your user, but the Filebeat container mounts it read-only, so read it through Filebeat.

Get the full ID of the running `api` container:

```bash
CID=$(docker compose ps -q api | xargs docker inspect -f '{{.Id}}')
```

Print its last log line, as Docker stored it:

```bash
docker compose exec -T filebeat tail -1 /var/lib/docker/containers/$CID/$CID-json.log
```

You'll see `{"log":"<our JSON>\n","stream":"stdout","time":"…"}`: our line *inside* a string, plus Docker's own timestamp.

**Rotation:** [docker-compose.yml](docker-compose.yml) (the `x-app-logging` anchor) caps each container at 3 files × 10 MB. Check it:

```bash
docker inspect $CID --format '{{json .HostConfig.LogConfig}}'
```

**Predict:** what happens to this file when the container is *re-created* (`docker compose up -d --force-recreate api`)? *(It's deleted with the old container, and the new container starts a new, empty file. That's why Docker's copy is only a buffer. Try it in [C7](#c7-experiments-to-run-yourself).)*

## C4. Layer 3: Filebeat (collect and parse)

Read [monitoring/filebeat/filebeat.yml](monitoring/filebeat/filebeat.yml) top to bottom. It's short, and every step is commented.

| Lines | What it does | Why |
|---|---|---|
| [13–35](monitoring/filebeat/filebeat.yml#L13) | **Autodiscover** asks Docker (through `docker.sock`) which containers exist, and starts one `filestream` input per container named `esd_hw1-(api\|simulator)-N` | Only our two services; a re-created container is followed automatically |
| [32](monitoring/filebeat/filebeat.yml#L32) | The `container` parser unwraps the envelope | `message` becomes our JSON line |
| [39](monitoring/filebeat/filebeat.yml#L39) | `copy_fields` → `log.original` | Keeps the untouched line for the "original → stored" comparison |
| [49](monitoring/filebeat/filebeat.yml#L49) | `decode_json_fields` → top-level fields | This is the parsing step: `event`, `request_id`, … become searchable fields |
| [55](monitoring/filebeat/filebeat.yml#L55) | `timestamp` → `@timestamp` | Order events by when the app wrote them |
| [67](monitoring/filebeat/filebeat.yml#L67) | `drop_fields` | Removes noise, **and the Docker labels, which contained `/home/<your name>/…`** |
| [71–79](monitoring/filebeat/filebeat.yml#L71) | Output to data stream `sensor-logs`; Filebeat's own template off | We provide the mapping and retention ourselves |

Filebeat remembers how far it has read each file in its **registry** (volume `filebeat-data`). That's why a restart doesn't re-send old lines, and why a stopped Filebeat catches up instead of losing lines.

**Try it:**
- See Filebeat's own warnings and errors (it's quiet when healthy):
  ```bash
  docker compose logs filebeat --no-log-prefix | jq -r 'select(.["log.level"]!="info") | .message' | tail
  ```
- See which inputs it started:
  ```bash
  docker compose logs filebeat --no-log-prefix | jq -r 'select(.message|test("filestream")) | .message' | tail -3
  ```

**What went wrong while building this**, worth knowing (docs/BUILD_LOG.md, C3):
1. The first version matched containers by Compose *label* and silently matched **nothing**: "Enabled inputs: 0". A debug run showed that Filebeat stores `com.docker.compose.service` as nested keys. Matching on the container *name* fixed it.
2. The first documents carried the host path `/home/muhammad-affan/…` from Docker labels. **Always look at a real stored document before trusting a pipeline.**

## C5. Layer 4: Elasticsearch (store, index, retain)

It's at http://localhost:9200 (localhost only; security is off, which is fine for a laptop and never acceptable for a server).

Count the stored log documents:

```bash
curl -s localhost:9200/sensor-logs/_count | jq .count
```

List the backing indices behind the data stream:

```bash
curl -s 'localhost:9200/_cat/indices/.ds-sensor-logs*?v&h=index,docs.count,creation.date.string&s=index'
```

Show the retention state of each backing index:

```bash
curl -s localhost:9200/sensor-logs/_ilm/explain | jq -c '.indices[] | {index, phase, action, age}'
```

Show the mapping, i.e. each field's type:

```bash
curl -s localhost:9200/sensor-logs/_mapping | jq -c '.[].mappings.properties | map_values(.type)' | head -1
```

Find the documents for your request:

```bash
curl -s localhost:9200/sensor-logs/_search -H 'Content-Type: application/json' -d '{"query":{"term":{"request_id":"me-c-1"}}}' | jq '.hits.hits[]._source'
```

**Read the template** ([monitoring/elasticsearch/index-template.json](monitoring/elasticsearch/index-template.json)). Three details matter:
- `"data_stream": {}` (line 3): logs are append-only.
- `"index.lifecycle.name"` (line 12): every new backing index gets the retention policy.
- `"dynamic": false` (line 15): only the fields listed are searchable.

**Exercise: `dynamic: false` is not a privacy control.** Predict first: if a document contains an unexpected field `password`, is it searchable? Is it stored?

Write a test document containing a `password` field to a throwaway data stream:

```bash
curl -s -XPOST 'localhost:9200/sensor-logs-test/_doc?refresh=true' -H 'Content-Type: application/json' -d '{"@timestamp":"2026-09-26T10:00:00Z","event":"test","password":"hunter2"}'
```

Search for it by that field:

```bash
curl -s localhost:9200/sensor-logs-test/_search -H 'Content-Type: application/json' -d '{"query":{"match":{"password":"hunter2"}}}' | jq .hits.total.value
```

Now fetch the stored document itself:

```bash
curl -s localhost:9200/sensor-logs-test/_search | jq '.hits.hits[0]._source'
```

Delete the throwaway data stream:

```bash
curl -s -XDELETE localhost:9200/_data_stream/sensor-logs-test
```

<details><summary>What happens</summary>

The search returns **0 hits**, because the field isn't indexed. But `_source` still **contains `"password":"hunter2"`**. The mapping controls what you can *search*, not what's *kept*. That's why privacy is enforced where lines are written: `EXTRA_FIELDS` in the app.
</details>

**Retention, the numbers:** [ilm-policy.json](monitoring/elasticsearch/ilm-policy.json) rolls over every 10 min (line 11) and deletes a backing index 1 h after its rollover (line 17). ILM checks every minute (set by [monitoring/logs-setup.sh](monitoring/logs-setup.sh)). So logs live for about 1 h 10 min. Run the `_cat/indices` command above twice, 10 minutes apart, and watch a new `-00000N` appear. An hour later the oldest one disappears.

## C6. Layer 5: Kibana (find)

Open **http://localhost:5601/app/discover**. There's no login, and it opens on the **Sensor logs** data view. How to use it:

| You want to… | Do this |
|---|---|
| Set the time window | Time picker (top right), e.g. *Last 15 minutes*. **Nothing older than about 1 h exists** (retention) |
| Search | Type KQL in the search bar and press Enter |
| Pick columns | In the field list on the left, hover over a field and click ⊕ (e.g. `event`, `level`, `request_id`) |
| See one whole document | Click the ↗ (expand) icon on a row; *Table* shows each field, *JSON* the stored document |
| See a field's values | Click a field name in the left list: top 5 values and their share |
| Open a saved search | **Open** (folder icon) → e.g. *Rejected readings (422)* |
| Jump from one line to all lines of its request | Expand a document, hover over the `request_id` row, and use its *Filter for value* action (a ⊕ or magnifier icon, depending on the Kibana version). Or just type `request_id : "…"` |

**KQL cheat sheet:**

| KQL | Meaning |
|---|---|
| `request_id : "me-c-1"` | exact match on a keyword field |
| `level : ("ERROR" or "WARNING")` | either value |
| `event : "http_request" and duration_ms > 100` | combine; numeric comparison (works because `duration_ms` is mapped as `float`) |
| `service : "simulator" and not event : "sim_summary"` | exclude |
| `device_id : dev-01*` | wildcard |
| `errors.field : "humidity_pct"` | a field inside an object |

**Exercises (predict, then search):**
1. **Follow your own request.** Search `request_id : "me-c-1"`. How many documents, and which events? Compare with your C2 prediction.
2. **Why does dev-019 fail?** Search `event : "reading_rejected"` and add the columns `errors.field` and `errors.type`. How many different reasons are there? *(3, rotating: humidity 150, a missing temperature, a future timestamp. See [simulate.py:27](simulator/simulate.py#L27).)*
3. **Same failure, two viewpoints.** Take one rejected row's `request_id` (`sim-…`) and search for it. You'll get the API's `reading_rejected` + `http_request` **and** the simulator's `sim_send_failed`. One ID, two services.
4. **Severity over time.** Search `level : "WARNING"`, then click the `event` field on the left. Which event produces most warnings? *(`anomaly_detected`, mostly dev-018.)*
5. **Metrics versus logs, the same events.** Set Kibana to *Last 15 minutes* and note the number of `event : "reading_rejected"` documents. Then, in Prometheus (http://localhost:9090/query), run `sum(increase(sensor_readings_total{outcome="rejected"}[15m]))`. The two should roughly agree: the same rejections, counted once as a metric (a number) and once as logs (one document each). They won't match exactly, because `increase()` extrapolates to the edges of the window and the two windows don't start at exactly the same instant.

The report's screenshots of these views, with what each shows, are in [REPORT.md § C.4–C.5](REPORT.md#c4-searching-in-kibana).

## C7. Experiments to run yourself

Write down a prediction for each row first. For every row, what matters is **where the log survives**.

| # | Do | Predict: `docker compose logs api` / Elasticsearch? |
|---|---|---|
| 1 | Send `X-Request-ID: me-c-2`, then `docker compose restart api` | |
| 2 | `docker compose up -d --force-recreate api` | |
| 3 | `docker compose stop filebeat`, send `me-c-3`, wait 20 s, check Elasticsearch, then `docker compose start filebeat` and check again | |
| 4 | `docker compose restart filebeat`, then count `me-c-2`'s documents | duplicates? |
| 5 | `docker compose restart elasticsearch` (wait until it's healthy) | |
| 6 | `docker compose stop kibana` | does ingestion stop? |
| 7 | Come back 80 minutes after sending `me-c-2` and search for it | |

<details><summary>What to expect</summary>

1. Both still have it: same container, same file.
2. `docker compose logs` has **lost** it (new container, new file). Elasticsearch still has it. This is the reason for Part C.
3. While stopped, it's in Docker's file but not in Elasticsearch. After the start it appears: Filebeat resumes from its registry position.
4. The same count, with no duplicates: the registry remembers what was already sent.
5. Everything is still there (the `es-data` volume).
6. No. Documents keep arriving (`_count` grows). Kibana is only the viewer, and it frees about 1.7 GB of RAM.
7. Gone: retention deleted its backing index. That's working as designed, not data loss.
</details>

Experiments 2, 3, 5 and 6 are Part D material: *what happens when a component stops*.

## C8. From what you see to the report

| Report section | How to verify it yourself | How to change it |
|---|---|---|
| C.1 What's logged, and where | The event table in [C2](#c2-layer-1-what-the-app-writes-stdout); `docker compose logs api --no-log-prefix \| jq -r .event \| sort \| uniq -c` | Add or change log calls in the app. A **new field** must go into `EXTRA_FIELDS` *and* `index-template.json` |
| C.2 Filebeat parsing | [filebeat.yml](monitoring/filebeat/filebeat.yml); compare `log.original` with the other fields of any document | Edit `filebeat.yml`, then `docker compose restart filebeat` |
| C.3 Storage and retention | The `_cat/indices` and `_ilm/explain` commands in [C5](#c5-layer-4-elasticsearch-store-index-retain); the experiments in [C7](#c7-experiments-to-run-yourself) | Edit `ilm-policy.json`, then `docker compose up logs-setup` |
| C.4 Kibana searches | Open each saved search | Edit `monitoring/kibana/saved-objects.ndjson`, then `docker compose up logs-setup` (it overwrites) |
| C.5 The worked example | Repeat it with your own request ID (C2 → C3 → C5 → C6) | The example's documents are deleted by retention after about an hour, so the report's IDs won't be findable later. Its screenshots and quoted lines are the record |
| C.6 Privacy | Plant a fake secret (e.g. an `Authorization` header) and search `_source`, as in docs/BUILD_LOG.md C6 | — |
| Screenshots | The Discover URLs are in docs/BUILD_LOG.md C5 | Recapture with headless Chrome, and rewrite the interpretation to match what *they* show |

## C9. Check yourself

<details><summary>1. Why are the logs JSON instead of readable sentences?</summary>

So that every field can be searched and typed without fragile parsing. A human sentence is still there, in `message`.
</details>

<details><summary>2. A log line never appears in Kibana. List the places to check, in order.</summary>

1. Did the app print it? (`docker compose logs api`)
2. Is Filebeat running, and did it start an input for that container? (its logs, "filestream starting")
3. Did Elasticsearch reject the document, for example because of a mapping conflict? (Filebeat's logs, WARN level)
4. Is Kibana's time range right? And has retention already deleted it?
</details>

<details><summary>3. Why must Filebeat start only after <code>logs-setup</code> has finished?</summary>

The index template must exist before the first document is written. Otherwise Elasticsearch creates `sensor-logs` with guessed field types and no retention policy.
</details>

<details><summary>4. Why is <code>request_id</code> a great log field but a terrible metric label?</summary>

In Elasticsearch it's one indexed value per document: cheap, and found in milliseconds. As a Prometheus label, every ID would create a new time series forever (Part E2).
</details>

<details><summary>5. Is short retention a bug?</summary>

No. It's a trade-off between cost, privacy and how far back you can investigate. The demo uses about 1 hour so it can be *watched*; production would keep days or weeks, which is a one-line change.
</details>

---

## Glossary

| Term | Meaning |
|---|---|
| **Scrape** | Prometheus fetching `GET /metrics` from a target (every 5 s here) |
| **Target** | Something Prometheus scrapes: `api:8000`, Node Exporter, itself |
| **Series** | A metric name plus one set of label values |
| **Sample** | One (timestamp, value) point in a series |
| **Label** | A key=value dimension on a metric. Must come from a small, fixed set |
| **Cardinality** | How many series exist. It drives Prometheus's memory and CPU |
| **Bucket (`le`)** | A histogram counter meaning "observations ≤ this bound". Buckets are cumulative |
| **Quantile / percentile** | p95 = the value 95 % of observations are at or below |
| **Window (`[1m]`)** | How far back a range function like `rate` looks from each point |
| **Provisioning** | Grafana loading datasources and dashboards from files at startup |
| **Pull vs push** | Prometheus *pulls* metrics; in Part C, Filebeat *pushes* logs |
| **Request ID** | One ID per request, carried in the header and on every log line |
| **Route template** | `/devices/{device_id}` instead of `/devices/dev-017`: a bounded label value |
| **Document / mapping** | One stored log line / the field types Elasticsearch uses for it |
| **Data stream / backing index** | The name logs are appended to (`sensor-logs`) / the actual indices behind it (`.ds-sensor-logs-…-00000N`) |
| **Rollover / retention (ILM)** | Starting a new backing index / deleting old ones on a schedule |
| **KQL** | Kibana Query Language: `field : value and …` |
| **Registry** | Filebeat's record of how far it has read each file |

## What Parts D–E will teach

- **D: System design.** A diagram, plus "follow one metric, follow one log" end to end. You've done both halves: the metric in [A3](#a3-follow-one-reading-through-the-code) and [B2–B4](#b2-layer-1-the-raw-metrics-page), and the log in [C2–C6](#c2-layer-1-what-the-app-writes-stdout). You've also seen the failure modes: [B5](#b5-experiments-to-run-yourself) experiments 3–5 and [C7](#c7-experiments-to-run-yourself).
- **E: Experiments.**
  - **E1** is B5's experiment 1, done carefully: a baseline, a written prediction, a fault, a recovery, and each phase run for several scrapes. The fault now shows in *both* Grafana (latency percentiles) and Kibana (`event : "fault_injected"`, slow `http_request` lines).
  - **E2** deliberately adds `request_id` as a label to a test counter, and shows B3 exercise 3's lesson at scale: the series count explodes, and removing the label doesn't delete the history.
