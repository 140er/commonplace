- **Owns:**

  - The Compute integration tests (`tests/test_compute_admission.py`, `tests/test_compute_fence.py`, `tests/test_compute_passive_admission.py`, `tests/test_compute_script_integration.py`, `tests/test_compute_discovery_lifecycle.py` and the `tests/test_compute_control_r2.py`, `_r2_safety`, `_r3`, `_r4`, `_regressions` and `_settlement` files) prove guarded two-hop SSH admission, cross-process writer exclusion, replay, tamper, expiry and revocation ordering and uncertain-restart retention against synthetic transports, with no live endpoints or keys.
