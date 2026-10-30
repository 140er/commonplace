#!/bin/sh
# Render the v2 mockups to png/after-*.png with headless Chrome. Usage: ./shot.sh
set -e
cd "$(dirname "$0")"
python3 build.py
shot() { google-chrome --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor="$4" \
  --virtual-time-budget=2000 --window-size="$2" --screenshot="png/after-$3" "file://$PWD/$1" 2>/dev/null; }
shot decide.html 1440,900 decide-1440.png 1
shot board.html  1440,900 board-1440.png 1
shot card.html   1440,900 card-1440.png 1
shot mobile.html 390,844  decide-mobile-390.png 2
