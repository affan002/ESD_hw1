# IoT Sensor Ingestion Service

This service receives readings from simulated temperature, humidity and battery sensors. It validates each reading, stores it and flags anomalies. It's the base application for an observability assignment (Prometheus/Grafana metrics and Filebeat → Elasticsearch → Kibana logs).

> Status: Part A (the API, the dashboard and the simulator) and Part B (Prometheus, Grafana and Node Exporter metrics) are complete. Logging (Filebeat/Elasticsearch/Kibana) hasn't been added yet.

## Prerequisites

- Docker Engine with the Compose v2 plugin (`docker compose version`).
- Your user must be able to talk to Docker without `sudo`. If you get `permission denied ... docker.sock`, run `sudo usermod -aG docker $USER`, then log out and back in.
- Python 3.12. This is only needed to run the tests locally.
- `curl`. `jq` is optional but useful.
- Free ports on the host: **8000** (API and dashboard), **9090** (Prometheus), **3000** (Grafana) and **9100** (Node Exporter).
- About 400 MB of free RAM for the whole stack.

## Start

Build the image and start everything: the API (which also serves the dashboard), the 20-device simulator, Prometheus, Node Exporter and Grafana:

```bash
docker compose up -d --build
```

All five services should be up (`api`, `simulator`, `prometheus`, `node-exporter`, `grafana`), with `api` marked `(healthy)`:

```bash
docker compose ps
```

```bash
curl -s localhost:8000/health
```

The health check should return `{"status":"ok","db":"ok"}`.

- **Dashboard:** http://localhost:8000
- **Interactive API docs:** http://localhost:8000/docs
- **Grafana:** http://localhost:3000 (no login needed to view)
- **Prometheus:** http://localhost:9090

After about 3 minutes, the device list shows the misbehaving devices:

```bash
curl -s localhost:8000/devices | jq -c '.devices[] | {device_id, status}'
```

dev-017 and dev-018 show `anomaly`. dev-020 turns `stale` for about 30 s of every 2 minutes.

To start only the API, with no simulated traffic or monitoring, run `docker compose up -d --build api`.

Readings are stored in SQLite at `/data/sensors.db`, inside the `sensor-data` Docker volume. The data survives `docker compose down` and restarts; only `docker compose down -v` deletes it. When the API runs without Docker, the database is `./data/sensors.db`.

## Use

### Dashboard

Open http://localhost:8000. The page refreshes every 5 s, and **Pause** stops it. It shows:

- **Summary cards:** device counts by status, and the total number of readings stored.
- **Devices table:** each device's status, latest values (the flagged ones highlighted), battery level, when it was last seen and its counters. You can filter it by status. Click a row to open that device's detail panel.
- **Device detail:** first and last seen, the latest anomaly reasons, and charts of the last 50 temperature, humidity and battery readings. Dots on a chart mark readings flagged for that value.
- **Recent anomalies:** the newest anomalies across the whole fleet. Click a device ID to open it.
- **Send a test reading:** presets for Normal, Too hot, Low battery and Impossible humidity. It shows whether the reading was stored, stored as an anomaly, or rejected with the reason, plus the request ID so you can find the request in the logs.
- **Fault injection:** turn on injected delay or errors for `POST /readings`. A yellow banner appears across the top while any fault is active, and it has a button to turn the fault off.

The dashboard is plain HTML, CSS and JavaScript in `app/static/`, served by the API itself. There's no build step. It only calls the public JSON API, and its requests carry `X-Request-ID: ui-…`.

### Dashboards (Grafana)

Open http://localhost:3000. No login is needed to view. It opens on the **Sensor Ingestion Service** dashboard, and the **Host (Node Exporter)** dashboard is under *Dashboards → Sensor Ingestion*. Both refresh automatically and show the last 15 minutes by default. Hover over a panel's ⓘ icon to see what it shows.

To go straight to a dashboard:
- App: http://localhost:3000/d/sensor-service
- Host: http://localhost:3000/d/node-host
- Add `?kiosk` to either URL for a full-screen view.

The datasource and dashboards are **provisioned from files** in `monitoring/grafana/`, so they come back identically after `docker compose down -v`. Changes made in the UI can't be saved. To change a dashboard, edit its JSON in `monitoring/grafana/dashboards/`, and Grafana reloads it within 30 s. To make edits in the UI and export them, log in as `admin` / `admin` (Grafana asks you to change the password on first login).

To regenerate the report's screenshots (headless Chrome over a fixed time window; the stack must be running), capture the last 30 minutes, or pass an explicit end time:

```bash
scripts/capture_screenshots.sh 30
```

The images are written to `docs/screenshots/`.

### Metrics (Prometheus)

