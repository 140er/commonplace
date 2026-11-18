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
# Usage: host-backup-lvm-snapshot.sh [--check | --latest-json | --retry-check | --help]
set -euo pipefail
umask 077

HOOK="${BACKUP_HOOK:-}"

usage() {
    cat <<'EOF'
Usage: host-backup-lvm-snapshot.sh [--check | --latest-json | --retry-check | --help]
  (no args)       LVM snapshot restic (restic host from BACKUP_RESTIC_HOST, tag nightly, same paths and
                  excludes as the hook). When the snapshot route is not
                  possible, runs the host hook (pause route) instead.
  --check, --latest-json
                  delegated to the host hook (read-only monitoring calls).
  --retry-check   finish a run whose result is CHECK-PENDING (uploaded, restic check
                  not done): clear stale locks, run restic check again. No new backup.
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
# The mapper can stay "in use" for a while after umount (udev and the kernel
# release it asynchronously); a 6x1s loop reported FAIL although close worked
# seconds later. Retry generously and fail cleanup only when it still fails.
CLOSE_ATTEMPTS="${BACKUP_CLOSE_ATTEMPTS:-30}"
CLOSE_RETRY_SLEEP_S="${BACKUP_CLOSE_RETRY_SLEEP_S:-2}"
LVREMOVE_ATTEMPTS="${BACKUP_LVREMOVE_ATTEMPTS:-5}"
LVREMOVE_RETRY_SLEEP_S="${BACKUP_LVREMOVE_RETRY_SLEEP_S:-2}"
# Holders of a busy snapshot: processes whose mount namespace still lists it. After the
# first cleanup pass fails, removal is retried in bounded late rounds (a holder may exit).
PROC_DIR="${BACKUP_PROC_DIR:-/proc}"
LATE_RETRY_ATTEMPTS="${BACKUP_LATE_RETRY_ATTEMPTS:-10}"
LATE_RETRY_SLEEP_S="${BACKUP_LATE_RETRY_SLEEP_S:-30}"
# restic check needs the repository lock free: stale locks are cleared with a plain
# `restic unlock` (never --remove-all); a live lock is waited out, bounded.
CHECK_LOCK_ATTEMPTS="${BACKUP_CHECK_LOCK_ATTEMPTS:-10}"
CHECK_LOCK_SLEEP_S="${BACKUP_CHECK_LOCK_SLEEP_S:-60}"
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
CHECK_PENDING=0 SNAPSHOT_LEFT=0 CLEANUP_EXHAUSTED=0
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
        CHECK_PENDING="$CHECK_PENDING" SNAPSHOT_LEFT="$SNAPSHOT_LEFT" \
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
    "check_pending": int(e["CHECK_PENDING"]),
    "snapshot_left": int(e["SNAPSHOT_LEFT"]),
    "fail_reason": e["FAIL_REASON"],
}
Path(e["JSON"]).write_text(json.dumps(data, indent=2) + "\n")
PY
}

# Flush and let udev finish its work on the just-released devices. Best effort.
settle_devices() {
    sync || true
    ! command -v udevadm >/dev/null 2>&1 || timeout 30 udevadm settle >>"$LOG" 2>&1 || true
}

# True while this run's own snapshot LV exists (reserved name only).
snapshot_present() {
    [[ -n "$SNAP_NAME" && "$SNAP_NAME" == "$SNAP_PREFIX"* && "$SNAP_NAME" != "$ORIGIN_LV" ]] &&
        lvs "$VG/$SNAP_NAME" >/dev/null 2>&1
}

# Log every process whose mount namespace still lists this run's snapshot (a CI sandbox
# that copied the namespace while it was mounted keeps the mapper busy). Read-only.
log_snapshot_holders() {
    local mi pid comm cmd found=0
    [[ -n "$SNAP_NAME" && "$SNAP_NAME" == "$SNAP_PREFIX"* ]] || return 0
    for mi in "$PROC_DIR"/[0-9]*/mountinfo; do
        [[ -r "$mi" ]] || continue
        pid="${mi#"$PROC_DIR"/}"
        pid="${pid%%/*}"
        [[ "$pid" != "$$" ]] || continue
        grep -qF -- "$SNAP_NAME" "$mi" 2>/dev/null || continue
        comm="$(tr -d '\n' <"$PROC_DIR/$pid/comm" 2>/dev/null || true)"
        cmd="$(tr '\0' ' ' <"$PROC_DIR/$pid/cmdline" 2>/dev/null | cut -c1-200 | redact || true)"
        log "holder pid=$pid comm=${comm:-?} cmd=${cmd:-?} mntns=$(readlink "$PROC_DIR/$pid/ns/mnt" 2>/dev/null || echo ?)"
        found=1
    done
    (( found )) || log "no process lists $SNAP_NAME in its mountinfo"
}

