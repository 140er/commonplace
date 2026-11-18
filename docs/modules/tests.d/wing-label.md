- **Owns:** Wing-label regressions in tests/memory_note.mjs and the import wiring
  in tests/memory_map_restore.mjs, tests/deck_memory.mjs and
  tests/admin_runners_ui.mjs.
- **Promises:** An unnamed project_blue_eye displays Blue Eye in the composer
  and saved confirmation, while POST /api/memory still sends project_blue_eye.
  An explicit board name keeps its spelling and case.
- **How it fails:** Raw ids shown as names or rewritten submitted destinations fail
  assertions. Isolated harnesses load the same label helper as the browser.
- **Tests:** tests/test_memory_note.py, tests/test_memory_map_restore.py tests/test_deck_memory.py and tests/test_admin_runners_ui.py execute these JavaScript harnesses.
