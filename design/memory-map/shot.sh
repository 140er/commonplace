#!/bin/sh
# Render the memory map to png/ with headless Chrome. Usage: ./shot.sh
set -e
cd "$(dirname "$0")"
shot() { google-chrome --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor="$4" \
  --virtual-time-budget=3000 --window-size="$2" --screenshot="png/$3" "file://$PWD/index.html$1" 2>/dev/null; }
shot ""                       1440,900 map-1440.png 1
shot "?hover=editor"              1440,900 hover-1440.png 1
shot "?open=m26"                1440,900 memory-1440.png 1
shot "?view=themes&hover=m14"   1440,900 themes-1440.png 1
shot "?view=people&open=artist"    1440,900 people-1440.png 1
shot "?q=poster"                   1440,900 filter-1440.png 1
shot "?week=44"                 1440,900 week-1440.png 1
shot ""                         390,844  map-390.png 2
shot "?open=m1"                 390,844  memory-390.png 2
