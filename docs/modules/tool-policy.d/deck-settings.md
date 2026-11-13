- **Owns:**

  - The policy controller accepts optional presentation functions for Deck labels, grouping and filtering while retaining the same loaded actions, modes and save request.

- **Promises:**

  - Classic markup stays unchanged without the Deck presentation. Deck searches human labels and purpose groups, keeps technical identities in disclosures, and retains retired actions. Filtering keeps its first result visible in the Settings scroller.

- **Tests:**

  - `tests/test_deck_settings.py` enumerates the installed permission catalog and checks search, disclosures and the unchanged save payload. `tests/test_deck_settings_browser.py` checks search visibility and Settings navigation in the real shell.
