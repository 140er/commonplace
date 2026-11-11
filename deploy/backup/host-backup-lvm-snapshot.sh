#!/usr/bin/env bash
# Nightly host backup from a short fsfreeze + LVM snapshot of the LUKS data/srv volume.
#
# The host hook (BACKUP_HOOK) pauses production for the whole
# backup. This script freezes /srv for well under 10 s, takes a snapshot, thaws,
# unlocks the snapshot through the existing automatic mechanism (clevis, as at
# boot), mounts it read-only and runs restic from there. Anything that makes the
# snapshot route impossible falls back to the host hook (pause route) after this
# run's own temporary snapshot, mapper and mount are removed and the backup
# locks are released.
#
# Same restic host/tag/paths/excludes and the same restic environment as the
# hook; no new credentials. No forget/prune (the repo is append-only, retention
# runs elsewhere). No chat or ticket notifications (the hook sends none either).
#
# Host-specific values are not in this repo; the unit that runs it sets them:
#   BACKUP_HOOK            the existing pause-route hook (fallback, --check, --latest-json)
#   BACKUP_RESTIC_HOST     restic --host value the hook uses
#   BACKUP_HOOK_LOCK_NAME  name of the hook's own lock file in the lock dir
#
# Usage: host-backup-lvm-snapshot.sh [--check | --latest-json | --help]
set -euo pipefail
umask 077

HOOK="${BACKUP_HOOK:-}"

usage() {
    cat <<'EOF'
Usage: host-backup-lvm-snapshot.sh [--check | --latest-json | --help]
  (no args)       LVM snapshot restic (restic host from BACKUP_RESTIC_HOST, tag nightly, same paths and
                  excludes as the hook). When the snapshot route is not
                  possible, runs the host hook (pause route) instead.
  --check, --latest-json
                  delegated to the host hook (read-only monitoring calls).
  --help          this text; never starts a backup.
No forget/prune. No notifications.
EOF
}

# ---- configuration (env overrides exist for tests and non-standard hosts) ----
SRV="${BACKUP_SRV:-/srv}"
VG="${BACKUP_VG:-data}"
ORIGIN_LV="${BACKUP_ORIGIN_LV:-srv}"
SRV_MAPPER="${BACKUP_SRV_MAPPER:-srv}"
MAPPER_DIR="${BACKUP_MAPPER_DIR:-/dev/mapper}"
MNT_BASE="${BACKUP_MNT_BASE:-/mnt}"
# Everything this run creates (snapshot LV, mapper, mount dir) starts with this
# reserved prefix; cleanup refuses to touch anything else.
SNAP_PREFIX="nightlysnap"
SNAP_SIZE_G="${BACKUP_SNAP_SIZE_G:-50}"
VG_HEADROOM_G="${BACKUP_VG_HEADROOM_G:-4}"
FREEZE_BUDGET_S="${BACKUP_FREEZE_BUDGET_S:-10}"
LVCREATE_MAX_S=7
UNLOCK_TIMEOUT_S="${BACKUP_UNLOCK_TIMEOUT_S:-45}"
CLOSE_ATTEMPTS="${BACKUP_CLOSE_ATTEMPTS:-6}"
CLOSE_RETRY_SLEEP_S="${BACKUP_CLOSE_RETRY_SLEEP_S:-1}"
# Logs and evidence live on the root fs: writes to /srv block while it is frozen.
LOG_DIR="${BACKUP_LOG_DIR:-/var/log/lvm-snapshot-backup}"
LOCK_DIR="${BACKUP_LOCK_DIR:-$SRV/commonplace/backups}"
RESTIC_ENV_FILE="${BACKUP_RESTIC_ENV_FILE:-/root/.config/restic/env}"
RESTIC_REST_PASSWORD_FILE="${BACKUP_RESTIC_REST_PASSWORD_FILE:-/root/.config/restic/rest-password}"
RESTIC_HOST="${BACKUP_RESTIC_HOST:-}"
HOOK_LOCK_NAME="${BACKUP_HOOK_LOCK_NAME:-}"
RESTIC_TAG="nightly"
BACKUP_PATHS=(/etc /srv /var/lib/caddy)
BACKUP_EXCLUDES=(/srv/docker /srv/containerd /srv/runner /srv/render-scratch /srv/voice)
METHOD="lvm-fsfreeze-clevis-restic"

