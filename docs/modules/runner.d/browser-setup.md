- **Owns:**

  - Browser request and host access records, machine-key status proofs, the private setup client and a bounded subscription device-login process.

- **Promises:**

  - The enrollment owner decides through a person session; exact scope, key, expiry and versions fence login, delivery and final attestation. Unsupported remote login stays incomplete. Installation never enables execution.
  - The access secret persists in plaintext only in the machine's 0600 file. Durable acknowledgement purges the encrypted relay. Revoke and setup disable invalidate decisions, status and access verifiers together.

- **Tests:**

  - tests/test_runner_setup_approvals.py and tests/test_runner_setup_contract.py cover clean setup, consent isolation, replay, expiry, delivery, secret scans and fingerprint confirmation alongside the earlier installer and receipt tests.
