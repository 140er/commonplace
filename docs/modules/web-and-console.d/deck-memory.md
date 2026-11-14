- **Promises:**

  - Deck Memory presents readable rooms and author display names above the unchanged source actions. Quotes preserve the original characters, including whitespace; source identifiers stay in a closed details disclosure.
  - Room chips use only the server-provided readable board inventory and the existing project filter. Search endpoints, concurrency, member scopes and source-action authority are unchanged.
  - Deck queries are saved in session storage per person and workspace. Only restoring a nonblank saved query shows “Kept from last time”; editing or submitting clears the note. Storage refusal does not prevent search.
  - The Deck import dialog retains choose → preview → confirm → report and identical upload bytes, destinations and retry semantics. Import requires the explicit ownership checkbox after preview; changing files resets consent. Classic markup and import behavior stay unchanged.

- **Tests:** `tests/test_deck_memory.py`, `tests/test_workbench_memory.py`, `tests/test_memory_import_ui.py`, and optional `tests/test_deck_memory_browser.py` (desktop/phone, owner/member, real scoped reads and import writes).
