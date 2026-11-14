- **Tests:**

  - `tests/commonplace_deck_compute.mjs` also runs the Compute status line and job list against scripted node reads (counts, idle, stuck and partial cases, card links, escaping), and `tests/compute_ui.mjs` has an `advanced` case for the Management grouping, run by `tests/test_compute_ui.py`. Its runner-only cases run the screen with zero map nodes: counts and job list, an idle fleet, a machine that cannot be read, clicking a runner, and the batching of reads.
