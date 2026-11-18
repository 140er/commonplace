- **Tests:**

  - `tests/test_memory_organise.py` (real hub and palace) covers the organise routes: history kept on end and replace, preview and cancelled or wrong confirm changing nothing, a denied connection, verbatim stored text, empty states, links and the guarantee that no stored text changes. `tests/organise_page.mjs` (run by `tests/test_organise_page_ui.py`) covers the page rendering, escaping, empty and unknown states, denied and failed reads, and the review, cancel, refuse and confirm flow. `tests/test_commonplace_public_surface.py` lists the new routes.
