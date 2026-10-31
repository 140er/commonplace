#!/bin/sh
# Render the mix and its comparison with the tracked base. Usage: ./shot.sh
set -e
cd "$(dirname "$0")"
render() { google-chrome --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor="$4" \
  --virtual-time-budget="${5:-3000}" --window-size="$2" --screenshot="$PWD/png/$3" "file://$1" 2>/dev/null; }
shot() { render "$PWD/index.html$1" "$2" "$3" "$4" "$5"; }
shot ""                      1440,900 map-1440.png 1
shot "?open=m26"             1440,900 memory-1440.png 1
shot "?hover=editor"           1440,900 hover-1440.png 1 3900
shot "?view=people&open=artist" 1440,900 person-1440.png 1
shot ""                      390,844  map-390.png 2
shot "?open=m1"              390,844  memory-390.png 2
render "$(cd ../memory-map && pwd)/index.html?open=m26" 1440,900 .base.png 1
convert png/.base.png \( png/memory-1440.png -background "#f7941d" -splice 4x0 \) +append png/compare-base-mix-1440.png
rm -f png/.base.png
