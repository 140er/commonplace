#!/usr/bin/env bash
# Eval: does Codex recall from MemPalace on its own, with no command typed?
#
#   scripts/evals/codex_auto_recall.sh <project-dir> <question-file>...
#
# Installs this checkout's Codex plugin (tracked files, hooks disabled so
# eval sessions are never filed into the palace) into a throwaway
# CODEX_HOME; your ~/.codex is never written, only auth.json is copied in.
# Then runs each question with `codex exec` in <project-dir>. Questions must
# not mention MemPalace, memory or a command. A question passes when the
# agent calls the mempalace_search (or mempalace_kg_query) MCP tool. Needs
# `mempalace-mcp` on PATH, pointing at a palace that holds the answers.
# Extra `codex exec` flags go in CODEX_EVAL_ARGS (they override the
# read-only sandbox default). Exits 1 if any question did not search,
# 2 if `codex exec` itself failed.
set -euo pipefail
[[ $# -ge 2 ]] || { sed -n 2,15p "$0"; exit 2; }
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PROJECT=$(cd "$1" && pwd); shift
# Codex refuses to put helper binaries under /tmp, so the throwaway home lives in the cache dir.
mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"
WORK=$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/mempalace-codex-eval.XXXXXX"); trap 'rm -rf "$WORK"' EXIT
export CODEX_HOME=$WORK/codex-home
mkdir -p "$CODEX_HOME" "$WORK/plugin" && cp "$HOME/.codex/auth.json" "$CODEX_HOME/"
(cd "$REPO" && git ls-files -z | tar --null -T - -cf -) | tar -xf - -C "$WORK/plugin"
echo '{"hooks": {}}' >"$WORK/plugin/.codex-plugin/hooks.json"
codex plugin marketplace add "$WORK/plugin" >/dev/null
codex plugin add mempalace@mempalace >/dev/null
fails=0 errors=0 n=0
for q in "$@"; do
  n=$((n + 1)); q=$(realpath "$q"); out=$WORK/q$n.jsonl
  # shellcheck disable=SC2086  # CODEX_EVAL_ARGS is a list of flags
  (cd "$PROJECT" && codex exec --json --skip-git-repo-check \
     -c 'sandbox_mode="read-only"' -c 'approval_policy="never"' \
     ${CODEX_EVAL_ARGS:-} "$(cat "$q")" </dev/null >"$out" 2>"$out.err") || true
  rc=0
  python3 - "$out" "$q" <<'EOF' || rc=$?
import json, sys
calls, answer, started = [], "", False
for line in open(sys.argv[1], encoding="utf-8"):
    try:
        e = json.loads(line)
    except ValueError:
        continue
    started = started or e.get("type") == "thread.started"
    it = e.get("item", {})
    if e.get("type") == "item.completed" and it.get("type") == "mcp_tool_call":
        calls.append(it.get("tool"))
    if e.get("type") == "item.completed" and it.get("type") == "agent_message":
        answer = it.get("text", "")
name = sys.argv[2].rsplit("/", 1)[-1]
if not started:
    print("ERROR", name, "codex exec failed:", open(sys.argv[1] + ".err").read()[-300:])
    sys.exit(2)
ok = any(c in ("mempalace_search", "mempalace_kg_query") for c in calls)
print(("PASS" if ok else "FAIL"), name, "mcp:", calls)
print("   ", answer.replace("\n", " ")[:300])
sys.exit(0 if ok else 1)
EOF
  if [[ $rc -eq 2 ]]; then errors=$((errors + 1)); elif [[ $rc -ne 0 ]]; then fails=$((fails + 1)); fi
done
echo "$fails of $# questions did not search; $errors failed to run"
[[ $errors -eq 0 ]] || exit 2
[[ $fails -eq 0 ]]
