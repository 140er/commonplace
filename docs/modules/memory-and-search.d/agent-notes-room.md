- **Owns:**

  - `GET /api/memory/rooms` (`note_rooms` in `commonplace/memory_tools.py`): the wings the Memory page searches that no project owns. Today that is the inbox wing, where every agent connection saves, listed as "Agent notes".

- **Promises:**

  - The room list obeys the caller's own read check: the inbox is listed only when memory search is allowed and the wing is readable and not private. A wing-scoped token, a connection without memory read and a caller in another workspace (its own palace) get no room and no trace of the notes. Where a project already owns the wing, no extra room is added.
  - Agents still write the same wing the same way; the route only reads, and notes stay verbatim.

- **How it fails:**

  - A refused or failed room listing leaves the project rooms working; the Agent notes room is simply absent.

- **Tests:** `tests/test_memory_agent_notes.py` (server listing, read-scope and workspace negatives, node harness for the page).
