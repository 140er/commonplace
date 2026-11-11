## memory-and-search

- **Paths:** `commonplace/memory_tools.py`, `commonplace/search_index_api.py`, `commonplace/store_embed.py`, `commonplace/store_switch.py`, `commonplace/workspace_store.py`, `commonplace/capture.py`, `commonplace/capture_client.py`
- **Owns:**

  - Memory tools and routes under the caller's policy, search index progress, workspace store generations and cutovers, and opt-in chat capture.

- **Promises:**

  - Saved-but-unlinked private notes retain their unreadable-author notice in the error response.

  - Health-flagged notes remain private. HTTP and MCP saves explicitly tell an unreadable author that the note was saved privately and cannot be read for now.

  - Owner memory maps retain readable person relationships when a citation cannot be proved.

  - Owner HTTP progress always forwards the authenticated principal, returns only readable progress and disables response caching.

  - Reads and writes respect the caller's wings and scopes; store cutovers are backup-gated and restartable; capture is explicit and append-only.

- **How it fails:**

  - Missing, deleted and unreadable citation targets omit memory links without failing the map.

  - Incomplete progress proof returns the fixed 503 error without partial totals or private failure details.

  - An interrupted cutover keeps the previous store live; an index rebuild degrades search speed, not stored text.

- **Tests:**

  - Memory-map regressions compare hidden and absent citation targets and retain readable fact text.

  - `tests/test_card253_s12.py` covers authenticated HTTP isolation and failures; `tests/test_search_index_progress.py` covers the route contract.

  - When only the steady untrusted write limit refuses a capture or import write, a bounded first-backfill allowance is spent instead: per connection once (closing after its window, never renewed) and per workspace (renewing only after its period), never keyed by stream, so restarts and new streams cannot re-arm it. The per-minute budgets still apply and are refunded on refusal; other routes and trusted connections keep their limits. `tests/test_commonplace_public_surface.py::test_first_backfill_of_realistic_size_is_not_throttled_by_the_steady_limit` is red on `origin/develop`.

  - `tests/test_commonplace_memory.py`, `tests/test_commonplace_capture.py`, `tests/test_workspace_store_switch.py`.
