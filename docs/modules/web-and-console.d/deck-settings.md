- **Owns:**

  - Deck Settings shortcuts, section ordering and permission presentation in `deck-settings.js`; the shared policy controller retains loading and saving.

- **Promises:**

  - Settings keeps its original shortcuts below the primary sections in the two-column layout; they move focus without discarding form state. Notification labels describe outcomes; permission details share the secondary line. Delayed Voice completion only refreshes the active Settings epoch.

  - Notifications, Connected apps and Workspace remain reachable through Settings shortcuts. Every existing section and action remains reachable. Permission search understands purpose labels such as notifications, and technical names stay in row titles. Classic presentation and all permission semantics remain unchanged.

- **Tests:**

  - `tests/test_deck_settings.py` runs the browser-free permission inventory, plain-word search, disclosure and save contract. `tests/test_deck_settings_browser.py` checks the real shell, original sections and forms, keyboard focus, phone gutters and overflow, and optional browser availability.
