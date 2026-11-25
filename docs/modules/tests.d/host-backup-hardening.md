- **Owns:**

  - Host backup script regressions (`tests/test_host_backup_lvm_snapshot.py`) run the real shell script against stub `restic`, `lvs`, `lvremove`, `cryptsetup` and mount tools on a temporary directory, with a fake process table for the holder search; no real volume, repository or host is touched.