# ---- cleanup: only this run's snapshot, mapper and mount ----
# Order: thaw, umount, sync + udevadm settle, cryptsetup close (retried: the
# mapper stays "in use" for a while after umount), a last dmsetup remove --retry,
# then lvremove (retried). Idempotent; returns non-zero only when something is
# still left behind after all retries.
cleanup_snap() {
    local rc=0 attempt closed origin removed
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
        settle_devices
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
            if (( ! closed )) && command -v dmsetup >/dev/null 2>&1; then
                # Last resort, still only this run's reserved-prefix mapper.
                log "cryptsetup close $MAP failed $CLOSE_ATTEMPTS times; dmsetup remove --retry"
                dmsetup remove --retry "$MAP" >>"$LOG" 2>&1 || true
                [[ -e "$MAPPER_DIR/$MAP" ]] || closed=1
            fi
            if (( closed )); then
                UNLOCKED=0
            else
                log "WARN cryptsetup close $MAP failed after $CLOSE_ATTEMPTS attempts"
                log_snapshot_holders
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
            else
                removed=0
                for (( attempt = 1; attempt <= LVREMOVE_ATTEMPTS; attempt++ )); do
                    if lvremove -y "$VG/$SNAP_NAME" >>"$LOG" 2>&1; then
                        log "lvremove $VG/$SNAP_NAME (attempt $attempt)"
                        removed=1
                        break
                    fi
                    log "lvremove $VG/$SNAP_NAME attempt $attempt failed"
                    if (( attempt < LVREMOVE_ATTEMPTS )); then
                        sleep "$LVREMOVE_RETRY_SLEEP_S"
                        settle_devices
                    fi
                done
                if (( ! removed )); then
                    log "WARN lvremove $VG/$SNAP_NAME failed after $LVREMOVE_ATTEMPTS attempts"
                    log_snapshot_holders
                    rc=1
                fi
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

# cleanup_snap, then bounded late rounds (one close and one lvremove attempt each, after
# a pause) while a holder may still exit. Same name/origin guards as cleanup_snap. Once
# the rounds are used up, later calls (finish_run, exit trap) try once and do not wait
# again, so a stuck snapshot costs one bounded wait per run.
cleanup_with_retries() {
    local round
    if (( CLEANUP_EXHAUSTED )); then
        local CLOSE_ATTEMPTS=1 LVREMOVE_ATTEMPTS=1
        cleanup_snap
        return
    fi
    cleanup_snap && return 0
    for (( round = 1; round <= LATE_RETRY_ATTEMPTS; round++ )); do
        sleep "$LATE_RETRY_SLEEP_S"
        log "late cleanup retry $round/$LATE_RETRY_ATTEMPTS"
        local CLOSE_ATTEMPTS=1 LVREMOVE_ATTEMPTS=1
        cleanup_snap && return 0
    done
    CLEANUP_EXHAUSTED=1
    return 1
}

release_locks() {
    exec 8>&- 9>&-
}

# Record the outcome once: cleanup, evidence, result line.
finish_run() {
    local kind="$1"
    cleanup_with_retries || true
    # A snapshot still present is never a quiet outcome: ALERT line, FAIL result, JSON
    # flag and (through the caller) a failing exit status.
    if snapshot_present; then
        SNAPSHOT_LEFT=1
        FAIL_REASON="ALERT: temporary snapshot $VG/$SNAP_NAME is still present after cleanup${FAIL_REASON:+; $FAIL_REASON}"
        log "ALERT: $VG/$SNAP_NAME is still present after cleanup (copy-on-write keeps growing); remove it by hand after checking the holders above"
        kind=FAIL
    elif [[ "$kind" == PASS ]] && (( CHECK_PENDING )); then
        kind=CHECK-PENDING
    fi
    write_json
    if (( ! RESULT_WRITTEN )); then
        if [[ "$kind" == PASS ]]; then
            printf 'PASS method=%s restic=%s check_rc=%s freeze_ms=%s cleanup_ok=%s\n' \
                "$METHOD" "$RESTIC_SNAP_ID" "$RESTIC_CHECK_RC" "$FREEZE_MS" "$CLEANUP_OK" >"$RESULT" || true
        elif [[ "$kind" == CHECK-PENDING ]]; then
            printf 'CHECK-PENDING uploaded restic=%s check_rc=%s freeze_ms=%s cleanup_ok=%s\n' \
                "$RESTIC_SNAP_ID" "$RESTIC_CHECK_RC" "$FREEZE_MS" "$CLEANUP_OK" >"$RESULT" || true
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
    if (( SNAPSHOT_LEFT )); then
        # The pause-route backup still runs, but the run fails so the alert is seen.
        local hook_rc=0
        "$HOOK" || hook_rc=$?
        log "ALERT: pause-route hook finished rc=$hook_rc with a leftover snapshot; failing the run"
        exit 1
    fi
    exec "$HOOK"
}

on_exit() {
    local status=$?
    trap - EXIT
    if (( ! RESULT_WRITTEN )); then
        [[ -n "$FAIL_REASON" ]] || FAIL_REASON="unexpected exit status $status"
        finish_run FAIL
    else
        cleanup_with_retries || true
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

# Restic environment exactly as the hook: its env file plus the rest password.
load_restic_env() {
    set -a
    # shellcheck disable=SC1090
    . "$RESTIC_ENV_FILE"
    set +a
    RESTIC_REST_PASSWORD="$(cat "$RESTIC_REST_PASSWORD_FILE")"
    export RESTIC_REST_PASSWORD
}

# List the repository locks, clear stale ones (plain `restic unlock`: stale locks only,
# never --remove-all, no forget/prune), then restic check. Exit 11 means the repository
# is still locked by a live process: wait and retry, bounded. Sets RESTIC_CHECK_RC.
run_restic_check() {
    local attempt rc=1
    for (( attempt = 1; attempt <= CHECK_LOCK_ATTEMPTS; attempt++ )); do
        log "restic locks before check (attempt $attempt/$CHECK_LOCK_ATTEMPTS)"
        restic list locks --no-lock >>"$LOG" 2>&1 || log "restic list locks failed (continuing)"
        restic unlock >>"$LOG" 2>&1 || log "restic unlock failed (continuing)"
        restic check >>"$LOG" 2>&1 && rc=0 || rc=$?
        (( rc == 11 )) || break
        log "restic check: repository still locked by a live lock (attempt $attempt/$CHECK_LOCK_ATTEMPTS)"
        (( attempt == CHECK_LOCK_ATTEMPTS )) || sleep "$CHECK_LOCK_SLEEP_S"
    done
    RESTIC_CHECK_RC=$rc
    log "restic check rc=$rc"
}

backup_from_snapshot() {
    load_restic_env

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

    run_restic_check
    unset RESTIC_REST_PASSWORD
    if (( RESTIC_CHECK_RC == 11 )); then
        # The upload is good; only the integrity check could not take the lock. Not a
        # failed backup: the result says CHECK-PENDING and --retry-check finishes it.
        CHECK_PENDING=1
        log "uploaded, check pending: snapshot $RESTIC_SNAP_ID; run --retry-check"
    elif (( RESTIC_CHECK_RC != 0 )); then
        fail_exit "restic check exited $RESTIC_CHECK_RC (snapshot $RESTIC_SNAP_ID)"
    fi
}

# Locks: same two as the hook. Sets FAIL_REASON and returns 1 when one is unavailable.
take_locks() {
    if [[ -L "$LOCK_DIR" || -L "$LOCK_DIR/$HOOK_LOCK_NAME" || -L "$LOCK_DIR/.commonplace-backup.lock" ]]; then
        FAIL_REASON="refusing symlinked backup root or lockfiles"
        return 1
    fi
    mkdir -p "$LOCK_DIR"
    exec 8>"$LOCK_DIR/.commonplace-backup.lock"
    flock -n 8 || { FAIL_REASON="another commonplace backup holds the lock"; return 1; }
    exec 9>"$LOCK_DIR/$HOOK_LOCK_NAME"
    flock -n 9 || { FAIL_REASON="the host backup hook holds its lock"; return 1; }
}

# Names of this script's own snapshots that still exist (reserved pattern only).
leftover_snapshots() {
    local names
    names="$(lvs --noheadings -o lv_name "$VG" 2>/dev/null)" || return 2
    printf '%s\n' "$names" | tr -d ' \t' | grep -E "^${SNAP_PREFIX}[0-9]{14}\$" || true
}

# Finish a CHECK-PENDING run: restic check again, no new backup, nothing deleted.
retry_check() {
    local kind rest id freeze cleanup snap mapper
    init_run
    mkdir -p "$LOG_DIR"
    : >>"$LOG"
    kind=""
    if [[ -r "$RESULT" && -r "$JSON" ]]; then read -r kind rest <"$RESULT" || true; fi
    if [[ "$kind" != CHECK-PENDING ]]; then
        echo "no CHECK-PENDING result in $LOG_DIR; nothing to retry" >&2
        return 2
    fi
    IFS=$'\t' read -r id freeze cleanup snap mapper < <(JSON="$JSON" python3 -I - <<'PY'
import json, os, re, sys

d = json.load(open(os.environ["JSON"]))
sid = str(d["restic_snapshot_id"])
if not re.fullmatch(r"[0-9a-f]{8,64}", sid):
    sys.exit(2)
ints = [int(d["freeze_ms"]), int(d["cleanup_ok"])]
names = [str(d["snapshot_lv"]), str(d["mapper"])]
if not all(re.fullmatch(r"[A-Za-z0-9_.+-]+", n) for n in names):
    sys.exit(2)
print("\t".join([sid, *map(str, ints), *names]))
PY
    ) || { echo "unreadable proof $JSON" >&2; return 2; }
    trap 'exit 143' TERM INT
    log "retry-check start for snapshot $id"
    take_locks || { log "FAIL: $FAIL_REASON"; return 1; }
    load_restic_env
    RESTIC_SNAP_ID="$id" FREEZE_MS="$freeze" CLEANUP_OK="$cleanup" SNAP_NAME="$snap" MAP="$mapper"
    RESTIC_BACKUP_RC=0
    run_restic_check
    unset RESTIC_REST_PASSWORD
    if (( RESTIC_CHECK_RC == 0 )); then
        CHECK_PENDING=0
        write_json
        printf 'PASS method=%s restic=%s check_rc=0 freeze_ms=%s cleanup_ok=%s check_retried=1\n' \
            "$METHOD" "$id" "$freeze" "$cleanup" >"$RESULT"
        log "PASS (check retried) restic=$id"
    elif (( RESTIC_CHECK_RC == 11 )); then
        CHECK_PENDING=1
        write_json
        log "uploaded, check still pending: snapshot $id"
        return 3
    else
        FAIL_REASON="restic check exited $RESTIC_CHECK_RC (snapshot $id)"
        write_json
        printf 'FAIL %s\n' "$FAIL_REASON" >"$RESULT"
        log "FAIL: $FAIL_REASON"
        return 1
    fi
}

require_config() {
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
}

main() {
    case "${1:-}" in
        --check | --latest-json)
            [[ -n "$HOOK" ]] || { echo "missing configuration: BACKUP_HOOK" >&2; return 2; }
            exec "$HOOK" "$1"
            ;;
        --help | -h) usage; return 0 ;;
        --retry-check)
            require_config || return 2
            log_dir_off_srv || { echo "refusing: log dir $LOG_DIR is on $SRV" >&2; return 2; }
            retry_check
            return
            ;;
        "") ;;
        *) echo "unknown argument: $1 (try --help)" >&2; return 2 ;;
    esac
    require_config || return 2

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
    take_locks || fail_exit "$FAIL_REASON"

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
    # A snapshot of an earlier run (own name pattern only) means its cleanup never
    # finished and its copy-on-write space keeps growing: refuse, alert, delete nothing.
    local left rc=0
    left="$(leftover_snapshots)" || rc=$?
    (( rc == 0 )) || fallback_to_hook "cannot list the logical volumes of VG $VG"
    if [[ -n "$left" ]]; then
        left="$(tr '\n' ' ' <<<"$left")"
        log "ALERT: snapshot(s) from an earlier run still exist in $VG: ${left% }; not starting a backup"
        fail_exit "ALERT: refusing to start: snapshot(s) from an earlier run still exist in $VG: ${left% }"
    fi

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

    cleanup_with_retries || fail_exit "restic OK but cleanup of the temporary snapshot failed"
    {
        echo "==== vgs/lvs after cleanup ===="
        vgs --units g -o vg_name,vg_size,vg_free "$VG"
        lvs -o lv_name,lv_size,lv_attr,origin --units g "$VG"
    } >>"$LOG" 2>&1 || true
    finish_run PASS
    if (( CHECK_PENDING )); then
        log "CHECK-PENDING restic=$RESTIC_SNAP_ID check_rc=$RESTIC_CHECK_RC freeze_ms=$FREEZE_MS cleanup_ok=$CLEANUP_OK"
    else
        log "PASS restic=$RESTIC_SNAP_ID check_rc=$RESTIC_CHECK_RC freeze_ms=$FREEZE_MS cleanup_ok=$CLEANUP_OK"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
