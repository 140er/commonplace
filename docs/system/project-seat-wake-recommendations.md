### Project seat wake recommendations

- Project Connect writes every HTTP 409 conflict to its persistent status. Shared-memory
  and replacement conflicts retain their explanation and next step after dialog dismissal;
  shared-memory consent and own-section conversion still require the existing explicit choices.
  Other conflicts include the server's reason in the Orchestrator section of project settings.

- Project settings render the orchestrator status and bind Connect as soon as the
  project view arrives, before chat settings, connections, cards or chat status
  settle. Chat and agent sections show loading and their own unavailable states;
  their initial reads have ten-second client deadlines.

- `GET /api/connections` spends at most one second waiting for its optional memory
  wing listing, including worker transport. Timeout, `ToolError` or an already
  busy lookup returns the existing connections and `wings: []`. One daemon lookup
  per gateway bounds outstanding work; a timed-out call may finish later, without
  caching its result or delaying later requests. Until it finishes, other requests
  use the same empty-wings fallback, including requests from other workspaces.

- Connect recommends a board-scoped webhook for `space_message` and `card.moved`, so messages and worker completion moves can wake the orchestrator.

- Setup is advisory: Connect and reconnect neither register nor modify subscriptions.

- Existing users must explicitly update their webhooks to receive card moves.

- Message coordinator filtering and reply-loop suppression still apply.

- With orchestration enabled, the list appends `card.created` and `project.report`.
