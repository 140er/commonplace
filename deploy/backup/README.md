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
7. Cleanup (always, through an EXIT trap): umount, `sync` and `udevadm settle`,
   `cryptsetup close` retried up to 30 times 2 s apart (the mapper can stay "in
   use" for a while after umount), a last `dmsetup remove --retry` on this run's
   mapper, then `lvremove` of this run's snapshot (up to 5 attempts). Only when
   close or lvremove still fail after all that does cleanup fail the run; a good
   backup plus check followed by a retried-but-successful cleanup is PASS. The
   snapshot name must start with the reserved prefix `nightlysnap` and the LV
   must be a snapshot of `srv`; `data/srv` and the `srv` mapper are never
   touched. The snapshot is kept if the mapper cannot be closed.
   Tunables: `BACKUP_CLOSE_ATTEMPTS`, `BACKUP_CLOSE_RETRY_SLEEP_S`,
   `BACKUP_LVREMOVE_ATTEMPTS`, `BACKUP_LVREMOVE_RETRY_SLEEP_S`.

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
keeps working; `--help` never starts a backup. Install the script under another
name than the hook (it refuses `BACKUP_HOOK` pointing at itself), e.g.
`/usr/local/sbin/host-backup-lvm-snapshot`, and point the unit's `ExecStart` at it.

Outcome files in `/var/log/lvm-snapshot-backup`: `result` (`PASS`, `FAIL ...` or
`FALLBACK ...`), `lvm-proof.json`, and one `lvm-snap-<ts>.log` per run.

Tests: `uv run pytest tests/test_host_backup_lvm_snapshot.py` (stubbed tools; no
real volume is touched).
