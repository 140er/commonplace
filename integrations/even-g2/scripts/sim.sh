#!/usr/bin/env bash
# Drive the Even Hub simulator through its automation API.
#
#   scripts/sim.sh start <plugin-url>     launch headless (xvfb-run) on port ${SIM_PORT:-9898}
#   scripts/sim.sh press up|down|click|double_click|long_press
#   scripts/sim.sh shot <file.png>        save the glasses framebuffer (576x288)
#   scripts/sim.sh console                print captured console output
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
api=http://127.0.0.1:${SIM_PORT:-9898}
case "${1:-}" in
  start)
    sim="$here/../plugin/node_modules/.bin/evenhub-simulator"
    nohup xvfb-run -a -s "-screen 0 1400x1000x24" "$sim" --automation-port "${SIM_PORT:-9898}" \
      --no-glow "$2" >"${SIM_LOG:-/tmp/evenhub-sim.log}" 2>&1 &
    for _ in $(seq 1 60); do curl -fs "$api/api/ping" >/dev/null && exit 0; sleep 0.5; done
    echo "simulator did not start; see ${SIM_LOG:-/tmp/evenhub-sim.log}" >&2; exit 1 ;;
  press)
    curl -fs -X POST -H 'Content-Type: application/json' -d "{\"action\":\"$2\"}" "$api/api/input" >/dev/null
    sleep "${SIM_SETTLE:-1.2}" ;;
  shot) curl -fs -o "$2" "$api/api/screenshot/glasses" ;;
  console) curl -fs "$api/api/console" ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
