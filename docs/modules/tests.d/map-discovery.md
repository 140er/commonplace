- **Owns:**

  - `tests/test_topology_discovery.py` runs map discovery through the real gateway with two fake runner machines and invented system facts (a `Probe` made of dictionaries that records every read, command and connection), and holds the read-only checks of the collector and its prompt. `tests/topology_discovery_ui.mjs` (run by `tests/test_topology_discovery_ui.py`) covers the start button's outcome lines and its wiring on both screens.
