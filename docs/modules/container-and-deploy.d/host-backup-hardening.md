- **Owns:**

  - `deploy/backup/host-backup-lvm-snapshot.sh` hardening: stale restic locks cleared before the check, a distinct CHECK-PENDING result with `--retry-check`, holder logging and bounded late cleanup retries for a busy snapshot, an ALERT for any snapshot left behind, and a refusal to start while an earlier run's snapshot exists.

- **Promises:**

  - A good upload whose `restic check` only finds the repository locked is `CHECK-PENDING` (exit 0, proof `check_pending`), not a failed backup, and `--retry-check` finishes it without a new backup. Only a plain `restic unlock` runs (stale locks only): never `--remove-all`, forget or prune.
  - A run never ends with its own snapshot present and no alert: an `ALERT` log line, `FAIL ALERT` result, `snapshot_left` in the proof and exit 1, even after a pause-route fallback. A leftover snapshot of the script's own name pattern blocks the next backup with the same alert and is never deleted by the guard.

- **How it fails:**

  - Holders keeping the mapper busy are logged and removal is retried a bounded number of times; the snapshot is then kept and alerted, never forced. A listing failure of the volume group falls back to the pause route.

- **Tests:**

  - `tests/test_host_backup_lvm_snapshot.py` covers the lock, retry, holder, alert and leftover-guard cases with stubbed restic, lvm and mount tools.
