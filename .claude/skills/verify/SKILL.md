---
name: verify
description: Start Commonplace for real on a throwaway data directory and check a change end-to-end (health, MCP save/search, API, optional browser journey) before committing or opening a PR
---

# Verify Commonplace

"Verified" means the gateway you built from this exact commit starts on an empty data directory, answers
`/healthz`, and a real client call exercising your change returns the expected words (verbatim memory
round trip, card/board API, sign-in page), with the server log free of tracebacks.

## Never

- Touch a running Commonplace service, its data directory, its database, backups, or any deploy. Always use
  a fresh `mktemp -d` data directory and a random loopback port. Unset every inherited `COMMONPLACE_*`.
- Use real users, real tokens, real social-login credentials, a real mail server, or any paid or external API.
  Throwaway accounts use the reserved `example.invalid` domain.
- Print a token. Keep it in a shell variable and only ever print its length.
- Run the full test suite, or kill a process by name or pattern.

## 1. Cheap checks

Run from the repository root (about 1 min after `uv sync --extra dev`, which is a few seconds when cached).

```bash
uv sync --extra dev
PY=$(git diff --name-only --diff-filter=d "$(git merge-base origin/develop HEAD)" -- '*.py')
[ -n "$PY" ] && uv run --no-sync ruff check $PY && uv run --no-sync ruff format --check $PY
uv run --no-sync pytest <only the test files your change touches or names> -q -p no:cacheprovider
```

Add the repo-wide guards only when your change touches docs, public routes or evals:
`tests/test_docs_obligations.py` (compares with `origin/develop`; `git fetch origin develop` first),
`tests/test_docs_fragments.py`, `tests/test_commonplace_public_surface.py`, `tests/test_eval_guards.py`.
A new route must be classified in `tests/test_commonplace_public_surface.py`.

## 2. Start it for real

```bash
unset $(env | grep -o '^COMMONPLACE_[A-Z_]*')
export D=$(mktemp -d /tmp/cp-verify.XXXXXX)
export COMMONPLACE_DATA=$D COMMONPLACE_ALLOWED_HOSTS=127.0.0.1
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
.venv/bin/commonplace init --admin-email verify@example.invalid
TOKEN=$(.venv/bin/commonplace token add verify-agent --workspace personal --client claude-code \
  --profile slim --read-wings shared,inbox --write-wing inbox --trust untrusted | grep -o 'cmmn_[A-Za-z0-9_-]*' | head -1)
echo "token length ${#TOKEN}"
.venv/bin/commonplace serve --host 127.0.0.1 --port $PORT >$D/serve.log 2>&1 &
PID=$!   # the server itself (the venv binary, not a wrapper), captured at spawn
for i in $(seq 1 60); do curl -fs http://127.0.0.1:$PORT/healthz && echo && break; sleep 0.5; done
```

Ready when `/healthz` prints `{"ok": true}` (1 to 10 s) and `$D/serve.log` ends with
`commonplace gateway on 127.0.0.1:<port>`. The memory worker starts on first use, so the first memory call
takes up to about 10 s on a cold machine.

## 3. Check the change

Drive it with the stock MCP wire format. Pick the checks that match your change.

```bash
call() { curl -sS -m 90 -X POST http://127.0.0.1:$PORT/mcp -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$1"; echo; }
# a. Verbatim round trip: the saved words must come back unchanged.
call '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"memory_save","arguments":{"text":"verify canary: the quick brown fox"}}}'
call '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_search","arguments":{"query":"quick brown fox"}}}'
# b. API route: authenticated gets JSON, anonymous gets 401 (swap the path for the route you changed).
curl -sS -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/api/boards
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:$PORT/api/boards
# c. Browser surface: the sign-in page must load.
curl -sSL -o /dev/null -w '%{http_code}\n' http://127.0.0.1:$PORT/app/login
```

Expected: `memory_save` answers `saved drawer_inbox_...`; `memory_search` returns the exact saved text under
`quoted data:`; (b) prints a 2xx then `401`; (c) prints `200`. Other tools: send `tools/list` to see what the
token's profile exposes. Call your changed tool or route with a sample input and compare to the expected
output; also try one failing input (bad token, missing field, other workspace) and confirm a clean refusal.

For sign-in, board/Deck, memory-page or MCP-flow changes, run the throwaway fresh-user journey instead of
hand-driving a browser (about 3 to 5 minutes; it starts its own empty instances and cannot target a
running service; needs the Playwright browser installed once, see `docs/commonplace-release-gate.md`):

```bash
uv pip install -r scripts/requirements-release.txt
timeout 10m uv run --no-sync python -m scripts.commonplace_release --output "$(mktemp -d)/report"
```

Exit 0 means every required check passed; read `report.md` and the screenshots in the output directory.

## 4. Evidence

Put in the PR body, from the exact commit being pushed (`git rev-parse HEAD`; re-run after any new commit):
the commands you ran, the output tail of each (`/healthz`, the tool replies, status codes, `report.md`
summary), the last lines of `$D/serve.log` (no traceback), and screenshot paths if the UI changed. Redact
tokens and never paste the `cmmn_` token.

## 5. Clean up

```bash
kill $PID; wait $PID 2>/dev/null          # only the PID captured above
ss -H -ltn "sport = :$PORT"               # must print nothing
rm -rf "$D"
unset TOKEN COMMONPLACE_DATA COMMONPLACE_ALLOWED_HOSTS
```

The release journey removes its own temp instances. Delete its output directory when done.

## If verify cannot run

Say so explicitly in the PR with the reason (missing dependency, no free port, browser not installed,
memory model unavailable offline) and list which steps did run. Never write "verified" without the evidence
above. A change that only touches docs or comments needs just step 1; say that in the PR.
