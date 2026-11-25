- **Owns:**

  - The fail-closed snapshot presence check of `deploy/backup/host-backup-lvm-snapshot.sh` and the host install notes in `deploy/backup/README.md` (the three required unit variables, reading the `result` line instead of the exit status).

- **Promises:**

  - Only `lvs` exit 5 (volume not found) means the temporary snapshot is gone; any other `lvs` failure counts as "still present", so the run ends in the `ALERT` / `snapshot_left` outcome and cleanup never removes a volume whose origin it could not verify.
  - The README states that `CHECK-PENDING` exits 0 (monitoring reads `result`, `check_pending` and `snapshot_left`) and that the script needs `BACKUP_HOOK`, `BACKUP_RESTIC_HOST` and `BACKUP_HOOK_LOCK_NAME` from the unit environment, with a systemd drop-in example.

- **How it fails:**

  - A transient `lvs` error is reported as a left-behind snapshot (exit 1) rather than a clean run; a plain copy over an older wrapper without the three variables exits 2 before any backup, which the README warns about.

- **Tests:**

  - `tests/test_host_backup_lvm_snapshot.py` covers absent, present and transiently failing `lvs` with a stub.
