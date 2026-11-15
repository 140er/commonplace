## pipeline

- **Paths:** `commonplace/pipeline/`
- **Owns:**

  - The data behind the Pipeline view: events per pull request in the workspace file `pipeline.sqlite3` (`pipeline_prs`, `pipeline_events`, append-only), and the numbers computed from them. `compute.py` is one pure function, `compute_pr`, from a PR's events and a clock to its steps (state, start, end, duration, wait before, attempts), lead time, slowest step, longest wait and current step. `store.py` validates a request whole and appends it in one transaction. `cards.py` reads the board for the card linked to a PR. `demo.py` is the fixed example. `routes.py` serves `POST /api/pipeline/events`, `GET /api/pipeline/prs` and `GET /api/pipeline/prs/<owner>/<name>/<pr>`. `commonplace.pipeline` is one entry of `extensions.MODULES`. Contract and decisions: `docs/system/pr-pipeline-view.md`.

- **Promises:**

  - A write is idempotent by event id (per repository and PR): a known id changes nothing, not even the title. A request is checked whole (known step and state, ISO 8601 time not more than five minutes ahead, at most 200 events, text of at most 300 characters with no secret-looking content) and a refusal stores nothing.

  - Writing needs the deploy script's `pipeline:write` token (the only route it may use) or the signed-in owner; reading needs a signed-in person of the workspace or an agent with `memory:read`. Every call is scoped to the caller's workspace file; a read never creates it.

  - The `card` step is built from the board's linked card (running at its first claim, passed when the PR opened, else when the PR was linked) and never from recorded card events; a PR with no card the reader may see shows "No card" and starts at its first event.

- **How it fails:**

  - A bad request is a plain 400 with one `error` line; a connection that may not use the route is 403; no or a revoked or expired token is 401. A card lookup that fails leaves the recorded events as they are and logs the stack, never the text.

- **Tests:**

  - `tests/test_pipeline_compute.py` (the pure function, its edge cases and the example) and `tests/test_pipeline_api.py` (authentication, the pipeline token, validation, idempotency, workspace isolation, the card step). Both fail on a tree without `commonplace/pipeline/`, which does not exist on the base.
