#!/bin/sh
set -eu

dry_run=false
home_dir=$HOME
usage() {
    printf 'Usage: %s [--dry-run] [--url URL] [--token-env NAME] [--home DIR] [--project DIR | --no-project]\n' "$0" >&2
    exit 2
}
inspect_options() {
    project_option=false
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dry-run) dry_run=true; shift ;;
            --no-project)
                [ "$project_option" = false ] || usage
                project_option=true
                shift ;;
            --home|--url|--token-env|--project)
                [ "$#" -ge 2 ] && [ -n "$2" ] || usage
                if [ "$1" = --home ]; then home_dir=$2; fi
                if [ "$1" = --project ]; then
                    [ "$project_option" = false ] || usage
                    project_option=true
                fi
                shift 2 ;;
            *) usage ;;
        esac
    done
}
inspect_options "$@"
case "$home_dir" in
    '~') home_dir=$HOME ;;
    '~/'*) home_dir="$HOME/${home_dir#\~/}" ;;
esac

# The CLI owns connection settings, native hooks and reversible pointer migration.
# Pass the environment variable name, never expand its credential into arguments.
onboard_status=0
commonplace onboard "$@" || onboard_status=$?
# Per-file refusals still allow independent skill setup; hard failures stop it.
if [ "$onboard_status" -gt 1 ]; then exit "$onboard_status"; fi

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for root in "$home_dir/.claude/skills" "$home_dir/.codex/skills" "$home_dir/.agents/skills"; do
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
Commonplace defaults to https://app.cmmn.dev; COMMONPLACE_URL or --url selects another server.
Keep the token in COMMONPLACE_TOKEN, or use --token-env to select another variable.
Client configuration stores an environment reference, never the token value.
Codex reviews native hook trust through its normal interface. MCP startup also provides context.
Call session_start automatically at session start, resume and compaction.
Use commonplace remember for memories and commonplace rule for rule proposals.
Imported and shared rule proposals await owner approval.
INSTRUCTIONS
exit "$onboard_status"
