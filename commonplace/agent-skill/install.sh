#!/bin/sh
set -eu

dry_run=false
rules=false
for argument in "$@"; do
    case "$argument" in
        --dry-run) dry_run=true ;;
        --rules) rules=true ;;
        --help|-h) printf 'Usage: %s [--dry-run] [--rules]\n' "$0"; exit 0 ;;
        *) printf 'Usage: %s [--dry-run] [--rules]\n' "$0" >&2; exit 2 ;;
    esac
done

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for root in "$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.agents/skills"; do
    destination="$root/commonplace"
    if [ "$dry_run" = true ]; then
        printf 'Would copy %s to %s/SKILL.md\n' "$source_dir/SKILL.md" "$destination"
    else
        mkdir -p "$destination"
        cp "$source_dir/SKILL.md" "$destination/SKILL.md"
        printf 'Installed %s/SKILL.md\n' "$destination"
    fi
done

if [ "$rules" = true ]; then
    destination="$HOME/.commonplace/bin/rules_sync.py"
    if [ "$dry_run" = true ]; then
        printf 'Would copy %s to %s\n' "$source_dir/rules_sync.py" "$destination"
    else
        # The same realpath checks apply to installation of the executable.
        python3 - "$source_dir/rules_sync.py" "$destination" <<'PY'
import importlib.util
import sys

source, destination = sys.argv[1:]
spec = importlib.util.spec_from_file_location("rules_sync", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
path = module.safe_path(module.Path(destination))
module.atomic_write(path, module.Path(source).read_bytes(), lambda: None, None)
PY
        printf 'Installed %s (run with python3)\n' "$destination"
    fi
fi

cat <<'INSTRUCTIONS'
Set COMMONPLACE_TOKEN to your token. The default base URL is https://cmmn.dev.
Set COMMONPLACE_URL to override the default, then run:
export COMMONPLACE_URL="${COMMONPLACE_URL:-https://cmmn.dev}"
Claude Code:
claude mcp add --transport http commonplace "$COMMONPLACE_URL/mcp" --header "Authorization: Bearer $COMMONPLACE_TOKEN"
Codex config.toml (replace <COMMONPLACE_URL> with the base URL):
[mcp_servers.commonplace]
url = "<COMMONPLACE_URL>/mcp"
bearer_token_env_var = "COMMONPLACE_TOKEN"
INSTRUCTIONS
