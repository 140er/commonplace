- **Promises:**

  - The Memory page asks `/api/memory/rooms` and adds each listed wing as a notes-only room next to the project rooms, in both the classic and Deck looks. It reuses the room chips, project filter, search and source panel unchanged; a notes-only room has no chat or cards, so those reads are skipped for it. Agent-saved notes show their exact words.

- **How it fails:**

  - If the listing fails, the page shows the project rooms only, without a warning.

- **Tests:** `tests/test_memory_agent_notes.py` (via `tests/memory_agent_notes.mjs`).
