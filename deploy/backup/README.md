# LVM snapshot backup

`host-backup-lvm-snapshot.sh` replaces the long production pause of the host
backup hook (`BACKUP_HOOK`) with a freeze of `/srv` that lasts
well under a second, then runs restic from an LVM snapshot of the LUKS
`data/srv` volume. When the snapshot route is not possible it runs the hook
(pause route) instead.

## Flow

1. Take the same two locks as the hook (`.commonplace-backup.lock`,
   the hook's own lock `BACKUP_HOOK_LOCK_NAME`, in `/srv/commonplace/backups`).
2. Preflight: tools present, restic environment readable, VG free space at least
   snapshot size (default 50 GiB) plus headroom (default 4 GiB).
3. `fsfreeze -f /srv`, `lvcreate --snapshot` of `data/srv`, `fsfreeze -u /srv`.
   A watchdog thaws `/srv` after 10 s whatever happens, and the exit trap thaws
   too. Logs and evidence live in `/var/log/lvm-snapshot-backup` (root fs): writes
   to `/srv` block while it is frozen, so the script refuses a log dir under
   `/srv`. Keep the unit's stdout/stderr off `/srv` as well (journald is fine).
   This LVM rejects `-Zn`/`--zero` on snapshots and `--noudevsync` failed with
   "device not cleared", so the plain command is used.
4. `clevis luks unlock -d <snapshot> -n nightlysnap<ts> -o --readonly`, once,
   stdin closed. A passphrase prompt, an unreachable tang or a timeout means
   stop: no retry, no key search, snapshot removed, pause-route fallback.
5. Mount `ro,noload,norecovery` (ext4 on this kernel rejects `nouuid`).
6. restic with the hook's environment (`/root/.config/restic/env` and
   `rest-password`; no new credentials): host `BACKUP_RESTIC_HOST`, tag `nightly`, paths
   `/etc /srv /var/lib/caddy`, the hook's excludes, `--one-file-system`. The
   snapshot is bind-mounted over `/srv` read-only inside an `unshare --mount`
   namespace only; the live `/srv` is unchanged. Then `restic check`.
7. Before `restic check`: `restic list locks --no-lock`, then a plain `restic unlock`
   (stale locks only; never `--remove-all`, never forget/prune). If `check` still
   exits 11 (a live lock), it is retried up to 10 times, 60 s apart
   (`BACKUP_CHECK_LOCK_ATTEMPTS`, `BACKUP_CHECK_LOCK_SLEEP_S`). A lock that never
   clears after a good upload is **not** a failed backup: the result is
   `CHECK-PENDING uploaded restic=<id> ...`, the proof has `check_pending: 1`, the
   exit status is 0, and `--retry-check` runs only the unlock and check again (no new
   backup, no snapshot) and turns the result into `PASS ... check_retried=1`
   (exit 3 while it is still locked). Any other check failure stays `FAIL`.
