- **Tests:**

  - `tests/deck_compute_capy.mjs`, wrapped by `tests/test_deck_compute_capy.py`, checks that under the Deck presentation a device with more than three link ends draws a count chip with the count in its label (desktop and phone), that three links or no link targets draw none, that the count stays whole while a link is selected, and that no end marks appear. `tests/test_deck_compute_layout_browser.py` no longer expects a failure for the Compute phone route, and `tests/test_deck_compute_capy_browser.py` expects the runner's count of four.
