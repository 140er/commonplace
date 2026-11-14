- **Owns:**

  - `launch_output.py` sanitizes the launch bridge's captured streams before local logging, journal persistence and result delivery. `jobs.py` stores failed-launch output on the existing job row.

- **Promises:**

  - A `launch_unknown` result carries at most 4,000 characters: exit status and labeled stdout/stderr tails, with known credentials, token-like strings and the shared redactor's secret patterns removed before truncation. Timeout diagnostics retain captured partial output.
  - The job's owner status includes `reason: {code: "launch_unknown", detail: ...}`. `GET /api/card-starts/<job>/launch-output` returns that reason to signed-in people who can read the card's board; tokens, foreign workspaces, unreadable cards and jobs without output get 404. Agent status rows omit diagnostics.
  - Output is a nullable column on `runner_jobs`, follows the row's retention and workspace export/deletion, and survives fenced result replay. Successful launches add no job output and keep the existing local log behavior. Launch outcomes and slot accounting are unchanged.

- **Tests:**

  - `tests/test_runner_launch_detail.py` covers readable tool errors, secret sentinels on both streams, bounded tails, private local logs, result replay, scope refusals, job deletion, input validation and unchanged successful jobs.