`docker compose up -d --build` also starts Prometheus, which scrapes the API, Node Exporter and itself every 5 s.

- **Raw metrics from the app:** `curl -s localhost:8000/metrics | grep '^sensor_'`
- **Prometheus UI:** http://localhost:9090. Use *Query* to run PromQL, and *Status → Target health* to see scrape targets. All three targets (`sensor-api`, `node`, `prometheus`) should be `UP`.

Some queries to try in the UI:

| What | PromQL |
|---|---|
| Readings per second, by outcome | `sum by (outcome) (rate(sensor_readings_total[1m]))` |
| p95 latency of `POST /readings` (seconds) | `histogram_quantile(0.95, sum by (le) (rate(sensor_http_request_duration_seconds_bucket{route="/readings"}[1m])))` |
| Devices by status | `sensor_devices` |
| Anomalies per second, by reason | `sum by (reason) (rate(sensor_anomalies_total[1m]))` |

Or query from the terminal:

```bash
curl -s --get localhost:9090/api/v1/query --data-urlencode 'query=sensor_devices' | jq -c '.data.result[] | {status: .metric.status, value: .value[1]}'
```

**Host metrics:** the `node-exporter` service reports CPU, memory, disk and network for the machine running Docker. It uses the host network, so it's at http://localhost:9100/metrics. Try `node_load1`, or `node_memory_MemAvailable_bytes / 1024^3`, in the Prometheus UI.

Prometheus keeps its data in the `prometheus-data` volume for 7 days. `docker compose down -v` deletes it along with the app's data.

### Run the device simulator

`docker compose up -d --build` starts the API and the simulator together. The simulator runs 20 devices, each sending one reading every 5 s. The last four misbehave on purpose:

| Device | Behaviour |
|---|---|
| dev-017 | overheats |
| dev-018 | battery drains |
| dev-019 | sends invalid data |
| dev-020 | drops offline for 60 s at a time |

To follow the simulator's output, with a summary every 30 s:

```bash
docker compose logs -f simulator --no-log-prefix
```

To run the simulator locally against a running API, faster and for 30 s only:

```bash
.venv/bin/python -m simulator.simulate --interval 1 --duration 30
```

The options are `--api-url` (default `http://localhost:8000`), `--devices` (20), `--interval` in seconds per device (5), `--seed` (42) and `--duration` in seconds, where 0 means run forever. They can also be set with the environment variables `API_URL`, `SIM_DEVICES`, `SIM_INTERVAL_S`, `SIM_SEED` and `SIM_DURATION_S`.

### Send a reading

The device timestamp must include a timezone and can't be more than 60 s in the future.

```bash
curl -s -XPOST localhost:8000/readings -H 'content-type: application/json' -d "{\"device_id\":\"dev-001\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"temperature_c\":22.5,\"humidity_pct\":45.0,\"battery_pct\":88.0}"
```

