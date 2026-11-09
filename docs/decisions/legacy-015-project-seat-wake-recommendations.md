### Project seat wake recommendations

- Connect recommends a board-scoped webhook for `space_message` and `card.moved`, so messages and worker completion moves can wake the orchestrator.

- Setup is advisory: Connect and reconnect neither register nor modify subscriptions.

- Existing users must explicitly update their webhooks to receive card moves.

- Message coordinator filtering and reply-loop suppression still apply.

- With orchestration enabled, the list appends `card.created` and `project.report`.

