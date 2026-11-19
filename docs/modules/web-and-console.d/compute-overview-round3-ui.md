- **Owns:**

  - Round 3 of the #615 review, screen side (`compute-overview.js`, `compute.js`, `compute.css`): elapsed time comes from the monotonic clock (`performance.now`), so a wall clock that is stepped or skewed moves no age; the age override covers every runner status that claims a recent signal (a draining runner gone quiet reads Offline and still says it drains); the unknown-workspace step is one sentence for owner and member ("Nothing to do yet; this clears when the machine reports again."); the "need attention" button names its scope with a kind filter ("1 runner needs attention"); the state sentence (loading, failed with "Try again", empty with what to do) sits outside the fold and the summary has no disclosure marker while there is nothing to fold; "Try again" shows "Trying again…" and a count when it fails again and focus lands on the heading when it works; an open item removed by a refresh hands focus to the list or the heading; the pause button and its line say "automatic updates" and that statuses and ages keep counting; a failed refresh is said by the alert and the per-row marks (the meta line stays empty); a machine with no blocker reads "Nothing known to block new work (not checked live)"; the page's own failure line is plain text whenever the overview has its own alert.
- **Promises:**

  - A stepped wall clock changes nothing on screen; one outage has one alert.
- **How it fails:**

  - A failed first read shows the alert and "Try again" outside the fold, so a closed fold on a phone never hides it.
- **Tests:** `tests/compute_overview_ui.mjs` (clock step, every runner status ages, scope and state outside the fold, Try again feedback) and `tests/test_compute_overview_browser.py` (a stepped wall clock, Try again focus, a removed open item, one alert).
