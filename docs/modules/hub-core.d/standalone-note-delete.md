- **Owns:** The note-delete REST alias and trusted worker origin forwarding.
- **Promises:** The delete alias uses the existing memory:delete tool boundary. Only server-owned context adds the standalone-note operation origin to the authenticated worker identity.
- **How it fails:** Connection, OAuth and pipeline credentials cannot acquire browser-delete authority.
- **Tests:** tests/test_memory_note_delete.py, tests/test_memory_note.py and
  tests/test_memory_note_delete_browser.py exercise the note deletion boundary.
