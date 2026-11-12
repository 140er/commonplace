- **Owns:**

  - The `card-start` command group (`commonplace card-start resolve`, wired in `commonplace/cli.py`, implemented in `commonplace/runner/owner_cli.py`): the owner's command for a runner start whose outcome is uncertain.

- **Promises:**

  - `card-start resolve --workspace W --job JOB --outcome failed|started [--thread ID] [--by USER]` acts as the owner of the data directory like `orch accept`: `--by` must name a user who owns the workspace (default: the first admin), and the decision is exactly the web tool's (`jobs.resolve`, same audit row).

- **How it fails:**

  - A non-owner, an unknown workspace or job, a start that is not uncertain, a `started` without a valid thread or a `failed` with one exits 2 with a message and leaves the start as it was.

- **Tests:**

  - `tests/test_card_start_resolve_cli.py` runs the command against a real data directory: both outcomes, the audit row, the freed active-start slot and every refusal.
