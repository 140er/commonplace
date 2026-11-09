### Durable orchestrator

- Card tool discovery defaults to the off schema when no control database is available or a settings read fails.

- One lease per `(board, role)`; the token is returned once and stored as `sha256(token + incarnation)`.

- Expiry only fences; it never moves cards.

- The journal records `intent → started → waiting → done | failed | in_doubt`.

- External effects (merge, gate, thread launch, card move) are never re-issued by the hub; a successor checks the truth source first, and an unproven effect becomes `needs_person`.

- One gate per `(board, repo, PR, head)` across roles unless the owner voids it.

- `orch_resume` returns a checkpoint (open steps, `in_doubt`, `needs_person`, holds, next, jobs, misses) plus the unstarted `todo` cards of each configured board: no active job and no job started strictly after the card's latest firing event (a start in the same second as the event does not count).

