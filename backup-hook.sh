#!/usr/bin/env bash
# The host backup job calls this hook before running host-managed restic.
set -Eeuo pipefail
umask 077
# Keep diagnostics visible even when errexit fires inside the logged function.
exec 3>&2

stage="setup"
restart_needed=0
log_file=""
finish() {
    local status=$?
    trap - EXIT
    if (( restart_needed )); then
        if ! "${compose[@]}" start commonplace >>"$log_file" 2>&1; then
            status=1
            stage="$stage; gateway restart failed"
        fi
    fi
    if (( status != 0 )); then
        printf 'commonplace backup failed: %s (details: %s)\n' "$stage" "${log_file:-no log created}" >&3
    elif [[ -n "$log_file" ]]; then
        rm -f -- "$log_file"
    fi
    exit "$status"
}
trap finish EXIT

[[ $# == 1 && "$1" == /* && -d "$1" ]] || exit 2
backup_dir=$(realpath -- "$1")
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$repo_dir"
log_file=$(mktemp /tmp/commonplace-backup.XXXXXXXX.log)
operator_dir=${COMMONPLACE_DEPLOY_DIR:-$(dirname -- "$repo_dir")}
env_file=${COMMONPLACE_ENV_FILE:-$operator_dir/.env}
compose_file=${COMPOSE_FILE:-$operator_dir/compose.yml}
compose=(docker compose --env-file "$env_file" -f "$compose_file")

run_backup() {
    stage="lock"
    # The timer is root; keep this lock outside the source data tree.
    exec 9>"$backup_dir/.commonplace-backup.lock"
    flock -n 9

    stage="CLI preflight"
    [[ -n $("${compose[@]}" ps --status running -q commonplace) ]]
    "${compose[@]}" exec -T commonplace commonplace backup --help

    local stamp staging_parent container_staging host_staging destination
    stamp=$(date -u +%Y%m%dT%H%M%S.%NZ)-$$
    staging_parent="$backup_dir/.commonplace-staging"
    # The backup CLI requires an existing parent and a new destination,
    # outside /data. Mount only the staging parent into the one-off container.
    install -d -o 10001 -g 10001 -m 0700 -- "$staging_parent"
    container_staging="/backup/$stamp"
    host_staging="$staging_parent/$stamp"
    destination="$backup_dir/commonplace-$stamp"
    [[ ! -e "$host_staging" && ! -e "$destination" ]]

    # An exec'd CLI has no handle on the gateway's running worker processes.
    # Stop the container first so every writer is gone, then use the supported
    # backup command in a one-off container with the same persistent bind mount.
    stage="stop gateway"
    restart_needed=1
    "${compose[@]}" stop --timeout 60 commonplace
    stage="snapshot"
    "${compose[@]}" run --rm --no-deps -T --volume "$staging_parent:/backup" \
        commonplace commonplace backup "$container_staging"
    stage="restart gateway"
    "${compose[@]}" start commonplace
    restart_needed=0

    stage="verify snapshot"
    [[ -s "$host_staging/control.sqlite3" && -s "$host_staging/manifest.json" ]]
    python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$host_staging/manifest.json"
    stage="publish snapshot"
    # Both paths share a parent filesystem: publish atomically only
    # after backup and validation succeed. Keep failed staging for diagnosis.
    mv -T -- "$host_staging" "$destination"
    printf 'commonplace backup ready: %s\n' "$destination"
}
run_backup >>"$log_file" 2>&1
