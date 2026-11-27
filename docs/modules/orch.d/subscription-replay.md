- **Owns:**

  - `replay.py`: owner-created, immutable replay specifications, pinned live/candidate versions, bounded machine-side execution, append-only attempts and explicit plan-limit resume. `cli.py` starts local experiments; `tools.py` and `rest.py` expose owner reports, promotion and rollback without model execution in the gateway.

- **Promises:**

  - A replay checks five or more known outcomes, runs each side in a separate disposable checkout, applies only declared source files, and evaluates the original tests. It records CLI-reported tokens or explicit unknown usage, elapsed time and fixed failure codes. A stale config cannot inherit another version's results.

  - `benchmark.TaskResult` permits null token/currency metrics. Unknown totals never create a gain, and an unknown currency total cannot satisfy a currency budget. A transaction copies existing benchmark rows before relaxing the old non-null constraint.

- **How it fails:**

  - Plan limits pause without retry or fallback. Missing subscription auth, unsupported config changes, unsafe manifests, altered known outcomes and invalid patches refuse with a persisted reason. Owner accepts recompute the existing decision and protected-key check; rollback restores the exact previous body. All execution workers are joined before returning.
