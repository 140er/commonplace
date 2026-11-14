- **Owns:**

  - `requester_gaps` lists why a principal cannot start on a machine (`workspace_mismatch`, `runner_credential`, `owner_required`, `pat_required`, `seat_not_trusted`, `not_on_requester_list`); `requester_ok` is exactly "no gaps".

- **Promises:**

  - The authority rule is unchanged: a signed-in owner, or an active trusted PAT on the machine's requester list.
