### Role and card contracts

- A card carries a contract: its owned path globs (`paths`) plus optional `mission`, `allowed_tools`, `inputs`, `test_commands`, `handoff_evidence` and `escalation` (validated and size-limited in `commonplace/boards/contracts.py`, stored in the `contract` column added in place). An orchestrator role carries the same table (`[roles.<name>.contract]`); the runner role's `owned_paths` are the paths of a card that declares none.

- A start with no paths holds the whole repo (`**`) when the card has a repo: it is refused with `path_overlap` while another running card of the board holds anything in that repo, and the refusal names that card (`blocked_by`). A card without a repo keeps the old rule (no contract, no conflict). Out-of-contract edit detection at the gate and the app editor are later slices.
