### Defaults chosen for slice 1

| Question | Default |
|---|---|
| Who approves a config | The owner only. |
| Who may hold an orchestrator lease | A principal that passes `requester_ok` for the configured machine (a listed trusted PAT or the person owner); a project seat only on its own board. Without `[runner]`: only the board's own trusted project seat, for its seat role. |
| Lease TTL | 1800 s (G). |
| Wait budgets | Guesses until measured (see Numbers). |
| CLI acts as owner | Yes, through data-dir access. |
| Members' moves fire triggers | No. |
| Model slots | Four (`main` = orchestrator, `worker` = builder, `reviewer`, `gate`), set per project by the owner. An optional per-project escalation rule (`after_failures` 1–10 and/or card `labels`, plus a stronger model) swaps the builder and reviewer model at start: the card's `failed` jobs are counted inside the start transaction, so a card that failed twice is retried on the escalation model. The job stores the model and `model_reason` (`failed_rounds:<n>` or `label:<name>`); the rule's model comes from the workspace model list; a keyed retry replays its stored job whatever escalation decided, judged by the job's pre-escalation request digest (`request_base_hash`, default model included), so a changed default is still a conflict; a role with no default stays unmapped; with no rule a start resolves exactly as before. |