8. Cleanup (always, through an EXIT trap): umount, `sync` and `udevadm settle`,
   `cryptsetup close` retried up to 30 times 2 s apart (the mapper can stay "in
   use" for a while after umount), a last `dmsetup remove --retry` on this run's
   mapper, then `lvremove` of this run's snapshot (up to 5 attempts). When the
   mapper or the volume stays busy, the log lists the holders: every process whose
   `/proc/<pid>/mountinfo` names the snapshot (pid, name, command line, mount
   namespace; a CI sandbox that copied the mount namespace is the known case). The
   removal is then retried in bounded late rounds (10 rounds, 30 s apart:
   `BACKUP_LATE_RETRY_ATTEMPTS`, `BACKUP_LATE_RETRY_SLEEP_S`), nothing is
   unmounted inside other namespaces, and nobody is killed. A good backup plus
   check followed by a retried-but-successful cleanup is PASS. If the snapshot is
   still present after that, the run logs an `ALERT` line, writes
   `FAIL ALERT: ...` to `result`, sets `snapshot_left: 1` in the proof and exits 1,
   also when the run was a pause-route fallback (the hook still runs first). The
   snapshot name must start with the reserved prefix `nightlysnap` and the LV
   must be a snapshot of `srv`; `data/srv` and the `srv` mapper are never
   touched. The snapshot is kept if the mapper cannot be closed.
   Tunables: `BACKUP_CLOSE_ATTEMPTS`, `BACKUP_CLOSE_RETRY_SLEEP_S`,
   `BACKUP_LVREMOVE_ATTEMPTS`, `BACKUP_LVREMOVE_RETRY_SLEEP_S`, `BACKUP_PROC_DIR`.

An earlier run's snapshot blocks the next backup: before `fsfreeze`, if any logical
volume in the group matches `nightlysnap` plus 14 digits (this script's own name
pattern; nothing else), the run logs `ALERT`, writes `FAIL ALERT: refusing to start
...` to `result`, exits 1, and removes nothing (the guard never deletes; an operator
removes the leftover after checking the holders). Mount propagation was considered
and not changed: `unshare --mount` copies every mount whatever its propagation, so
only mounting inside the script's own namespace would keep sandboxes from inheriting
it, which would move the mount failure past the pause-route fallback.

No `forget`/`prune` (the repository is append-only; retention runs elsewhere)
and no notifications. Fallback releases both locks first so the hook can lock. After
restic has run against a snapshot there is no fallback: a failure is a failure.

## Configuration

Host-specific values are not in this repo; the unit sets them:
`BACKUP_HOOK` (the existing pause-route hook), `BACKUP_RESTIC_HOST` (the restic
`--host` the hook uses, so snapshots stay in one lineage) and
`BACKUP_HOOK_LOCK_NAME` (the hook's own lock file name). The script exits 2
without them. Tunables: `BACKUP_SNAP_SIZE_G`, `BACKUP_VG_HEADROOM_G`,
`BACKUP_LOG_DIR`, `BACKUP_RESTIC_ENV_FILE`, `BACKUP_RESTIC_REST_PASSWORD_FILE`.

## Using it as the host ExecStart

`--check` and `--latest-json` are delegated to the hook, so deploy monitoring
keeps working; `--help` never starts a backup; `--retry-check` only repeats the
restic check of a `CHECK-PENDING` result. Install the script under another
name than the hook (it refuses `BACKUP_HOOK` pointing at itself), e.g.
`/usr/local/sbin/host-backup-lvm-snapshot`, and point the unit's `ExecStart` at it.

Outcome files in `/var/log/lvm-snapshot-backup`: `result` (`PASS`, `CHECK-PENDING ...`,
`FAIL ...` or `FALLBACK ...`), `lvm-proof.json` (adds `check_pending` and `snapshot_left`), and one `lvm-snap-<ts>.log` per run.

**The exit status alone is not the verdict.** `CHECK-PENDING` exits 0 (the upload is
good, only the integrity check is outstanding), so a "unit succeeded" signal also covers
a run that still needs `--retry-check`. Monitoring and any proof of a backup must read
the `result` line (anything other than `PASS` needs a look) and the proof's
`check_pending` and `snapshot_left` fields (both must be 0 for a clean run), not only
the exit status.

`snapshot_present` fails closed: only `lvs` exit 5 (volume not found) means the
snapshot is gone. Any other `lvs` failure (a busy lock, a metadata error) counts as
"still present", so the run ends with the `ALERT` / `snapshot_left: 1` outcome and
cleanup refuses to remove a volume whose origin it could not verify, instead of
reporting a clean run.

Tests: `uv run pytest tests/test_host_backup_lvm_snapshot.py` (stubbed tools; no
real volume is touched).

## Install on the production host

The script lives in the repository only; merging the change does not alter the
production host. Until the host owner installs it, the old script keeps running
unchanged.

**The script needs three variables from the unit's environment** and exits 2 without
any of them, so the unit environment does not stay as it is when the old wrapper
hard-coded these values:

| Variable | Meaning |
| --- | --- |
| `BACKUP_HOOK` | Path of the executable the script calls for the pause route, `--check` and `--latest-json` (the existing host backup hook). It must not point at the script itself. |
| `BACKUP_RESTIC_HOST` | The restic `--host` label the hook uses, so new snapshots stay in the same lineage as the existing ones. |
| `BACKUP_HOOK_LOCK_NAME` | File name (not a path) of the hook's own lock file inside the backups directory (`BACKUP_LOCK_DIR`); the script locks it as well as its own lock. |

Set them in a systemd drop-in, for example
`/etc/systemd/system/<backup-unit>.service.d/lvm-snapshot.conf` (generic example values):

```ini
[Service]
Environment=BACKUP_HOOK=/usr/local/sbin/host-backup-hook
Environment=BACKUP_RESTIC_HOST=backup-host
Environment=BACKUP_HOOK_LOCK_NAME=.host-backup-hook.lock
```

Run `systemctl daemon-reload` afterwards. Use the real values of the old wrapper or
hook, not the examples. A plain copy of the new script over an older wrapper that
hard-codes these values, without the drop-in, makes every backup fail at once (exit 2
before anything is backed up).

Steps for the host owner:

1. Keep the old script as `<name>.bak` next to the installed one.
2. Copy the new version to the installed path (root-owned, mode 0755) and set up
   the drop-in above.
3. `bash -n` on the installed script.
4. Do not start a manual backup. The next scheduled backup is the proof: read the
   `result` line and the proof's `check_pending` and `snapshot_left` fields (see the
   outcome files above; the unit exit status alone is not enough).

A run left in `CHECK-PENDING` is finished by running the installed script once with
`--retry-check`.
