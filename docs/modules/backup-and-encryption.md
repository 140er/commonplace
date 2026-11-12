## backup-and-encryption

- **Paths:** `commonplace/backup_status.py`, `commonplace/backup.py`, `commonplace/backup_online.py`, `commonplace/backup_supervisor.py`, `commonplace/backup_pause.py`, `commonplace/admission.py`, `commonplace/workspace_encryption.py`, `commonplace/workspace_keyslots.py`
- **Owns:**

  - Offline and online verified backups and restores of the data directory, the gateway pause socket and run fence, the admission gate for store/file creators and the successor registry, the restore marker, and opt-in workspace SQLite encryption.

- **Promises:**

  - backup_status.py reads fixed no-follow/nonblocking regular files up to 16 KiB with strict schema. CLI-only 0600 atomic publication never changes snapshot/fence outcomes; SIGKILL may retain prior receipt and upload is unverified. test_backup_status.py covers privacy and atomic faults.

  - A backup copies every SQLite file with a manifest; an online backup runs beside the serving gateway without stopping or replacing it and publishes only after the run is committed.

  - Its enumeration tolerates SQLite sidecars disappearing during commit or connection cleanup, while missing user files and unsafe paths still fail closed.

  - Online writer scans exempt the gateway's ancestor chain from the same `/proc` tree, alongside the backup's own ancestry; unrelated writers remain refused even with identical supervisor arguments.

  - A restore publishes a verified copy into a new directory and never overwrites; the restore marker blocks config-driven work until the owner acknowledges.

  - workspace_keyslots.py checks a keyslot credential against its slot and the workspace databases without writing, and re-wraps the unchanged data key for one slot under a new credential: offline, owner-authorized, atomic, other slots kept byte for byte. The recovery slot can re-create a lost server slot. Output and errors carry no key bytes. Runbook: `docs/recovery/encrypted-workspace.md`.

- **How it fails:**

  - A keyslot re-wrap refuses a wrong unlock credential, a new credential that already opens a slot, a key that opens no database, an unknown record format and a running service; every refusal and an interrupted publish leave the previous record in place. It is not revocation: the data key does not change.

  - Receipt publication faults emit a fixed diagnostic and preserve the backup result; unsafe or noncanonical records fail independently per mode without paths or raw exception text.

  - A damaged backup is refused at verify time; an online run that loses its lease, heartbeat or fence ends invalid and publishes nothing; a live in-container successor refuses the run; a hand-copied restore carries no marker (residual in `SYSTEM.md`).

- **Tests:**

  - `tests/test_backup_status.py` covers atomic replacement faults, backup independence and hook exit-2 alias rejection; `tests/test_admin_instance_api.py` observes no-follow/nonblocking flags and per-mode reader faults.

  - `tests/test_backups.py`, `tests/test_backup_snapshot_deadline.py`, `tests/test_commonplace_w5_backup.py`, `tests/test_backup_online.py`, `tests/test_backup_online_enumeration.py`, `tests/test_backup_hook_online.py`, `tests/test_backup_pause.py`, `tests/test_backup_cli_fence.py`, `tests/test_admission_gate.py`, `tests/test_orch_store.py`.

  - `tests/test_workspace_encryption.py` covers migration, fail-closed opens, the keyless byte-copy backup and restore, and a recovery-slot reopen; `tests/test_workspace_keyslots.py` covers the keyslot check, credential rotation, the restore-then-recreate-server-slot drill, each refusal with its message, and that the CLI prints no key material.
