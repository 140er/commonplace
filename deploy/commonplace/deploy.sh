#!/usr/bin/env bash
set -Eeuo pipefail

die() {
    printf '[deploy] ERROR: %s: %s\n' "$1" "$2" >&2
    exit 1
}

fresh_enough() {
    local start=$1 snap=$2 now=$3 max_age=$4
    (( snap >= start && snap <= now + 60 && now - snap <= max_age ))
}

run_command() {
    local -a command
    read -r -a command <<< "$1"
    set -f
    "${command[@]}"
}

main() {
    local key value line config start before after result latest timestamp snap now rendered health
    [[ $(id -u) == 0 ]] || die preflight 'must run as root'
    [[ $# == 0 ]] || die usage 'deploy.sh accepts no arguments'
    COMMONPLACE_ROOT=${COMMONPLACE_ROOT:-/srv/commonplace}
    config=${COMMONPLACE_DEPLOY_ENV:-$COMMONPLACE_ROOT/deploy.env}
    if [[ -f $config ]]; then
        while IFS= read -r line || [[ -n $line ]]; do
            [[ $line =~ ^[A-Z_][A-Z0-9_]*= ]] || continue
            key=${line%%=*}
            value=${line#*=}
            if [[ ! -v $key ]]; then
                export "$key=$value"
            fi
        done < "$config"
    fi
    [[ -n ${DEPLOY_BACKUP_UNIT:-} ]] || die config 'DEPLOY_BACKUP_UNIT is required'
    [[ -n ${DEPLOY_BACKUP_CHECK_CMD:-} ]] || die config 'DEPLOY_BACKUP_CHECK_CMD is required'
    [[ -n ${DEPLOY_BACKUP_LATEST_CMD:-} ]] || die config 'DEPLOY_BACKUP_LATEST_CMD is required'
    DEPLOY_BACKUP_MAX_AGE=${DEPLOY_BACKUP_MAX_AGE:-900}
    APP_PORT=${APP_PORT:-8765}
    [[ $DEPLOY_BACKUP_MAX_AGE =~ ^[0-9]{1,5}$ ]] || die config 'invalid DEPLOY_BACKUP_MAX_AGE'
    (( 10#$DEPLOY_BACKUP_MAX_AGE >= 60 && 10#$DEPLOY_BACKUP_MAX_AGE <= 86400 )) || die config 'invalid DEPLOY_BACKUP_MAX_AGE'
    [[ $APP_PORT =~ ^[0-9]{1,5}$ ]] || die config 'invalid APP_PORT'
    (( 10#$APP_PORT >= 1 && 10#$APP_PORT <= 65535 )) || die config 'invalid APP_PORT'
    APP_PORT=$((10#$APP_PORT))
    COMPOSE_FILE=${COMPOSE_FILE:-$COMMONPLACE_ROOT/compose.yml}
    COMPOSE_ENV_FILE=${COMPOSE_ENV_FILE:-$COMMONPLACE_ROOT/.env}
    for key in jq flock curl systemctl docker; do
        command -v "$key" >/dev/null || die preflight "missing $key"
    done
    [[ -f $COMPOSE_FILE && -f $COMPOSE_ENV_FILE ]] || die config 'compose and environment files must exist'
    { exec 9>"$COMMONPLACE_ROOT/.deploy.lock"; } 2>/dev/null || die lock 'cannot open deploy lock'
    flock -n 9 || die lock 'another deploy is running'
    start=$(date -u +%s) || die preflight 'cannot read clock'

    before=$(systemctl show -p InvocationID --value "$DEPLOY_BACKUP_UNIT" 2>/dev/null) || die backup 'cannot read invocation'
    systemctl start --wait "$DEPLOY_BACKUP_UNIT" 2>/dev/null || die backup 'unit start failed'
    after=$(systemctl show -p InvocationID --value "$DEPLOY_BACKUP_UNIT" 2>/dev/null) || die backup 'cannot read invocation'
    [[ -n $after && $after != "$before" ]] || die backup 'unit did not run a new invocation'
    result=$(systemctl show -p Result --value "$DEPLOY_BACKUP_UNIT" 2>/dev/null) || die backup 'cannot read result'
    [[ $result == success ]] || die backup 'unit did not succeed'

    latest=$(run_command "$DEPLOY_BACKUP_LATEST_CMD" 2>/dev/null) || die latest 'command failed'
    timestamp=$(jq -er 'select(type == "object") | select(.id | type == "string" and test("^[0-9a-f]{64}$")) | .time | select(type == "string")' <<< "$latest" 2>/dev/null) || die latest 'invalid snapshot JSON'
    [[ $timestamp =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$ ]] || die latest 'invalid snapshot time'
    snap=$(date -d "$timestamp" +%s 2>/dev/null) || die latest 'invalid snapshot time'
    now=$(date -u +%s) || die latest 'cannot read clock'
    fresh_enough "$start" "$snap" "$now" "$((10#$DEPLOY_BACKUP_MAX_AGE))" || die latest 'snapshot is not fresh'
    run_command "$DEPLOY_BACKUP_CHECK_CMD" >/dev/null 2>&1 || die check 'backup verification failed'

    local -a compose=(docker compose -f "$COMPOSE_FILE" --env-file "$COMPOSE_ENV_FILE")
    rendered=$("${compose[@]}" config --format json 2>/dev/null) || die deploy 'compose config failed'
    jq -e 'all(.services[] | .ports[]? | select(.published != null); .host_ip == "127.0.0.1")' <<< "$rendered" >/dev/null 2>&1 || die deploy 'published ports must use loopback'
    "${compose[@]}" up -d --build --remove-orphans || die deploy 'compose up failed'
    "${compose[@]}" ps || die deploy 'compose ps failed'
    health=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$APP_PORT/healthz") || die deploy 'health request failed'
    printf '[deploy] healthz: %s\n' "$health"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
