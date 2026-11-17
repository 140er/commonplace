- **Owns:** tests/test_memory_note.py, tests/memory_note.mjs and
  tests/test_memory_note_browser.py for standalone workspace notes, plus the
  standalone-wing search contract in tests/memory_agent_notes.mjs.
- **Promises:** The real API checks Unicode and long-text round trips, default and
  picked destinations, immediate search, validation, scopes, forbidden wings and the
  free-space floor. The harness checks retained drafts, explicit retries, duplicate
  submission locks and stopped-controller fencing. Browser cases cover both looks,
  owner and member sessions, and 1280/390 px widths with optional screenshots and
  exact strings and control positions; a baseline-static option captures prior UI.
- **How it fails:** Missing composer, route reuse or permission checks fail assertions;
  missing optional browser dependencies skip only browser cases.
- **Tests:** tests/test_memory_note.py wraps the JavaScript harness; the API and browser
  cases use isolated HTTP fixtures and never target a running service.
