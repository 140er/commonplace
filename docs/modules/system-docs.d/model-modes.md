- **Owns:**

  - The model modes design in `docs/system/model-modes.md`: mode semantics and precedence, whose account a run uses, spend attribution per mode, the missing-connection rule, security, migration, the effect preview, limits, failure states and the three-slice plan (card #617).

- **Promises:**

  - The note records the design and the slice as built: the precedence matrix, what each run shows, credential privacy, planned spend attribution, open questions and defaults. It names the admission transaction window, the existing run-status fields and the Needs me failure behaviour without claiming later slices have shipped.

- **How it fails:**

  - A design note can go stale against later code; each slice updates it and reviewers judge its truth, since `docs-check` is mechanical.

- **Tests:**

  - Documentation obligations (`tests/test_docs_obligations.py`) and fragment validation keep the note and its module fragment well-formed.
