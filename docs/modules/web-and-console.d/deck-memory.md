- **Promises:**

  - Deck Memory presents readable rooms and author display names above the unchanged source actions. Quotes preserve the original characters, including whitespace; source identifiers stay in a closed details disclosure.
  - The quiet search field submits with Enter, keeps its native clear control, and uses a focus-visible ring. Room-chip counts come only from returned readable matches; the same query keeps its known per-room counts during room filtering. Display names replace room slugs and saver roles; the viewer is shown as “you”. Phone import is after the list.
  - Room chips use only the server-provided readable board inventory and the existing project filter. Search endpoints, concurrency, member scopes and source-action authority are unchanged.
  - Deck queries are saved in session storage per person and workspace. Only restoring a nonblank saved query shows “Kept from last time”; editing or submitting clears the note. Storage refusal does not prevent search.
  - On phones, all room chips and the final More filters chip share one scroll row with a 16px right fade; the result count sits below at the left. Import dialogs use the opaque background at both sizes, independently of card translucency. Desktop chooser intro gaps are 20px; preview retry copy refers to importing the same file “here”.
  - The Deck import dialog retains choose → preview → confirm → report and identical upload bytes, destinations and retry semantics. Import requires the explicit ownership checkbox after preview; changing files resets consent. Classic markup and import behavior stay unchanged. The chosen export label survives preview; import actions follow reading order, and a custom checkbox changes presentation only.

- **Tests:** `tests/test_deck_memory.py`, `tests/test_workbench_memory.py`, `tests/test_memory_import_ui.py`, and optional `tests/test_deck_memory_browser.py` (desktop/phone, owner/member, real scoped reads and import writes).
