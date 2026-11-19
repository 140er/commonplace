- **Owns:**

  - Round 4 of the #615 review, server side: the matrix test in `tests/test_runner_drain.py` runs `jobs.lease` and the overview over the same runs (running, finished with its card still Doing, card moved on, never ended and eight days old, two slots) and requires them to agree; plan assertions for every poll read including the drain log, resources, ended runs and a machine's history; a two-thread backfill race; an unreadable resource row; `tests/test_compute_overview.py` pins the finished-run sentences and the attention rule.
- **Promises:**

  - Each new check fails on the code before it; none sleeps (a barrier and events order the threads).
- **How it fails:**

  - A screen that says Waiting for work while the lease refuses fails the matrix.
- **Tests:** The two files above.
