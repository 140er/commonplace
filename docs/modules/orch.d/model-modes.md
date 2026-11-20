- **Owns:**

  - The account of a config-driven start: `runner_bridge.prepare` captures account and machine authority; the fenced start resolves it after final model selection and pins the account and eligible machine on the run.

- **Promises:**

  - A start with no requesting person belongs to the person who accepted the config version; the launch reply carries the mode and account kind, never the label of a person's own account. A provider escalation uses the final provider's connection and machine, with the same requester and grant fences and no account reads inside the start transaction.

- **How it fails:**

  - Missing, revoked or ineligible final authority refuses before any job or ledger row is written. A missing-account item names only the final provider, after the refused transaction ends.

- **Tests:**

  - `tests/test_model_modes_runs.py`, `tests/test_escalation_authority.py` and `tests/test_escalation_authority_probe.py`.
