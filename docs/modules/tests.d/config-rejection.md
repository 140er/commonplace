- **Owns:**

  - Config-review regression coverage in `tests/test_project_config_review.py`, `tests/project_config_review.mjs` and `tests/test_commonplace_project_review_browser.py`.
- **Promises:**

  - Controls require confirmation, support cancellation and retries, prevent concurrent decisions and stop rendering after navigation. Members have read-only review; gateway tests verify refused member decisions, a final rejected version and the decision audit row.
  - The connection browser fixture answers the optional config-list and checkpoint reads made by project settings, so its routed requests settle before connection actions are tested.
  - Deck name resolution, unknown-name fallback, escaping and diff wrapping have Node checks. Byte snapshots preserve Classic's proposed, confirmation and rejected markup.
