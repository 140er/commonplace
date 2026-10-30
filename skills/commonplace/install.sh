#!/bin/sh
set -eu

case "${1-}" in
    "") dry_run=false ;;
    --dry-run) dry_run=true ;;
    *) printf 'Usage: %s [--dry-run]\n' "$0" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [--dry-run]\n' "$0" >&2
    exit 2
fi

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

cat <<'INSTRUCTIONS'
Set COMMONPLACE_TOKEN to your token. The default base URL is https://cmmn.dev.
Current tailnet address: https://edge1.tail6cee8c.ts.net.
Set COMMONPLACE_URL to override the default, then run:
export COMMONPLACE_URL="${COMMONPLACE_URL:-https://cmmn.dev}"
Claude Code:
claude mcp add --transport http commonplace "$COMMONPLACE_URL/mcp" --header "Authorization: Bearer $COMMONPLACE_TOKEN"
Codex config.toml (replace <COMMONPLACE_URL> with the base URL):
[mcp_servers.commonplace]
url = "<COMMONPLACE_URL>/mcp"
bearer_token_env_var = "COMMONPLACE_TOKEN"
INSTRUCTIONS
