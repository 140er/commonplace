- **Owns:**

  - Native runner authority design and explicit separation of setup checks from execution capability.

- **Promises:**

  - Native authority responsibilities, isolated custody, privileged provisioning boundaries, independent issuer/hub pins, versioned scope and required live proof are defined before implementation.

- **How it fails:**

  - Missing native enforcement or job/key separation blocks productive readiness; a wrapper, login or synthetic signature cannot replace it.

- **Tests:**

  - tests/test_docs_obligations.py, tests/test_docs_fragments.py; existing host-boundary regressions remain applicable.
