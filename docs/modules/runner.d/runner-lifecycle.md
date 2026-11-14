- **Owns:**

  - Signed setup lifecycle, bounded supervision, host issuer verification and explicit deployment trust.

- **Promises:**

  - Only the enrolled key can renew setup or acknowledge stop. Revocation invalidates every authority before requesting termination. The supervisor stops owned processes on denial or loss; a stop acknowledgement follows confirmed termination. Issuer signatures require an independently configured pin and exact current scope. Execution stays disabled until compatible live issuer and provider proofs exist.

- **How it fails:**

  - Unknown pins, stale scope, replay, blocked replies, missing trust or lost authority stop setup. A revoked or offline machine remains stop unconfirmed until its supervisor acknowledges; no hostile or unreachable operating-system kill is claimed.

- **Tests:**

  - tests/test_runner_setup_lifecycle.py, existing enrollment, installer, receipt, binding and revocation checks.
