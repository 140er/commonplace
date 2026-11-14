- **Owns:**

  - The Memory search request plan in `commonplace/web/static/memory.js` (`searchMemory`, the search controller) and its loading, error and "Show more" states in the Classic and Deck renderers.

- **Promises:**

  - One query sends one note request to the palace, for every readable room at once with a limit of 25, plus one cheap chat request per readable project. At most two requests are in flight. Each note is attributed to its room from its own wing, and rows from a wing the page has no room for are not shown. Choosing one room scopes the note request to that room.
  - "Show more" repeats the same query with a larger limit (50, then 100). Every row shown is a returned drawer, word for word.
  - A new query, leaving Memory, or opening the map aborts the request in flight. Deck search also runs after a short pause in typing; each pause sends at most one search and cancels the older one.
  - A search shows a loading line while it runs and always ends within 20 seconds. A search that found nothing because a source failed or timed out shows an error with a Try again button, never "No exact words match"; when only some sources failed the found rows stay with a Try again link.

- **How it fails:**

  - A slow or unreachable palace gives a plain "taking too long" message and a retry after the deadline; chat results that arrived in time are still shown. The room listing waits at most three seconds before the search starts without it.

- **Tests:** `tests/test_workbench_memory.py` (request count, limit, abort, timeout and retry, empty versus error, loading, show more, partial failure), `tests/test_deck_memory.py` (typing pause and abort), `tests/test_memory_agent_notes.py`, and optional `tests/test_deck_memory_browser.py` (one note request per query; a stuck search ends in an error with Try again).
