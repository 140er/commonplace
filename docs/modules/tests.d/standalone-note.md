- **Owns:** tests/test_memory_note.py, tests/test_memory_note_loading.py, tests/memory_note.mjs and
  tests/test_memory_note_browser.py for standalone workspace notes, plus the save
  response fixture in tests/test_commonplace_rules_enforce.py.
  The VM map harness supplies the note module imported by the Memory controller.
- **Promises:** The real API checks Unicode and long-text round trips, default and
  picked destinations, immediate search, validation, scopes, forbidden wings and the
  free-space floor. The harness checks retained drafts, explicit retries, duplicate
  submission locks and stopped-controller fencing. Browser cases cover both looks,
  owner and member sessions, and 1280/390 px widths with optional screenshots and
  exact strings and control positions; a baseline-static option captures prior UI.
  Literal dollar replacement patterns, HTML characters, quotes, newlines and emoji
  retain identical textarea, preview and posted bytes after redraws in both looks.
  MCP acceptance below the shared limit preserves its reply and stored text, and
  save fixtures follow the five-value result used by every production consumer.
  A 30-second taxonomy stub proves opening stays below one second without a worker
  call. Optional room tests cover deadlines, late-answer rejection, cache hits and
  expiry, workspace and connection isolation, unavailable memory and forbidden wings.
  Harness cases distinguish loading and saving errors and fence stale room choices.
  Memory Map wing discovery is checked for a short propagated deadline.
  Browser error cases capture the prior loading message, the corrected message,
  Try again and failed-save draft retention in both looks at 1280/390 px.
- **How it fails:** Missing composer, route reuse or permission checks fail assertions;
  missing optional browser dependencies skip only browser cases.
- **Tests:** tests/test_memory_note.py wraps the JavaScript harness; the API and browser
  cases use isolated HTTP fixtures and never target a running service.
