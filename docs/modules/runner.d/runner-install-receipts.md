- **Owns:**

  - Signed, owner-approved installation snapshots and machine-self fetch; private receipt verification and compatibility-cache sync in the setup-only service and runner check.

- **Promises:**

  - Only current owner-attested bindings produce acknowledgements. Fresh challenges, identity checks, signatures and authority versions fence stale or swapped receipts; cache edits are rejected and re-fetched. Setup remains execution-disabled.

- **Tests:**

  - tests/test_runner_install_receipts.py covers HTTP authority, signatures, tampering, narrowing, setup service/check, private transport and out-of-grant starts; existing runner/binding checks remain required.
