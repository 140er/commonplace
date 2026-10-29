#!/usr/bin/env bash
# Eval: does Codex recall from MemPalace on its own, with no command typed?
#
#   scripts/evals/codex_auto_recall.sh <project-dir> <question-file>...
#
# Installs this checkout's Codex plugin into a throwaway CODEX_HOME (your
# ~/.codex is never written; only auth.json is copied in), then runs each
# question with `codex exec` in <project-dir>. Questions must not mention
# MemPalace, memory or a command. A question passes when the agent calls
# the mempalace_search (or mempalace_kg_query) MCP tool. Needs
# `mempalace-mcp` on PATH, pointing at a palace that holds the answers.
# Exits 1 if any question did not search.
set -euo pipefail
[[ $# -ge 2 ]] || { sed -n 2,12p "$0"; exit 2; }
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PROJECT=$(cd "$1" && pwd); shift
# Codex refuses to put helper binaries under /tmp, so the throwaway home lives in the cache dir.
mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"
WORK=$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/mempalace-codex-eval.XXXXXX"); trap 'rm -rf "$WORK"' EXIT
export CODEX_HOME=$WORK/codex-home ASK_CHIEF_DISABLE=1
mkdir -p "$CODEX_HOME" && cp "$HOME/.codex/auth.json" "$CODEX_HOME/"
codex plugin marketplace add "$REPO" >/dev/null
codex plugin add mempalace@mempalace >/dev/null
fails=0
for q in "$@"; do
  q=$(realpath "$q")
  out=$WORK/$(basename "$q").jsonl
  (cd "$PROJECT" && codex exec --json --skip-git-repo-check \
     -c 'sandbox_mode="danger-full-access"' -c 'approval_policy="never"' \
     ${CODEX_EVAL_ARGS:-} "$(cat "$q")" </dev/null >"$out" 2>"$out.err") || true
  python3 - "$out" "$q" <<'EOF' || fails=$((fails + 1))
import json, sys
calls, answer = [], ""
for line in open(sys.argv[1]):
    try:
        e = json.loads(line)
    except ValueError:
        continue
    it = e.get("item", {})
    if e.get("type") == "item.completed" and it.get("type") == "mcp_tool_call":
        calls.append(it.get("tool"))
    if e.get("type") == "item.completed" and it.get("type") == "agent_message":
        answer = it.get("text", "")
ok = any(c in ("mempalace_search", "mempalace_kg_query") for c in calls)
print(("PASS" if ok else "FAIL"), sys.argv[2].rsplit("/", 1)[-1], "mcp:", calls)
print("   ", answer.replace("\n", " ")[:300] or open(sys.argv[1] + ".err").read()[-300:])
sys.exit(0 if ok else 1)
EOF
done
echo "$fails of $# questions did not search"
[[ $fails -eq 0 ]]
