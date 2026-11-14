- **Promises:**

  - Dense same-row pairs occupy at most 32 map units, while two links retain their 16-unit separation. Four or more cross-row links sharing a clear side corridor use distinct lanes within a 32-unit span, with ordered exits at their device discs. Every replacement stroke and status mark must clear the scene's labels and devices; blocked exits retain their existing routes. Sparse and classic routes keep their existing geometry.

- **Tests:**

  - `tests/test_commonplace_deck_compute.py` proves bounded bundles, label and device clearance, deterministic routes, unchanged sparse geometry and independent pointer and keyboard selection.
