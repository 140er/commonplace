- **Owns:**

  - `tests/test_subscription_replay.py` uses fake binaries on PATH for every adapter, real disposable git histories and real narrow Python checks. It covers subscription refusal, environment/flag guards, unknown metrics, nullable migration, plan pause/reopen/resume, stale configs, immutable tests, bounded concurrency and the owner CLI accept/rollback journey.

  - `tests/test_orch_surfaces.py` covers owner-only replay reports and promote/rollback REST aliases, cross-board lookup refusal, anonymous/agent denial, and registration/visibility of the three owner tools.

  - Replay regressions also cover ambiguous Grok plan responses, local owner recovery of a hard interruption without model repeat, and read-only construction of reports on migrated stores.
