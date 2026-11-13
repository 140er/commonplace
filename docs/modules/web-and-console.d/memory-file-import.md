- **Owns:**

  - Memory offers a file chooser, count and destination preview, explicit confirmation, progress and a retryable report through the existing session. The importer clears file contents on cancel, completion or navigation and uses the surrounding design tokens.

- **Tests:**

  - `tests/test_memory_import_browser.py` exercises real owner/member file selection, preview cancellation, progress, report and a lost-response retry at desktop and phone sizes.
