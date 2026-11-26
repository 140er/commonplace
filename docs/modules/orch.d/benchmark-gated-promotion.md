- **Owns:**

  - `commonplace/orch/benchmark.py` and the `orch_benchmarks`, `orch_benchmark_results` and `orch_promotions` tables of `schema.sql`: the replay-result record, the deterministic promote decision, the protected-key guard, and owner promote and rollback (`tests/test_orch_benchmark.py`).

- **Promises:**

  - `decide` promotes only when the success rate is at least live's, no live-passing task fails on the candidate and one metric is strictly better; it is pure and independent of result order. Stored results are append-only, and promote recomputes the decision from them.

  - Promote and rollback are owner-only, write new live versions (history is never edited), and rollback restores the replaced body and sha256 exactly. A candidate touching `limits`, `runner`, `triggers`, `fleet`, `factory`, `mcps`, a role's structure, a reviewer or gate role, or a gate, review or verification workflow step is refused.

- **How it fails:**

  - `not_a_win`, `stale_benchmark` (live or the candidate changed since), `protected_key`, `sha256_mismatch`, `invalid_results`, `nothing_to_roll_back` and `stale_live` refuse without writing; the tables are created idempotently on open, so an older store gains them and keeps its rows.

- **Tests:**

  - `tests/test_orch_benchmark.py` covers win, tie, regression and protected-key refusals, the promote and rollback round trip over five recorded fake replay results, and the migration of an older store.
