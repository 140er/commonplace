- **Owns:**

  - `enrollment.py`, `enrollment_api.py` and `enrollment_crypto.py` implement the first installer slice: workspace opt-in, frozen intent, owner issuance/status/revocation, code-only exchange and machine-key-bound encrypted delivery recovery. The existing registry supplies separate credentials in the same control transaction.

- **Promises:**

  - Missing setup settings mean off. Only a current signed-in workspace owner enables setup or issues codes. Bootstrap accepts no session or bearer authority. A 256-bit code is shown once, stored only as a hash and consumed with a conditional update; it expires after five minutes. One outstanding code per workspace/label and request deduplication never return a code again.
  - Redemption freezes workspace, label, manifest digest, settings version, current owner and machine key. Consumption, disabled/unbound machine creation, both credentials, encrypted delivery and audit commit together. No installation, attestation or execution activation occurs.
  - RSA-3072/4096 machine keys receive an RSA-OAEP-SHA256/AES-256-GCM envelope. Recovery uses a single-use, 60-second server challenge signed with RSA-PSS-SHA256 for the exact operation and persisted installation identity, within ten minutes. Acknowledgement, expiry or revocation purges ciphertext. Disabling invalidates all enrollments from this flow and both credentials; re-enabling revives none. Ordinary machine revocation invalidates its delivery in the same transaction.
  - Every bootstrap attempt in an enabled workspace increments a durable workspace/operation counter, capped at ten per minute. Wrong, expired and used codes return one refusal envelope; audit contains fixed outcomes and ids only. A maintenance worker starts only after opt-in and sweeps expiry every minute, with transactional expiry checks on authority use as well.

- **Tests:**

  - `tests/test_runner_enrollment.py` proves clean off/on workspaces, owner/token isolation, one-time secret handling including account exports/files/logs, conditional-consume races, credential/encryption/delivery/audit rollback, key-bound recovery, expiry, kill switches and account purge. Existing owner/identity tests preserve manual runner behavior.
