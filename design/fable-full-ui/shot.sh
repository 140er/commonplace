#!/usr/bin/env bash
# Screenshots of the design directions:
#   bash design/fable-full-ui/shot.sh <output-dir> [directions, default "a b c"] [screens]
# Writes <direction>-<screen>-desktop.png (1440x900) and <direction>-<screen>-phone.png (390x844 at 2x).
set -euo pipefail
out="${1:?usage: shot.sh <output-dir> [directions] [screens]}"
dirs="${2:-a b c}"
screens="${3:-chat bots project compute settings}"
mkdir -p "$out"
here="$(cd "$(dirname "$0")" && pwd)"
chrome="${CHROME:-google-chrome}"
profile="${HEAVY_BROWSER_PROFILE:-$(mktemp -d)}"
port="$((20000 + RANDOM % 20000))"
python3 -m http.server "$port" --bind 127.0.0.1 --directory "$here" >/dev/null 2>&1 &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT
sleep 1
shot() { # direction screen kind width height scale
  "$chrome" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --user-data-dir="$profile" \
    --force-device-scale-factor="$6" --window-size="$4,$5" --virtual-time-budget=3000 \
    --screenshot="$out/$1-$2-$3.png" "http://127.0.0.1:$port/index.html?ui=$1#/$2" >/dev/null 2>&1
}
for d in $dirs; do
  for s in $screens; do
    shot "$d" "$s" desktop 1440 900 1
    shot "$d" "$s" phone 390 844 2
  done
done
ls "$out"
