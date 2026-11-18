- **Owns:** Classification of the owner-browser note-delete REST route.
- **Promises:** POST /api/memory/<id>/delete is exempt from connection instruction freshness because it requires an owner browser session and drawer-bound CSRF proof.
- **How it fails:** The exemption does not grant token access or bypass memory:delete policy.
- **Tests:** tests/test_memory_note_delete.py, tests/test_memory_note.py and
  tests/test_memory_note_delete_browser.py exercise the note deletion boundary.
