### Reporting lines

- A role's `parent` in the orchestrator config is its reporting line; a role without one reports to the owner.
- Escalation follows the line: unvoided `in_doubt` and `needs_person` steps of a role appear as `escalations` in the checkpoint of the role it reports to.
- Delegation follows the line: only the lease holder of that role takes a step back (`orch_reassign_step`, a void with history kept); the owner keeps void. Runner-start, gate, hold and `needs_person` steps stay owner-only: a person's decision is answered by a person.
- A config without `parent` keys has no escalations and refuses every reassignment.
