### Restricted-origin release

- Cards created by subagents or restricted agents start inactive in backlog, including create-and-claim requests.

- Activation and claiming require release by the board's live coordinator, or by the signed-in workspace owner when no coordinator is configured or live.

- `task_approve` supports both pending proposals and unreleased restricted submissions, with an audit entry and an `approved_by` release marker.

- Nonproposal releases leave `proposal_status` unset.

- Proposal approval by a parent alone does not activate the card without that release.

- An inactive card's holder may update only its status line without release; this does not activate, claim or approve the card.

- Rejected-proposal handoffs retain the proposal-approval guard before origin authorization.