# ---- run state (reset by init_run) ----
TS="" SNAP_NAME="" MAP="" MNT="" SNAPDEV="" LOG="" RESULT="" JSON=""
FROZEN=0 GUARD_PID="" UNLOCKED=0 MOUNTED=0
CLEANUP_OK=0 RESULT_WRITTEN=0
FREEZE_MS=-1 RESTIC_BACKUP_RC=-1 RESTIC_CHECK_RC=-1 RESTIC_SNAP_ID="" FAIL_REASON=""

init_run() {
    TS="$(date +%Y%m%d%H%M%S)"
    SNAP_NAME="${SNAP_PREFIX}${TS}"
    MAP="$SNAP_NAME"
    MNT="$MNT_BASE/$SNAP_NAME"
    LOG="$LOG_DIR/lvm-snap-$TS.log"
    RESULT="$LOG_DIR/result"
    JSON="$LOG_DIR/lvm-proof.json"
}

log() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S %Z') $*"
    printf '%s\n' "$line" >>"$LOG"
    printf '%s\n' "$line" >&2
}

redact() {
    sed -E 's#https?://[^[:space:]"]+#[URL]#g; s#(RESTIC_[A-Z0-9_]+=)\S+#\1[REDACTED]#g; s#([Tt]ang[^[:space:]]*)#tang#g'
}

# ---- freeze / thaw ----
# Thaw /srv, stop the guard. Safe to call repeatedly.
thaw() {
    if (( FROZEN )); then
        fsfreeze -u "$SRV" 2>>"$LOG" || fsfreeze -u "$SRV" || true
        FROZEN=0
        UNFROZE_NS=$(date +%s%N)
        log "fsfreeze -u $SRV"
    fi
    stop_guard
}

stop_guard() {
    if [[ -n "$GUARD_PID" ]]; then
        kill "$GUARD_PID" 2>/dev/null || true
        wait "$GUARD_PID" 2>/dev/null || true
        GUARD_PID=""
    fi
}

# Independent watchdog: thaws /srv after FREEZE_BUDGET_S whatever else happens
# (a hung lvcreate, a killed parent that never reaches its trap).
start_guard() {
    (
        # Not holding the backup locks open; ends its own sleep when terminated.
        exec 8>&- 9>&-
        sleep_pid=""
        trap '[[ -z "$sleep_pid" ]] || kill "$sleep_pid" 2>/dev/null; exit 0' TERM
        sleep "$FREEZE_BUDGET_S" & sleep_pid=$!
        wait "$sleep_pid"
        fsfreeze -u "$SRV" >/dev/null 2>&1 || true
        printf '%s GUARD thawed %s after %ss\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$SRV" "$FREEZE_BUDGET_S" >>"$LOG"
    ) &
    GUARD_PID=$!
}

# ---- evidence ----
write_json() {
    METHOD="$METHOD" SNAP_NAME="$SNAP_NAME" MAP="$MAP" JSON="$JSON" \
        RESTIC_SNAP_ID="$RESTIC_SNAP_ID" RESTIC_BACKUP_RC="$RESTIC_BACKUP_RC" \
        RESTIC_CHECK_RC="$RESTIC_CHECK_RC" FREEZE_MS="$FREEZE_MS" \
        CLEANUP_OK="$CLEANUP_OK" FAIL_REASON="$FAIL_REASON" \
        python3 -I - <<'PY' || true
import json, os, time
from pathlib import Path

e = os.environ
data = {
    "method": e["METHOD"],
    "ts": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    "snapshot_lv": e["SNAP_NAME"],
    "mapper": e["MAP"],
    "restic_snapshot_id": e["RESTIC_SNAP_ID"],
    "restic_backup_rc": int(e["RESTIC_BACKUP_RC"]),
    "restic_check_rc": int(e["RESTIC_CHECK_RC"]),
    "freeze_ms": int(e["FREEZE_MS"]),
    "cleanup_ok": int(e["CLEANUP_OK"]),
    "fail_reason": e["FAIL_REASON"],
}
Path(e["JSON"]).write_text(json.dumps(data, indent=2) + "\n")
PY
}

