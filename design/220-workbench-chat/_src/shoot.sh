#!/usr/bin/env bash
# Screenshot screens listed in _src/shots.txt (html out width height dpr).
# Usage: bash _src/shoot.sh [out-name...]   (no args = all). Optional: BROWSER_PROFILE=<dir> for the Chrome profile.
set -u
D="$(cd "$(dirname "$0")/.." && pwd)"
want=" $* "
while read -r html out w h dpr; do
  [ $# -gt 0 ] && [[ $want != *" $out "* ]] && continue
  timeout 60 google-chrome --headless=new --disable-gpu --no-first-run --no-default-browser-check \
    --user-data-dir="${BROWSER_PROFILE:-/tmp/wb-profile}" --hide-scrollbars \
    --window-size=$w,$h --force-device-scale-factor=$dpr \
    --screenshot="$D/shots/$out.png" "file://$D/screens/$html.html" 2>/dev/null >/dev/null \
    && echo "ok $out" || echo "FAIL $out"
done < "$D/_src/shots.txt"
