- **Owns:**

  - `tests/test_personal_chat_runner_unavailable.py`.

- **Promises:**

  - Personal mode and a hybrid personal override receive an actionable refusal for never-seen, offline or incompatible runners, even with an enabled hosted front or an unrelated online machine. Replies remain private, contain no credential or connection identity and stay associated once with each posted message. No hosted, relay or card job is created.
  - Eligible online runners still answer on their admitted account. Fresh subscribed sessions suppress the unavailable reply; stale and unsubscribed sessions do not. Only eligible online runners with always-relay enabled bypass the active-session preference.

- **How it fails:**

  - The pinned implementation silently drops the unavailable personal chat cases; the same regression file runs before and after the refusal fix, with online and active-session controls.

- **Tests:**

  - The regression file, existing relay and model-mode tests, and evaluation, public-surface and documentation guards.
