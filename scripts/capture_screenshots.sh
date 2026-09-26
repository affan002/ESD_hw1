#!/usr/bin/env bash
# Capture the report screenshots with headless Chrome (no clicking, reproducible).
#
#   scripts/capture_screenshots.sh [minutes] [end]
#     minutes  length of the time window (default 30)
#     end      window end, anything `date -d` understands (default: 20 s ago)
#
# A fixed absolute window is used instead of "now": headless Chrome's
# --virtual-time-budget runs the page clock ahead of real time, which makes
# the right edge of rate() graphs dip. Needs the stack running and google-chrome.
set -euo pipefail

MINUTES="${1:-30}"
END="${2:-20 seconds ago}"
OUT="${OUT:-docs/screenshots}"         # override with OUT=dir to capture elsewhere
PROFILE="$(mktemp -d)"                     # throwaway profile; never touches yours
trap 'rm -rf "$PROFILE"' EXIT

TO=$(( $(date -d "$END" +%s) * 1000 ))
FROM=$(( TO - MINUTES * 60 * 1000 ))
G="orgId=1&theme=light&from=$FROM&to=$TO"
mkdir -p "$OUT"
echo "window: $(date -d "@$((FROM / 1000))" '+%F %T %Z') -> $(date -d "@$((TO / 1000))" '+%T %Z')"

shot() {  # url file width,height
  timeout 90 google-chrome --headless=new --disable-gpu --hide-scrollbars --no-first-run \
    --no-default-browser-check --user-data-dir="$PROFILE" --window-size="$3" \
    --virtual-time-budget=15000 --screenshot="$OUT/$2" "$1" >/dev/null 2>&1
  echo "  $OUT/$2"
}

shot "http://localhost:3000/d/sensor-service?$G&kiosk"            b_app_dashboard.png        1600,1880
shot "http://localhost:3000/d-solo/sensor-service?$G&panelId=10"  b_latency_percentiles.png  1200,420
shot "http://localhost:3000/d-solo/sensor-service?$G&panelId=15"  b_readings_by_outcome.png  1200,420
shot "http://localhost:3000/d-solo/sensor-service?$G&panelId=16"  b_devices_by_status.png    1200,420
shot "http://localhost:3000/d-solo/sensor-service?$G&panelId=17"  b_anomalies_by_reason.png  1200,420
shot "http://localhost:3000/d-solo/sensor-service?$G&panelId=18"  b_validation_errors.png    1200,420
shot "http://localhost:3000/d/node-host?$G&kiosk"                 b_node_dashboard.png       1600,1300
shot "http://localhost:9090/targets"                              b_prometheus_targets.png   1400,650
shot "http://localhost:8000/"                                     a_operator_dashboard.png   1400,1500

# Trim the empty background below single panels and pages (needs Pillow).
python3 - "$OUT" <<'EOF' || echo "  (Pillow not installed: skipped cropping)"
import sys
from PIL import Image, ImageChops
out = sys.argv[1]
for name in ["b_latency_percentiles", "b_readings_by_outcome", "b_devices_by_status", "b_anomalies_by_reason",
             "b_validation_errors", "b_prometheus_targets", "a_operator_dashboard"]:
    path = f"{out}/{name}.png"
    im = Image.open(path).convert("RGB")
    bbox = ImageChops.difference(im, Image.new("RGB", im.size, im.getpixel((im.width - 2, im.height - 2)))).getbbox()
    if bbox:
        im.crop((0, 0, im.width, min(im.height, bbox[3] + 8))).save(path, optimize=True)
EOF
