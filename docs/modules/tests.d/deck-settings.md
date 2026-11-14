- **Owns:**

  - Browser-free and optional-browser coverage for common Settings navigation and grouped action permissions.

- **Promises:**

  - Settings keeps its original shortcuts below the primary sections in the two-column layout; they move focus without discarding form state. Notification labels describe outcomes; permission details share the secondary line. Delayed Voice completion only refreshes the active Settings epoch.

  - The permission inventory enumerates every installed action, including retired actions, and checks label and technical-name search and row titles. Browser checks preserve the former section and form inventory while checking shortcuts, notifications setup guidance and Deck geometry at phone and desktop widths.

  - Repeated arrangements and replacement navigation keep a single scroll listener. The member browser journey keeps Sign out, navigation and personal controls enabled while workspace policy, sharing and management stay disabled.

- **Tests:**

  - `tests/test_deck_settings.py`, `tests/deck_settings.mjs` and `tests/test_deck_settings_browser.py`.
