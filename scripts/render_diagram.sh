#!/usr/bin/env bash
# Render docs/diagrams/architecture.mmd to .svg and .png with headless Chrome
# and Mermaid from the jsDelivr CDN (no Node.js needed; needs internet once).
#
#   scripts/render_diagram.sh [file.mmd]
set -euo pipefail

SRC="${1:-docs/diagrams/architecture.mmd}"
BASE="${SRC%.mmd}"
PROFILE="$(mktemp -d)"
PAGE="$(mktemp --suffix=.html)"
trap 'rm -rf "$PROFILE" "$PAGE"' EXIT

python3 - "$SRC" "$PAGE" <<'EOF'
import html, sys
src, page = sys.argv[1], sys.argv[2]
body = html.escape(open(src).read())
open(page, "w").write(f"""<!doctype html><html><head><meta charset="utf-8">
<style>body{{margin:0;background:#fff;font-family:sans-serif}} .mermaid{{padding:16px}}</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true, theme:"default", securityLevel:"loose",
  flowchart:{{htmlLabels:true, curve:"basis", nodeSpacing:40, rankSpacing:70}}}});</script>
</head><body><pre class="mermaid">{body}</pre></body></html>""")
EOF

chrome() { timeout 90 google-chrome --headless=new --disable-gpu --hide-scrollbars --no-first-run \
  --no-default-browser-check --user-data-dir="$PROFILE" --virtual-time-budget=15000 "$@" 2>/dev/null; }

# SVG: take the rendered <svg> element out of the final DOM.
chrome --dump-dom "file://$PAGE" | python3 -c '
import re, sys
dom = sys.stdin.read()
m = re.search(r"<svg[\s\S]*?</svg>", dom)
if not m: sys.exit("mermaid did not render (no <svg> in the DOM)")
svg = m.group(0)
if "xmlns=" not in svg[:200]:
    svg = svg.replace("<svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"", 1)
open(sys.argv[1], "w").write(svg)
' "$BASE.svg"
echo "wrote $BASE.svg"

# PNG: screenshot of the page, trimmed to the diagram.
chrome --window-size=1800,1000 --force-device-scale-factor=2 --screenshot="$BASE.png" "file://$PAGE" >/dev/null
python3 - "$BASE.png" <<'EOF' || true
import sys
from PIL import Image, ImageChops
p = sys.argv[1]; im = Image.open(p).convert("RGB")
bb = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
if bb: im.crop((max(0, bb[0]-12), max(0, bb[1]-12), min(im.width, bb[2]+12), min(im.height, bb[3]+12))).save(p, optimize=True)
EOF
echo "wrote $BASE.png"
