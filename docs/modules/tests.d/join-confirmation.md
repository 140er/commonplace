- **Tests:**

  - `tests/workbench_e2e/test_invite_join.py` runs with `CP_BROWSER_E2E=1` using the existing Chromium fixture and real invite gateway. It checks confirmation at 1280×800 and 390×844 with scale 2, the sent Origin and absent referrer, the member session, replay, expiry and wrong-account refusals. `CP_INVITE_SHOTS` optionally writes the invite-state screenshots outside the repository.
  - The invite HTTP helper sends a real Origin for form posts by default; explicit missing, null and foreign Origin cases verify that none can bypass join protection.
