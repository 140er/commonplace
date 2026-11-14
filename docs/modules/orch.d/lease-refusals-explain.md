- **Owns:**

  - A refused `orch_resume` and the other lease writes name what is missing: the refusal's `details` carry `missing` (stable slugs) and `fix` (where a workspace owner repairs it), and the message repeats the fix.

- **Promises:**

  - The codes and HTTP statuses of the refusals are unchanged (`not_configured` 409, `not_allowed` 403) and so is who may claim; only the explanation is added. Missing slugs: `accepted_config`, `runner_table`, `seat_role_mismatch`, `seat_of_another_project`, `seat_not_trusted`, `not_on_requester_list`, `runner_machine_unavailable`, plus the requester gaps below.

- **How it fails:**

  - The explanation names only the caller's own board and seat and never another connection, the machine's identity or a token.
