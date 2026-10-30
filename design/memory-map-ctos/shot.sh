#!/bin/sh
# Render the ctOS memory map to png/ with headless Chrome, plus look B vs ctOS side by side. Usage: ./shot.sh
set -e
cd "$(dirname "$0")"
shot() { google-chrome --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor="$4" \
  --virtual-time-budget="${6:-3000}" --window-size="$2" --screenshot="$5png/$3" "file://$PWD/${5:-}index.html$1" 2>/dev/null; }
shot ""               1440,900 map-1440.png 1
shot "?open=m26"      1440,900 memory-1440.png 1
shot "?hover=mila"    1440,900 hover-1440.png 1 "" 3900
shot "?open=sam&view=people" 1440,900 person-1440.png 1
shot ""               390,844  map-390.png 2
shot "?open=m1"       390,844  memory-390.png 2
# Same view in look B, then side by side.
B=../memory-map
google-chrome --headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=3000 --window-size=1440,900 \
  --screenshot="$PWD/png/.b.png" "file://$(cd $B && pwd)/index.html?open=m26" 2>/dev/null
convert png/.b.png \( png/memory-1440.png -background "#3fe0ee" -splice 4x0 \) +append -resize 2880x png/compare-b-vs-ctos-1440.png
rm -f png/.b.png
