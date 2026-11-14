- **Promises:**

  - Two links between same-row neighbours use straight parallel lanes 16 map units apart when both lanes and their status marks clear all label plates and other devices. The ends meet the device discs. Single links, blocked lanes and links across rows retain their existing geometry; the classic canvas is unchanged.

  - On the routed canvas each link is a separate focusable target: a pointer press, Enter or Space toggles its highlight without selecting a device or opening a detail panel.

- **Tests:**

  - `tests/test_commonplace_deck_compute.py` covers the parallel router output in both directions, both viewport layouts, label and status-mark clearance, exact single-link and obstructed-route geometry, and independent pointer and keyboard selection.
