- **Promises:**

  - More than three incident links on the routed Compute canvas share one end-status badge per device, with the worst known end state and a count. Down takes precedence over Degraded, then Up; Unknown applies when no end has a known state. Each state keeps its existing shape and a text description. Unclaimed ends draw no mark.

  - Selecting a link shows its own end marks; the remaining links stay collapsed and their count and worst state exclude the selected link. Each stroke keeps its pointer and keyboard target. One to three links retain their original end marks and positions; the classic canvas keeps all its marks.

- **Tests:**

  - `tests/test_commonplace_deck_compute.py` covers four, six and eight links at both layout widths, all four status shapes, mixed link directions and peers, sparse and classic marks, selected marks, label clearance, counts and independent pointer and keyboard selection.

- **Promises:**

  - Aggregate counts use 11px text in the secondary text colour, beside their status mark on the side away from the disc. Count boxes clear every disc, link stroke and label. Both Compute phone pages retain 16px gutters with wrapping actions and no horizontal overflow; desktop spacing and classic rendering retain their existing rules.
