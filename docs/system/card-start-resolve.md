### Owner resolution of an uncertain runner start

- A runner start whose outcome is uncertain counts as in flight and holds the machine's single slot until a workspace owner resolves it; the web tool `card_start_resolve` does that for a signed-in owner and `commonplace card-start resolve --workspace W --job JOB --outcome failed|started [--thread ID]` does the same for the owner of the data directory, through the same `jobs.resolve` and audit row.
- Resolve as `failed` only when no thread was launched; resolve as `started` with the thread's id when one exists. Nothing resolves an uncertain start automatically, because a launch may really have happened.
