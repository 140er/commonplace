- **Owns:**

  - Round 4 behaviour of the cloud workspaces section (`compute-workspaces.js`, `compute.js`, `compute.css`): one status vocabulary ("Saved, not started", "Changes waiting", "Planned machine: <name>"), a discard confirmation inside the record's own row with fixed labels and a refusal that replaces its body when the record started, a stale edit with a really disabled submit, "Reload to continue" and "Apply my changes again", image help behind a "Where do I find it?" disclosure with the address box toggled by `hidden`, validation on commit and submit (a commit made on the way to a button waits for the pointer to be released), the summary taking focus, and the page called "Cloud workspaces" with every technical section behind "Advanced".
- **Promises:**

  - Nothing on these screens says Ready, running or started until a record really holds one; a failed record shows its reason in the list row; a refusal never contradicts itself and never leaves a live red button; typed values are never lost to a reload and are shown as typed; no error is announced per keystroke; the status line never takes focus (a saved row's heading does); a member reads "You can read this workspace's settings. Only its owner can change them."
- **How it fails:**

  - A refused discard, a stale edit, a lost answer and a failed load each say what happened and what to do next beside the control that caused them.
- **Tests:** `tests/compute_workspaces_ui.mjs` (adds validation timing, image choice, discard in the row, the refusal and stale recovery), `tests/compute_ui.mjs` (the Scripts heading, Advanced holds all six technical sections) and `tests/test_compute_workspaces_browser.py` (typing is silent, the image choice does not scroll, discard in the row, stale recovery).
