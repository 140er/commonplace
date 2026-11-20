- **Owns:**

  - `tests/test_model_mode_relay_eligibility.py`.

- **Promises:**

  - Real HTTP chat posts receive an actionable missing-connection refusal during an actual missing Needs me table or shared-cache write lock. No runner or hosted model starts; unrelated approval and connection items survive, and a failed write creates no item. Replies expose no machine credentials, connection IDs or other person's account details.
  - An excluded machine polling first leaves the queued row unchanged so its admitted machine can answer, for org and personal modes, machines offline at admission and machines becoming eligible later. An admitted machine whose connection is revoked or replaced still refuses instead of substituting another account. Legacy, empty and malformed snapshots remain stored and receive a resend refusal.

- **How it fails:**

  - The pinned source loses both refusal replies during table outages and lets all four excluded polling cases mark the queued message skipped. The identical regression file runs before and after the relay fix; account-change and unknown-snapshot controls must continue to pass.

- **Tests:**

  - The file itself, plus the existing model-mode and relay tests and the documentation and public-surface guards.
