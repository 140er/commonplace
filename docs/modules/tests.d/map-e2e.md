- **Owns:**

  - The Compute map proof (`tests/test_topology_end_to_end.py`, `tests/test_topology_generic_guard.py`): the end-to-end path on a clean workspace through the real hub with synthetic runners, proxy config and DNS, and the guard that scans the topology code, prompt, collector, web files, docs, tests and fixtures for real-setup details (derived from structure, with a planted-violation self-test). Both reuse the fixtures of the topology discovery and site tests.
