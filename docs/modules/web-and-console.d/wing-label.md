- **Owns:** Display-only wing labels in commonplace/web/static/wing-label.js,
  shared by memory-note.js, memory.js, deck-memory.js, agents.js and adminrunners.js.
- **Promises:** Named boards keep their display names. Unnamed wing labels strip
  project_, replace underscores with spaces and title-case words. shared displays
  Workspace notes. Option values, filters and submitted wing ids remain unchanged.
- **How it fails:** Missing board names use the wing label without changing storage
  or permissions. Labels are escaped at the existing HTML rendering boundaries.
- **Tests:** tests/test_memory_note.py, tests/test_memory_map_restore.py and
  tests/test_admin_runners_ui.py cover the shared displays and unchanged requests.
