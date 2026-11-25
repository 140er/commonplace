- **Owns:**

  - Round 5 behaviour of the cloud workspaces section (`compute-workspaces.js`, `compute.js`, `compute.css`) and its Deck pages (`deck-shell.js`, `deck-workspace.js`, `deck-compute.js`, `deck-compute-scene.js`): the no-machine state (a disabled Machine field reading "No machines set up yet" and one explaining sentence, no instruction that cannot be followed), one machine label and one tone per status, states a start would produce worded as what a machine recorded, "Starting is not available yet" once per screen, a refused discard that updates the row status, stale and leave rows that wrap on a narrow phone, live regions written only when their text changes, the ready-made image error on the visible list, focus kept across a background reload, plainer wording on the custom image path, and the Deck entry and page called "Cloud workspaces" with a Map line, the route card on the Map tab and no tab bar for a member.
- **Promises:**

  - No screen says something started, failed or is up to date as a fact of the page while the page says nothing starts; a screen without a machine offers nothing it cannot do; nothing clips at 390px or 320px; a member never sees a Management tab.
- **How it fails:**

  - An unreadable stored image shows as "Image unreadable" with the way to fix it; a refused discard says in its own status that the record changed.
- **Tests:** `tests/compute_workspaces_ui.mjs` (no machines, once per screen, refused row, status tone, background reload focus, curated image error) and `tests/test_compute_workspaces_browser.py` (narrow stale and leave rows, Deck naming and route card, silent typing, the image error on the list).
