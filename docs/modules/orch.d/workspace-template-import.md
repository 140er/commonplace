- **Owns:**

  - `local_principal` in `commonplace/orch/cli.py` builds the CLI's local owner principal for `orch` commands and for the workspace template import, which files its roles and skills only as a proposed config through `Store.propose` (see workspace-templates).
