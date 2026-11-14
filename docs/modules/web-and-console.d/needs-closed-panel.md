- **Promises:**

  - On desktop, a background refresh of Needs me (a changed payload, a new item, the selected item leaving the queue) updates the list and the selection highlight but never reopens a detail panel the user closed; only a pick by the user (click, `j`/`k`, filter change) or an explicit jump to a card (the GitHub return) opens it again. `needs.js` marks the refresh's own reads with `{ background: true }` in the `onSelect` callback and `app.js` keeps the closed state in `state.needsPanelClosed`.

- **Tests:**

  - `tests/commonplace_home_render.mjs` case `closed_panel` (run by `tests/test_commonplace_home.py`) closes the panel, changes the payload and asserts the panel stays hidden while the list and highlight update, then that a pick reopens it.
