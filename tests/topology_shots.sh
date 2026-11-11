#!/usr/bin/env bash
# Product-proof screenshots of the Compute map from tests/topology_harness.html (real topology-map.js
# and real CSS over a scripted API with an invented topology):
#   bash tests/topology_shots.sh <output-dir>
# Writes <scene>-desktop.png (1280x800) and <scene>-phone.png (390x844 at 2x), walk-<n>-desktop.png for
# each stage of the scripted walk (drag, zoom and pan, status change, reload), and walk.html, the
# walk's final DOM. Fails unless that DOM says every check of the walk passed.
set -euo pipefail
out="${1:?usage: topology_shots.sh <output-dir>}"
mkdir -p "$out"
root="$(cd "$(dirname "$0")/.." && pwd)"
chrome="${CHROME:-google-chrome}"
profile="${TOPOLOGY_BROWSER_PROFILE:-$(mktemp -d)}"
port="${TOPOLOGY_HARNESS_PORT:-$((20000 + RANDOM % 20000))}"
python3 -m http.server "$port" --bind 127.0.0.1 --directory "$root" >/dev/null 2>&1 &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT
sleep 1
page="http://127.0.0.1:$port/tests/topology_harness.html"
run() { "$chrome" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --user-data-dir="$profile" --virtual-time-budget=8000 "$@" 2>/dev/null; }
shot() { # name query kind width height scale
  run --force-device-scale-factor="$6" --window-size="$4,$5" --screenshot="$out/$1-$3.png" "$page?$2" >/dev/null
}
for scene in map route empty; do
  shot "$scene" "scene=$scene" desktop 1280 800 1
  shot "$scene" "scene=$scene" phone 390 844 2
done
shot large "scene=large" desktop 1280 800 1
for steps in 0 1 2 3 4; do shot "walk-$steps" "scene=walk&steps=$steps" desktop 1280 800 1; done
run --window-size=1280,800 --dump-dom "$page?scene=walk" > "$out/walk.html"
ls -l "$out"
grep -q '<html lang="en" data-walk="pass">' "$out/walk.html" || { echo "walk failed:"; grep -o '<li class="[a-z]*">[^<]*' "$out/walk.html"; exit 1; }
echo "walk: pass"
