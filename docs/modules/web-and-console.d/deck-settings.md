- **Owns:**

  - Deck Settings shortcuts, section ordering and permission presentation in `deck-settings.js`; the shared policy controller retains loading and saving.

- **Promises:**

  - Notifications, Connections and Workspace remain one shortcut away from Settings. Every existing section and action remains reachable. Permission search understands purpose labels such as notifications, and technical names stay inside disclosures. Classic presentation and all permission semantics remain unchanged.

- **Tests:**

  - `tests/test_deck_settings.py` runs the browser-free permission inventory, plain-word search, disclosure and save contract. `tests/test_deck_settings_browser.py` checks the real shell, original sections and forms, keyboard focus, phone gutters and overflow, and optional browser availability.