- A normal reading returns `201` with `"is_anomaly": false`.
- A value outside the expected range (for example `"temperature_c": 40`) returns `201` with `"is_anomaly": true` and `"anomaly_reasons": ["temperature_high"]`.
- Physically impossible or malformed data (for example `"humidity_pct": 150`, a missing field, or a `device_id` that isn't `dev-NNN`) returns `422` and isn't stored.

### Query devices and anomalies

List all devices with their status (`ok`, `anomaly`, or `stale` after 30 s of silence):

```bash
curl -s localhost:8000/devices | jq
```

List only the devices with a given status:

```bash
curl -s 'localhost:8000/devices?status=anomaly' | jq
```

Show one device, including its latest reading and counters:

```bash
curl -s localhost:8000/devices/dev-001 | jq
```

Show a device's history, newest first. `limit` can be 1–500, and `anomalies_only` can be added:

```bash
curl -s 'localhost:8000/devices/dev-001/readings?limit=10' | jq
```

Show the latest anomalies across the whole fleet:

```bash
curl -s 'localhost:8000/anomalies?limit=10' | jq
```

### Inject and undo a fault

Fault injection is used for the experiments. It affects only `POST /readings`, is off by default, and is reset by a restart.

Add 500 ms of latency to every 5th reading:

```bash
curl -s -XPUT localhost:8000/admin/faults -H 'content-type: application/json' -d '{"delay_ms":500,"delay_every_n":5,"error_every_n":0}'
```

Make every 10th reading fail with a 500:

```bash
curl -s -XPUT localhost:8000/admin/faults -H 'content-type: application/json' -d '{"delay_ms":0,"delay_every_n":1,"error_every_n":10}'
```

Check the current setting:

```bash
curl -s localhost:8000/admin/faults
```

Undo the fault, turning everything off:

```bash
curl -s -XDELETE localhost:8000/admin/faults
```

### View logs

The API writes one JSON object per line. To follow the logs:

```bash
docker compose logs -f api --no-log-prefix
```

To show only the access-log lines:

```bash
docker compose logs api --no-log-prefix | jq -c 'select(.event=="http_request")'
```

Every response carries an `X-Request-ID` header. You can send your own with `-H 'X-Request-ID: my-id'`, and it appears as `request_id` on every log line for that request.

### Run without Docker

To run the API without Docker:

```bash
.venv/bin/python -m app
```

## Test

### Automated tests

The unit and API tests use a temporary SQLite file and don't need Docker.

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements-dev.txt
```

```bash
.venv/bin/pytest -q
```

### Manual check (about 5 minutes)

To understand *why* each check behaves the way it does, see [WALKTHROUGH.md](WALKTHROUGH.md). It's a learning guide that follows a reading through the code line by line, gives hands-on exercises (predict first, then run), explains how to read every Grafana panel, and maps each section of the report to how you can check it yourself.

Run these steps after `docker compose up -d --build`. Each step says what you should see.

1. **Health.** `curl -s localhost:8000/health` returns `{"status":"ok","db":"ok"}`, and `docker compose ps` shows `api` as `(healthy)`.
2. **Dashboard loads.** Open http://localhost:8000. The top bar says **API healthy**, and the table fills with devices dev-001 to dev-020.
3. **Validation, from the dashboard's "Send a test reading" panel:**
   - **Normal** gives *201 Stored as normal*.
   - **Too hot** gives *201 Stored as anomaly: temperature_high*.
   - **Impossible humidity** gives *422 Rejected, not stored* with `humidity_pct: Input should be less than or equal to 100`.
4. **The faulty devices show up.** Wait about 3 minutes after startup:
   - dev-017 (overheat) and dev-018 (low battery) show **anomaly**, and they appear in *Recent anomalies* as `temperature_high` and `battery_low`.
   - dev-020 turns **stale** for about 30 s of every 2 minutes. The **Stale** filter chip shows it.
   - Click dev-017. Its temperature chart climbs, with dots once it passes 35 °C.
5. **The invalid device is rejected.** The simulator's summary shows `status_422 > 0` (from dev-019) and `status_5xx == 0`:
   ```bash
   docker compose logs simulator --no-log-prefix | jq -c 'select(.event=="sim_summary") | .stats' | tail -1
   ```
6. **Logs are structured.** This prints `OK` only if every log line is valid JSON:
   ```bash
   docker compose logs api --no-log-prefix | jq -c . > /dev/null && echo OK
   ```
   Searching for a request ID shown by the dashboard (`ui-…`) returns that request's log lines:
   ```bash
   docker compose logs api --no-log-prefix | jq -c 'select(.request_id=="ui-XXXXXXXXXXXX")'
   ```
7. **Fault injection.** In the dashboard's *Fault injection* panel, set Delay to 500 and click **Apply**. The yellow banner appears, and the logged `duration_ms` for `/readings` rises to about 500:
   ```bash
   docker compose logs api --no-log-prefix --since 15s | jq -r 'select(.path=="/readings") | .duration_ms'
   ```
   Click **Turn off** in the banner. The banner disappears and the latency drops back to a few ms.
8. **Data persists.** Note dev-001's reading count, run `docker compose restart api`, and check again. The count keeps growing from where it was instead of starting from zero.
9. **Prometheus scrapes all targets.** This lists `node up`, `prometheus up` and `sensor-api up`:
   ```bash
   curl -s localhost:9090/api/v1/targets | jq -r '.data.activeTargets[] | "\(.labels.job) \(.health)"'
   ```
10. **Grafana dashboards have data.** Open http://localhost:3000/d/sensor-service. After about a minute (the rate window), *Stored readings / s* shows about 3.9, *p95 latency* about 20 ms, and *Devices by status* matches the API. Then open http://localhost:3000/d/node-host. *Hostname* shows the machine running Docker, and the CPU, memory, disk and network panels have data.

## Clean up

Stop the containers. Stored data is kept in the volumes: `sensor-data` (readings), `prometheus-data` (metrics history) and `grafana-data` (Grafana's internal state):

```bash
docker compose down
```

Stop the containers **and delete all stored data**: readings, metrics history and Grafana state. The dashboards themselves are files in `monitoring/grafana/dashboards/`, so they come back on the next start. This can't be undone:

```bash
docker compose down -v
```

Remove the built image:

```bash
docker image rm sensor-api:dev
```

Remove the downloaded monitoring images:

```bash
docker image rm prom/prometheus:v3.15.0 prom/node-exporter:v1.12.1 grafana/grafana:13.2.2
```
