- **Owns:**

  - `runner/model_modes_api.py` (the owner-only mode routes, a person's connection routes and the read-only `model_mode` tool) and the account columns of the run record (`mode`, `mode_source`, `account_kind`, `account_label`, `account_person`) on `runner_jobs` and `run_ledger`.

- **Promises:**

  - A start, a chat message and a board session ask `projects.model_modes.decide` before they are admitted; the run record pins the mode and account. Starts and ledger rows from before keep NULL columns and read as no mode, counted as org. `card_start_status` shows each run's mode and account kind; the label is shown only to the person whose account it is. There is no tool or route that lets an agent change a mode.

- **How it fails:**

  - A refused start names the code (`connection_missing`, `machine_not_your_account`, `machine_not_org_account`, `no_requesting_person`, `mode_unavailable`); a chat message gets the same words as a reply, and a queued message whose connection or mode changed is skipped at lease, never moved to another account.

- **Tests:**

  - `tests/test_model_modes.py`, `tests/test_model_modes_runs.py` and the updated public surface inventory.
