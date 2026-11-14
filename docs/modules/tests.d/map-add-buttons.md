- **Owns:**

  - `tests/test_topology_add.py` runs "Add machine" and "Add site" through the real gateway with fake runner machines (the map discovery tests' world), and holds the check that the prompts contain nothing specific to a setup. `tests/topology_add_ui.mjs` (run by `tests/test_topology_add_ui.py`) covers the buttons' requests, the copied prompt and its fallback; the two buttons' wiring on the Compute screen is in `tests/commonplace_deck_compute.mjs`.
