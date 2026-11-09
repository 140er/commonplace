### Defaults chosen for slice 1

| Question | Default |
|---|---|
| Who approves a config | The owner only. |
| Who may hold an orchestrator lease | A principal that passes `requester_ok` for the configured machine (a listed trusted PAT or the person owner). |
| Lease TTL | 1800 s (G). |
| Wait budgets | Guesses until measured (see Numbers). |
| CLI acts as owner | Yes, through data-dir access. |
| Members' moves fire triggers | No. |
| Model slots | Two (`main`, `worker`). |

