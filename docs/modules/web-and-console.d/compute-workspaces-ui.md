- **Owns:**

  - `commonplace/web/static/compute-workspaces.js` and its section in `compute.js`/`compute.css`: the "Cloud workspaces" section of the Compute management screen, shared by Classic and the Deck Management tab. It lists records with a plain status, shows a form (name, image choice or own reference, CPU, memory in GiB, storage, network, machine) with a live resource preview and inline validation, a review step before confirm, an edit form with the restart note, a details view and a registry-login reference form. Members see the list and details read-only.
- **Promises:**

  - Every state says what it is: loading, empty (owner and member), failed load with a reload button, image list unavailable (creation disabled with the reason), each status with its sentence, and "Not started yet" until the lifecycle exists. Nothing is sent before the confirm step. Errors from the server land beside their field. All user text is escaped; there are no inline styles; targets are at least 44px on the phone and the page never scrolls sideways at 390px; focus moves to the form heading on open and back to the list on close.
- **How it fails:**

  - A failed or unanswered save keeps the person's values and says whether anything is confirmed; a 401 ends the visit like the rest of Compute; a 403 for the owner inventory still shows the read-only list.
- **Tests:** `tests/compute_workspaces_ui.mjs` (via `tests/test_compute_workspaces_ui.py`) and `tests/test_compute_workspaces_browser.py` (Classic and Deck, desktop and phone, owner and member).
