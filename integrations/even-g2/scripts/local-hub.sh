#!/usr/bin/env bash
# Start a THROWAWAY local Commonplace hub for the G2 plugin, with seeded data.
#
#   scripts/local-hub.sh [data-dir] [port]     (defaults: a new temp dir, 8796)
#
# Run from a Commonplace checkout with its Python environment (uv sync). Creates:
#   <data>/g2.token     the glasses connection (bridge reads it; never print it)
#   <data>/agent.token  a test agent that files asks
# and grants the glasses connection "answer Needs-me questions" through a local
# browser session, the same consent the owner gives in the app.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(git -C "$here" rev-parse --show-toplevel)
data=${1:-$(mktemp -d /tmp/cmmn-g2-hub.XXXXXX)}
port=${2:-8796}
hub=http://127.0.0.1:$port
py=${PYTHON:-$repo/.venv/bin/python}

if curl -fs -o /dev/null "$hub/healthz" 2>/dev/null || ss -ltn | grep -q ":$port "; then
  echo "port $port is taken; pass another: $0 <data-dir> <port>" >&2; exit 1
fi
for v in $(env | grep -o '^COMMONPLACE_[A-Z_]*' || true); do unset "$v"; done
mkdir -p -m 700 "$data/sock"
export COMMONPLACE_DATA=$data/hub COMMONPLACE_ADMIN_SOCKET=$data/sock/gw.sock \
  COMMONPLACE_ALLOWED_HOSTS=127.0.0.1 COMMONPLACE_RATE_LIMITS=off COMMONPLACE_VOICE=off \
  COMMONPLACE_APP_URL=$hub COMMONPLACE_BASE_URL=$hub
cd "$repo"
umask 077
cmmn() { "$py" -m commonplace "$@"; }

cmmn init --admin-email owner@example.test --workspace personal >/dev/null
# The glasses: read everything incl. the event log, comment; decide is granted below.
cmmn token add g2 --workspace personal --profile full \
  --scopes memory:read,memory:write,events:read,private:read \
  --read-wings '*' --write-wings '*' >"$data/g2.token" 2>/dev/null
cmmn token add seed-agent --workspace personal --profile full --trust trusted \
  --read-wings '*' --write-wings '*' --write-wing general >"$data/agent.token" 2>/dev/null

nohup "$py" -m commonplace serve --host 127.0.0.1 --port "$port" >"$data/hub.log" 2>&1 &
echo $! >"$data/hub.pid"
for _ in $(seq 1 60); do curl -fs "$hub/healthz" >/dev/null && break; sleep 0.5; done
curl -fs "$hub/healthz" >/dev/null || { echo "hub did not start; see $data/hub.log" >&2; exit 1; }

# Owner consent, as in the app: sign in locally, then let "g2" answer Needs-me questions.
link=$(cmmn login-link owner@example.test --workspace personal --base-url "$hub")
curl -fs -c "$data/jar" -b "$data/jar" -L -o /dev/null "$link"
curl -fs -b "$data/jar" -X PATCH -H 'Content-Type: application/json' -H "Origin: $hub" \
  -d '{"decide": true}' "$hub/api/connections/g2" >/dev/null

"$py" -I "$here/seed_local_hub.py" "$hub" "$data/agent.token" base >/dev/null
echo "hub $hub  data $data  (stop: kill \$(cat $data/hub.pid))"
