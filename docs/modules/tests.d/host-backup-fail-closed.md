- **Owns:**

  - The `lvs` stub of `tests/test_host_backup_lvm_snapshot.py` can fail with a non-5 exit (`LVS_TRANSIENT`) while the volume exists, to prove a failing `lvs` is never read as "the snapshot is gone"; still no real volume, repository or host.
