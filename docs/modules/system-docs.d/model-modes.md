- **Owns:**

  - The model modes design in `docs/system/model-modes.md`: mode semantics and precedence, whose account a run uses, spend attribution per mode, the missing-connection rule, security, migration, the effect preview, limits, failure states and the three-slice plan (card #617).

- **Promises:**

  - The note is design only and changes no behaviour: it records the gaps on develop, the precedence matrix, what each run shows (mode, model, account label, never a secret value), that personal-account spend stays out of org budgets by default, and the open questions with their defaults.

- **How it fails:**

  - A design note can go stale against later code; each slice updates it and reviewers judge its truth, since `docs-check` is mechanical.

- **Tests:**

  - Documentation obligations (`tests/test_docs_obligations.py`) and fragment validation keep the note and its module fragment well-formed.
