### Role and card contracts

- A card carries a contract: its owned path globs (`paths`) plus optional `mission`, `allowed_tools`, `inputs`, `test_commands`, `handoff_evidence` and `escalation` (validated and size-limited in `commonplace/boards/contracts.py`, stored in the `contract` column added in place). An orchestrator role carries the same table (`[roles.<name>.contract]`); the runner role's `owned_paths` are the paths of a card that declares none.

- A start with no paths holds the whole repo (`**`) when the card has a repo: it is refused with `path_overlap` while another running card of the board holds anything in that repo, and the refusal names that card (`blocked_by`). A card without a repo keeps the old rule (no contract, no conflict). Out-of-contract edit detection at the gate and the app editor are later slices.

- Limits: 2,000 characters for `mission` and `escalation`, 32 entries of 500 characters for each list field. `task_add` and `task_update` take `paths`, or a `contract` object whose `owned_paths` is the same list (different globs in both are refused); `contract` replaces the stored text fields, `owned_paths: []` clears the paths.

- A role table in the orchestrator config looks like `[roles.builder.contract]` with `mission`, `owned_paths`, `allowed_tools`, `test_commands`, `handoff_evidence` and `escalation`. Only `owned_paths` of the runner role is enforced (as the paths of a card that declares none, and held by a running job of that role under the config the job started under); the other fields are advisory text rendered for sessions.

- Adding globs to a running card (the edit check) compares only declared contracts; the default contract applies to starts.
