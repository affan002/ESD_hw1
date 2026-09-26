#!/bin/sh
# One-shot setup for the log pipeline (Part C), run by the `logs-setup` Compose
# service (curlimages/curl). Idempotent: every step overwrites, so it's safe to
# re-run on each `docker compose up`.
#
#   1. Elasticsearch: ILM poll interval, retention policy, index template
#      (must exist before Filebeat writes, or the data stream would get a
#      guessed mapping and no retention).
#   2. Kibana: import the data view and saved searches, make the data view default.
set -eu

ES="${ES_URL:-http://elasticsearch:9200}"
KB="${KIBANA_URL:-http://kibana:5601}"
DIR="${CONFIG_DIR:-/monitoring}"

say() { echo "[logs-setup] $*"; }

say "waiting for Elasticsearch at $ES"
until curl -fs "$ES/_cluster/health?wait_for_status=yellow&timeout=5s" >/dev/null; do sleep 2; done

# ILM checks policies every 10 minutes by default; check every minute so the
# short demo retention (monitoring/elasticsearch/ilm-policy.json) is visible.
say "cluster setting indices.lifecycle.poll_interval=1m"
curl -fsS -X PUT "$ES/_cluster/settings" -H 'Content-Type: application/json' \
  -d '{"persistent":{"indices.lifecycle.poll_interval":"1m"}}'; echo

say "ILM policy sensor-logs-policy"
curl -fsS -X PUT "$ES/_ilm/policy/sensor-logs-policy" -H 'Content-Type: application/json' \
  --data-binary "@$DIR/elasticsearch/ilm-policy.json"; echo

say "index template sensor-logs"
curl -fsS -X PUT "$ES/_index_template/sensor-logs" -H 'Content-Type: application/json' \
  --data-binary "@$DIR/elasticsearch/index-template.json"; echo

say "waiting for Kibana at $KB"
until curl -fs "$KB/api/status" 2>/dev/null | grep -q '"overall":{"level":"available"'; do sleep 3; done

say "importing Kibana saved objects (data view + saved searches)"
curl -fsS -X POST "$KB/api/saved_objects/_import?overwrite=true" -H 'kbn-xsrf: true' \
  -F "file=@$DIR/kibana/saved-objects.ndjson"; echo

say "default data view = sensor-logs"
curl -fsS -X POST "$KB/api/data_views/default" -H 'kbn-xsrf: true' -H 'Content-Type: application/json' \
  -d '{"data_view_id":"sensor-logs","force":true}'; echo

say "done"
