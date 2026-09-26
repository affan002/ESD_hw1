#!/usr/bin/env bash
# Experiment E2: cardinality explosion, bounded (100 ids) and fully cleaned up.
#
#   scripts/cardinality_demo.sh [results.json]
#
# 1. start the demo exporter WITH a request_id label (100 unique ids) and
#    point Prometheus at it (file_sd/cardinality-demo.json)
# 2. wait for 2 scrapes, count series
# 3. restart the exporter WITHOUT the label, wait for 2 scrapes, count again
# 4. clean up: stop the exporter, empty the targets file
# Needs the stack running (prometheus). Touches nothing in the sensor API.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-docs/results/e2_cardinality.json}"
TARGETS=monitoring/prometheus/file_sd/cardinality-demo.json
PROM=http://localhost:9090

q() { curl -s --get "$PROM/api/v1/query" --data-urlencode "query=$1" | jq -r '.data.result[0].value[1] // "0"'; }
snapshot() {  # label
  jq -n --arg stage "$1" --arg t "$(date -u +%FT%TZ)" \
    --arg count "$(q 'count(demo_requests_total)')" \
    --arg sum "$(q 'sum(demo_requests_total)')" \
    --arg history "$(q 'count(last_over_time(demo_requests_total[30m]))')" \
    --arg added "$(q 'max_over_time(scrape_series_added{job="cardinality-demo"}[20s])')" \
    --arg scraped "$(q 'scrape_samples_scraped{job="cardinality-demo"}')" \
    --arg head "$(q 'prometheus_tsdb_head_series')" \
    '{stage:$stage, t:$t, "count(demo_requests_total)":$count, "sum(demo_requests_total)":$sum,
      "count(last_over_time(demo_requests_total[30m]))":$history, "max scrape_series_added (20s)":$added,
      scrape_samples_scraped:$scraped, prometheus_tsdb_head_series:$head}'
}
wait_scrapes() { sleep 12; }  # 2 scrapes at 5 s, plus margin

cleanup() {
  docker compose --profile e2 rm -sf cardinality-demo >/dev/null 2>&1 || true
  echo '[]' > "$TARGETS"
}
trap cleanup EXIT

results=()
results+=("$(snapshot before)")

echo "== 1. exporter WITH request_id label (100 unique ids)"
echo '[{"targets":["cardinality-demo:8001"],"labels":{"demo":"e2"}}]' > "$TARGETS"
DEMO_MODE=label docker compose --profile e2 up -d --force-recreate cardinality-demo >/dev/null 2>&1
wait_scrapes
results+=("$(snapshot with_label)")
echo "   a few of the series Prometheus now stores:"
curl -s --get "$PROM/api/v1/query" --data-urlencode 'query=topk(3, demo_requests_total)' \
  | jq -r '.data.result[] | "   \(.metric.__name__){request_id=\"\(.metric.request_id)\"} \(.value[1])"'

echo "== 2. exporter restarted WITHOUT the label (same 100 increments, one series)"
DEMO_MODE=nolabel docker compose --profile e2 up -d --force-recreate cardinality-demo >/dev/null 2>&1
wait_scrapes
results+=("$(snapshot without_label)")

echo "== 3. cleanup (exporter stopped, targets file emptied)"
cleanup
wait_scrapes
results+=("$(snapshot after_cleanup)")

printf '%s\n' "${results[@]}" | jq -s . | tee "$OUT"
