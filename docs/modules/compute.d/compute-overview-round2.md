- **Owns:**

  - Round 2 of the #615 review, server words: the next step for a machine that is not verified (or whose verification expired) says verification is not available in this version instead of pointing at a control that does not exist; a member reads "Ask the workspace owner ..." for failures and unknown states; blockers name themselves (Offline, Never connected, Not approved, Turned off, Removed, May be full, Hourly limit, Drained or Draining); an expired request carries the issue label "Not carried out" and says there is nothing to do yet; a removed runner is `idle`, not `attention`; the page note about starting no longer mentions requests.
- **Promises:**

  - No sentence points at a control this version does not have, and no member sentence names an owner-only control.
- **How it fails:**

  - An unreadable state still reads as a fixed sentence; nothing here changes what is refused.
- **Tests:** `tests/test_compute_overview.py` (verification wording, member wording, blocker names, drained word, removed runner, expired label, executor note).