# ---- cleanup: only this run's snapshot, mapper and mount ----
# Order: thaw, umount, cryptsetup close (retried: the mapper is briefly "still
# in use" right after umount), lvremove. Idempotent; returns non-zero when
# something is left behind.
cleanup_snap() {
    local rc=0 attempt closed origin
    thaw

    if (( MOUNTED )) || { [[ -n "$MNT" ]] && findmnt -rn "$MNT" >/dev/null 2>&1; }; then
        if umount "$MNT" >>"$LOG" 2>&1; then
            log "umount $MNT"
            MOUNTED=0
        elif umount -l "$MNT" >>"$LOG" 2>&1; then
            log "WARN umount $MNT failed; lazy umount done"
            MOUNTED=0
        else
            log "WARN umount $MNT failed (lazy too)"
            rc=1
        fi
    fi

    if (( UNLOCKED )) || [[ -n "$MAP" && -e "$MAPPER_DIR/$MAP" ]]; then
        if [[ -z "$MAP" || "$MAP" == "$SRV_MAPPER" || "$MAP" != "$SNAP_PREFIX"* ]]; then
            log "REFUSING to close mapper '$MAP'"
            rc=1
        else
            closed=0
            for (( attempt = 1; attempt <= CLOSE_ATTEMPTS; attempt++ )); do
                if [[ ! -e "$MAPPER_DIR/$MAP" ]]; then
                    closed=1
                    break
                fi
                if cryptsetup close "$MAP" >>"$LOG" 2>&1; then
                    log "cryptsetup close $MAP (attempt $attempt)"
                    closed=1
                    break
                fi
                log "cryptsetup close $MAP attempt $attempt: still in use"
                (( attempt == CLOSE_ATTEMPTS )) || sleep "$CLOSE_RETRY_SLEEP_S"
            done
            if (( closed )); then
                UNLOCKED=0
            else
                log "WARN cryptsetup close $MAP failed after $CLOSE_ATTEMPTS attempts"
                rc=1
            fi
        fi
    fi

    if [[ -n "$SNAP_NAME" ]] && lvs "$VG/$SNAP_NAME" >/dev/null 2>&1; then
        if [[ "$SNAP_NAME" != "$SNAP_PREFIX"* || "$SNAP_NAME" == "$ORIGIN_LV" ]]; then
            log "REFUSING to lvremove unexpected name $SNAP_NAME"
            rc=1
        elif (( UNLOCKED )); then
            log "WARN keeping $VG/$SNAP_NAME: mapper $MAP is still open"
            rc=1
        else
            origin="$(lvs --noheadings -o origin "$VG/$SNAP_NAME" 2>/dev/null | tr -d '[:space:]')"
            if [[ "$origin" != "$ORIGIN_LV" ]]; then
                log "REFUSING to lvremove $VG/$SNAP_NAME: origin '$origin' is not $ORIGIN_LV"
                rc=1
            elif lvremove -y "$VG/$SNAP_NAME" >>"$LOG" 2>&1; then
                log "lvremove $VG/$SNAP_NAME"
            else
                log "WARN lvremove $VG/$SNAP_NAME failed"
                rc=1
            fi
        fi
    fi

    [[ -z "$MNT" ]] || rmdir "$MNT" 2>/dev/null || true
    if (( rc == 0 )); then
        CLEANUP_OK=1
        log "cleanup OK"
    else
        CLEANUP_OK=0
        log "cleanup incomplete"
    fi
    return "$rc"
}

release_locks() {
    exec 8>&- 9>&-
}

# Record the outcome once: cleanup, evidence, result line.
finish_run() {
    local kind="$1"
    cleanup_snap || true
    write_json
    if (( ! RESULT_WRITTEN )); then
        if [[ "$kind" == PASS ]]; then
            printf 'PASS method=%s restic=%s check_rc=%s freeze_ms=%s cleanup_ok=%s\n' \
                "$METHOD" "$RESTIC_SNAP_ID" "$RESTIC_CHECK_RC" "$FREEZE_MS" "$CLEANUP_OK" >"$RESULT" || true
        else
            printf '%s %s\n' "$kind" "$FAIL_REASON" >"$RESULT" || true
        fi
        RESULT_WRITTEN=1
    fi
}

fail_exit() {
    FAIL_REASON="${1:-unknown failure}"
    log "FAIL: $FAIL_REASON"
    finish_run FAIL
    exit 1
}

# The snapshot route is not possible: remove this run's leftovers, release the
# locks (the hook takes them itself), then run the pause-route hook. Never used
# once restic has run against a snapshot.
fallback_to_hook() {
    FAIL_REASON="${1:-snapshot route not possible}"
    log "snapshot route not possible: $FAIL_REASON; falling back to $HOOK (pause route)"
    finish_run FALLBACK
    release_locks
    exec "$HOOK"
}

