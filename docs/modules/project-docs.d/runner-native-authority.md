- **Owns:**

  - Native runner authority design and explicit separation of setup checks from execution capability.

- **Promises:**

  - The inspected native sources, owner-selected isolated authority, pending privileged provisioning, independent issuer/hub pins, versioned scope and required live proof are identified before implementation.

- **How it fails:**

  - Missing native enforcement or job/key separation blocks productive readiness; a wrapper, login or synthetic signature cannot replace it.

- **Tests:**

  - tests/test_docs_obligations.py, tests/test_docs_fragments.py; existing host-boundary regressions remain applicable.
