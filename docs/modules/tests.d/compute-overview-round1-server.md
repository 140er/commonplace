- **Owns:**

  - Round 1 of the #615 review, server side: `tests/test_compute_overview.py` pins that no sentence promises a control the screen lacks, the not-verified and expired machine blockers, one unreadable machine row, offline and removed runner wording and the configured offline limit; `tests/test_runner_drain.py` pins the drain scope sentence, the hub audit written inside the drain transaction (and a failed audit leaving no drain), the indexes and the single runner-store open per poll; `tests/test_compute_workspace_lifecycle.py` pins `request_pending` for edits, a withdrawn claimed request ending in `failed` / `machine_unreachable`, the expiry audit surviving a refusal and a random claim token.
- **Promises:**

  - Each new check fails on the code before this change and passes after it; none sleeps (clocks are moved, hooks are spied on).
- **How it fails:**

  - A sentence that offers withdraw, resume or ask-again, a drain with no audit line, a request retargeted while it waits, or a workspace left in `starting` after its request was withdrawn fails these tests.
- **Tests:** The three files above, with `tests/test_compute_workspaces.py` and `tests/test_topology_graph_status.py`.