on_exit() {
    local status=$?
    trap - EXIT
    if (( ! RESULT_WRITTEN )); then
        [[ -n "$FAIL_REASON" ]] || FAIL_REASON="unexpected exit status $status"
        finish_run FAIL
    else
        cleanup_snap || true
    fi
    exit "$status"
}

# ---- helpers for the main flow ----
# Fails (non-zero) when the log dir would sit on the frozen filesystem.
log_dir_off_srv() {
    local logreal srvreal
    logreal="$(realpath -m -- "$LOG_DIR")"
    srvreal="$(realpath -m -- "$SRV")"
    [[ "$logreal" != "$srvreal" && "$logreal" != "$srvreal"/* ]]
}

# Free bytes in the VG; non-numeric output means "unknown".
vg_free_bytes() {
    vgs --noheadings --units b --nosuffix -o vg_free "$VG" 2>/dev/null | tr -d '[:space:]'
}

# Classify a failed unlock: passphrase | timeout | unreachable_or_fail.
classify_unlock_failure() {
    local rc="$1" file="$2"
    if grep -qiE 'passphrase|password|enter ' "$file"; then
        echo passphrase
    elif (( rc == 124 )); then
        echo timeout
    else
        echo unreachable_or_fail
    fi
}

backup_from_snapshot() {
    # Restic environment exactly as the hook: its env file plus the rest password.
    set -a
    # shellcheck disable=SC1090
    . "$RESTIC_ENV_FILE"
    set +a
    RESTIC_REST_PASSWORD="$(cat "$RESTIC_REST_PASSWORD_FILE")"
    export RESTIC_REST_PASSWORD

    local before="$LOG_DIR/restic-snapshots-before.json" after="$LOG_DIR/restic-snapshots-after.json"
    local list=(snapshots --json --host "$RESTIC_HOST" --tag "$RESTIC_TAG")
    local p excludes=() rc
    for p in "${BACKUP_PATHS[@]}"; do list+=(--path "$p"); done
    for p in "${BACKUP_EXCLUDES[@]}"; do excludes+=(--exclude "$p"); done
    restic "${list[@]}" >"$before" 2>>"$LOG" || fail_exit "restic snapshots (before) failed"

    # The snapshot appears as /srv only inside a private mount namespace (bind,
    # remount read-only); the live /srv is never touched.
    log "restic backup from snapshot ($MNT presented as $SRV in a private mount namespace)"
    set +e
    unshare --mount --propagation private -- bash -c '
        set -e
        mount --bind "$1" "$2"
        mount -o remount,ro,bind "$2"
        shift 2
        exec restic backup --host "$1" --tag "$2" --one-file-system "${@:3}"
    ' bash "$MNT" "$SRV" "$RESTIC_HOST" "$RESTIC_TAG" "${excludes[@]}" "${BACKUP_PATHS[@]}" >>"$LOG" 2>&1
    rc=$?
    set -e
    RESTIC_BACKUP_RC=$rc
    log "restic backup rc=$rc"
    if (( rc != 0 )); then
        unset RESTIC_REST_PASSWORD
        fail_exit "restic backup exited $rc"
    fi

    restic "${list[@]}" >"$after" 2>>"$LOG" || fail_exit "restic snapshots (after) failed"
    RESTIC_SNAP_ID="$(BEFORE="$before" AFTER="$after" python3 -I - <<'PY'
import json, os, sys

before = {s["id"] for s in json.load(open(os.environ["BEFORE"]))}
new = [s for s in json.load(open(os.environ["AFTER"])) if s["id"] not in before]
if len(new) != 1:
    print(f"expected one new snapshot, got {len(new)}", file=sys.stderr)
    sys.exit(2)
print(new[0]["id"])
PY
    )" || fail_exit "restic did not produce exactly one new $RESTIC_HOST/$RESTIC_TAG snapshot"
    log "new restic snapshot $RESTIC_SNAP_ID"

    set +e
    restic check >>"$LOG" 2>&1
    RESTIC_CHECK_RC=$?
    set -e
    log "restic check rc=$RESTIC_CHECK_RC"
    unset RESTIC_REST_PASSWORD
    (( RESTIC_CHECK_RC == 0 )) || fail_exit "restic check exited $RESTIC_CHECK_RC (snapshot $RESTIC_SNAP_ID)"
}

main() {
    case "${1:-}" in
        --check | --latest-json)
            [[ -n "$HOOK" ]] || { echo "missing configuration: BACKUP_HOOK" >&2; return 2; }
            exec "$HOOK" "$1"
            ;;
        --help | -h) usage; return 0 ;;
        "") ;;
        *) echo "unknown argument: $1 (try --help)" >&2; return 2 ;;
    esac
    local missing=""
    [[ -n "$HOOK" ]] || missing="BACKUP_HOOK"
    [[ -n "$RESTIC_HOST" ]] || missing="$missing BACKUP_RESTIC_HOST"
    [[ -n "$HOOK_LOCK_NAME" && "$HOOK_LOCK_NAME" != */* ]] || missing="$missing BACKUP_HOOK_LOCK_NAME"
    if [[ -n "$missing" ]]; then
        echo "missing or invalid configuration:$missing (set by the host unit)" >&2
        return 2
    fi
    if [[ "$(realpath -m -- "$HOOK")" == "$(realpath -m -- "${BASH_SOURCE[0]}")" ]]; then
        echo "BACKUP_HOOK points at this script; it must be the pause-route hook" >&2
        return 2
    fi

    init_run
    if ! log_dir_off_srv; then
        echo "refusing: log dir $LOG_DIR is on $SRV, which blocks while frozen" >&2
        return 2
    fi
    mkdir -p "$LOG_DIR"
    : >>"$LOG"
    trap on_exit EXIT
    trap 'exit 143' TERM INT
    log "start snap=$SNAP_NAME mnt=$MNT size=${SNAP_SIZE_G}G hook=$HOOK"

    # ---- locks: same two as the hook; released before any fallback ----
    local cmd
    mountpoint -q "$SRV" || { release_locks; fallback_to_hook "$SRV is not mounted"; }
    if [[ -L "$LOCK_DIR" || -L "$LOCK_DIR/$HOOK_LOCK_NAME" || -L "$LOCK_DIR/.commonplace-backup.lock" ]]; then
        fail_exit "refusing symlinked backup root or lockfiles"
    fi
    mkdir -p "$LOCK_DIR"
    exec 8>"$LOCK_DIR/.commonplace-backup.lock"
    flock -n 8 || fail_exit "another commonplace backup holds the lock"
    exec 9>"$LOCK_DIR/$HOOK_LOCK_NAME"
    flock -n 9 || fail_exit "the host backup hook holds its lock"

    # ---- preflight: anything missing means the snapshot route is not possible ----
    for cmd in fsfreeze lvcreate lvs lvremove vgs clevis cryptsetup mount umount unshare restic findmnt python3 timeout; do
        command -v "$cmd" >/dev/null 2>&1 || fallback_to_hook "required command missing: $cmd"
    done
    [[ -r "$RESTIC_ENV_FILE" && -r "$RESTIC_REST_PASSWORD_FILE" ]] ||
        fallback_to_hook "restic environment files not readable"
    if [[ "$MAP" == "$SRV_MAPPER" || "$SNAP_NAME" == "$ORIGIN_LV" ]]; then
        fail_exit "internal: reserved name"
    fi
    if [[ -e "$MAPPER_DIR/$MAP" ]]; then fail_exit "mapper $MAP already exists"; fi
    if lvs "$VG/$SNAP_NAME" >/dev/null 2>&1; then fail_exit "LV $VG/$SNAP_NAME already exists"; fi

    # ---- size check: snapshot size plus VG headroom ----
    local free need
    free="$(vg_free_bytes)"
    need=$(( (SNAP_SIZE_G + VG_HEADROOM_G) * 1024 * 1024 * 1024 ))
    [[ "$free" =~ ^[0-9]+$ ]] || fallback_to_hook "cannot read free space of VG $VG"
    if (( free < need )); then
        fallback_to_hook "VG $VG free ${free}B is below snapshot ${SNAP_SIZE_G}G + ${VG_HEADROOM_G}G headroom"
    fi
    {
        echo "==== vgs/lvs before ===="
        vgs --units g -o vg_name,vg_size,vg_free,vg_extent_size "$VG"
        lvs -o lv_name,lv_size,lv_attr,origin,data_percent --units g "$VG"
    } >>"$LOG" 2>&1 || true

    # ---- freeze + snapshot; the guard thaws after FREEZE_BUDGET_S regardless ----
    # Nothing in this window writes to /srv: log, evidence and scratch files are
    # on the root fs. This LVM rejects -Zn/--zero on snapshots and
    # --noudevsync failed ("device not cleared"), so plain lvcreate --snapshot.
    local t0 now remain lv_ok=0
    start_guard
    t0=$(date +%s%N)
    log "fsfreeze -f $SRV"
    if ! fsfreeze -f "$SRV" 2>>"$LOG"; then
        stop_guard
        fallback_to_hook "fsfreeze -f $SRV failed"
    fi
    FROZEN=1
    now=$(date +%s%N)
    remain=$(( (t0 + FREEZE_BUDGET_S * 1000000000 - now) / 1000000000 ))
    (( remain <= LVCREATE_MAX_S )) || remain=$LVCREATE_MAX_S
    if (( remain < 1 )); then
        log "no time left in the ${FREEZE_BUDGET_S}s freeze window; skipping lvcreate"
    elif timeout "$remain" lvcreate -y --snapshot -L "${SNAP_SIZE_G}G" -n "$SNAP_NAME" "$VG/$ORIGIN_LV" >>"$LOG" 2>&1; then
        lv_ok=1
        log "lvcreate snapshot $VG/$SNAP_NAME size=${SNAP_SIZE_G}G of $VG/$ORIGIN_LV"
    else
        log "lvcreate failed or timed out (limit ${remain}s)"
    fi
    UNFROZE_NS=""
    thaw
    FREEZE_MS=$(( (${UNFROZE_NS:-$(date +%s%N)} - t0) / 1000000 ))
    log "freeze duration ${FREEZE_MS} ms"
    (( lv_ok )) || fallback_to_hook "lvcreate snapshot failed or exceeded ${remain}s in the freeze window (freeze_ms=$FREEZE_MS)"

    if [[ -e "/dev/$VG/$SNAP_NAME" ]]; then
        SNAPDEV="/dev/$VG/$SNAP_NAME"
    elif [[ -e "$MAPPER_DIR/$VG-$SNAP_NAME" ]]; then
        SNAPDEV="$MAPPER_DIR/$VG-$SNAP_NAME"
    else
        fallback_to_hook "snapshot LV created but its device node was not found"
    fi
    log "snapshot device $SNAPDEV"

    # ---- unlock: existing automatic mechanism only; once, stdin closed ----
    # A passphrase prompt or unreachable tang means STOP: no retry, no key
    # search, drop the snapshot and use the pause route.
    local unlock_err unlock_rc kind
    unlock_err="$(mktemp "$LOG_DIR/clevis-err.XXXXXX")"
    set +e
    timeout "$UNLOCK_TIMEOUT_S" clevis luks unlock -d "$SNAPDEV" -n "$MAP" -o --readonly </dev/null >"$unlock_err" 2>&1
    unlock_rc=$?
    set -e
    kind=ok
    (( unlock_rc == 0 )) || kind="$(classify_unlock_failure "$unlock_rc" "$unlock_err")"
    redact <"$unlock_err" >>"$LOG" || true
    rm -f -- "$unlock_err"
    if [[ "$kind" != ok || ! -e "$MAPPER_DIR/$MAP" ]]; then
        # A mapper may exist even after a failed unlock; make cleanup close it.
        [[ ! -e "$MAPPER_DIR/$MAP" ]] || UNLOCKED=1
        [[ "$kind" != ok ]] || kind="no_mapper"
        fallback_to_hook "clevis luks unlock: $kind (rc=$unlock_rc); STOP, no retry, no key search"
    fi
    UNLOCKED=1
    log "clevis luks unlock OK ($MAP, readonly)"

    # ---- read-only mount; no nouuid (ext4 on this kernel rejects it) ----
    mkdir -p "$MNT"
    mount -t ext4 -o ro,noload,norecovery "$MAPPER_DIR/$MAP" "$MNT" >>"$LOG" 2>&1 ||
        fallback_to_hook "mount ro,noload,norecovery of $MAPPER_DIR/$MAP failed"
    MOUNTED=1
    log "mounted $MNT ro,noload,norecovery"

    backup_from_snapshot

    cleanup_snap || fail_exit "restic OK but cleanup of the temporary snapshot failed"
    {
        echo "==== vgs/lvs after cleanup ===="
        vgs --units g -o vg_name,vg_size,vg_free "$VG"
        lvs -o lv_name,lv_size,lv_attr,origin --units g "$VG"
    } >>"$LOG" 2>&1 || true
    finish_run PASS
    log "PASS restic=$RESTIC_SNAP_ID check_rc=$RESTIC_CHECK_RC freeze_ms=$FREEZE_MS cleanup_ok=$CLEANUP_OK"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
