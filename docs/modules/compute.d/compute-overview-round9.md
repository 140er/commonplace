- **Owns:**

  - Round 8 of the #615 review, server side: `can_take_new_work` in `commonplace/compute/overview.py` is the one predicate for whether a runner can take new work, built from the inputs the hub's lease reads (enrollment, drain, offline and never connected, runs at once in use with finished cards still Doing, the hourly start limit). The runner status (new "Online · Limit reached" and "Not approved"), the blockers, "Allowed to start card runs" and the runner item's `intake` field all derive from it; a count of one reads "1 of 1 run at once".
- **Promises:**

  - The chip, the sentence, the blockers and "Allowed to start card runs" never disagree, and the hub's lease refuses exactly when the answer says nothing can start now.
- **How it fails:**

  - Nothing new is refused; only the words of a runner at its hourly limit changed, and two statuses were added.
- **Tests:** `tests/test_compute_overview.py` (the 96-runner matrix with `jobs.lease` as oracle, the hourly-limit cases, one case per status for "Allowed").
