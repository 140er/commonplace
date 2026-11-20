- **Owns:**

  - `projects/model_modes.py`: the mode record (`org`, `personal`, `hybrid`, no record = `unset`), a person's connections, the `resolve_account` precedence matrix, provider-independent account snapshots and the person's own Needs me items (card #617, `docs/system/model-modes.md`).

- **Promises:**

  - The project record wins over the workspace record as a whole; a person's own account is used only in personal mode or in a hybrid project that allows it and where the person switched it on; a machine behind a connection serves only its person, in every mode. Labels of a person's account are shown to that person only. `models_effective` of the project view carries the account per role for the caller.

- **How it fails:**

  - A mode or connection store that cannot be read raises `ModeUnavailable`: nothing is admitted on a guess. A missing connection in personal mode is a refusal with a reason and a next step, never the org account.

- **Tests:**

  - `tests/test_model_modes.py` (the precedence matrix, private machines, owner-only setting, connections, chat), `tests/test_model_modes_runs.py` (card starts) and `tests/test_escalation_authority.py` (final-provider resolution from the snapshot).
