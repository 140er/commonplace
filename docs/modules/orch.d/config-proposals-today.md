- **Owns:**

  - `commonplace/orch/proposals.py`: the owner's pending config proposals for Waiting on you (card #662). Each proposed version on a board the owner can read is one item with its board, version, accept digest, the proposer by display name (a person by name, the project's Coworker seat as "<project> bot", another app by its name), a one-line plain-words summary of the changed values (agent limit first, at most three, then "and N more changes") and the diff against the accepted config of the channel; a first config is summarized by what it sets up and shown whole.

- **Promises:**

  - Read-only: the store opens without a statement, nothing is written, and a body that no longer parses yields "The changes could not be read" instead of an error. Accepting and rejecting stay on `orch_config_accept` and `orch_config_reject`; their owner check and digest check are unchanged.

- **How it fails:**

  - An unreadable or missing store gives an empty list and a logged failure, never a failed Waiting on you read.

- **Tests:**

  - tests/test_config_proposals_today.py.
