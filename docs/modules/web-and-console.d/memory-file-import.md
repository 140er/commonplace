- **Owns:**

  - Memory offers a file chooser, count and destination preview, explicit confirmation, progress and a retryable report through the existing session. The importer clears file contents on cancel, completion or navigation and uses the surrounding design tokens.
  - Deck uses rounded input wells, a keyboard accessible file pill, plain retry wording and a compact result group. Its phone actions share the content gutter and scroll clear of the tabs. Classic rendering remains unchanged.

- **Tests:**

  - `tests/test_memory_import_browser.py` exercises real owner/member file selection, preview cancellation, progress, report and a lost-response retry at desktop and phone sizes.
