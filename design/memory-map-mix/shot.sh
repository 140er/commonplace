#!/bin/sh
# Render the mix to png/ with headless Chrome. Usage: ./shot.sh [path to the ctOS preview folder]
# With the ctOS folder (branch design/memory-map-ctos), also renders look B, ctOS and mix side by side.
set -e
cd "$(dirname "$0")"
render() { google-chrome --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor="$4" \
  --virtual-time-budget="${5:-3000}" --window-size="$2" --screenshot="$PWD/png/$3" "file://$1" 2>/dev/null; }
shot() { render "$PWD/index.html$1" "$2" "$3" "$4" "$5"; }
shot ""                      1440,900 map-1440.png 1
shot "?open=m26"             1440,900 memory-1440.png 1
shot "?hover=mila"           1440,900 hover-1440.png 1 3900
shot "?view=people&open=sam" 1440,900 person-1440.png 1
shot ""                      390,844  map-390.png 2
shot "?open=m1"              390,844  memory-390.png 2
if [ -n "$1" ]; then
  render "$(cd ../memory-map && pwd)/index.html?open=m26" 1440,900 .b.png 1
  render "$(cd "$1" && pwd)/index.html?open=m26" 1440,900 .c.png 1
  convert png/.b.png \( png/.c.png -background "#f7941d" -splice 4x0 \) \( png/memory-1440.png -background "#f7941d" -splice 4x0 \) +append png/compare-b-ctos-mix-1440.png
  rm -f png/.b.png png/.c.png
fi
