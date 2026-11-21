### Durable orchestrator

- Card tool discovery defaults to the off schema when no control database is available or a settings read fails.

- One lease per `(board, role)`; the token is returned once and stored as `sha256(token + incarnation)`.

- Expiry only fences; it never moves cards.

- The journal records `intent → started → waiting → done | failed | in_doubt`.

- External effects (merge, gate, thread launch, card move) are never re-issued by the hub; a successor checks the truth source first, and an unproven effect becomes `needs_person`.

- One gate per `(board, repo, PR, head)` across roles unless the owner voids it.

- `orch_resume` returns a checkpoint (open steps, `in_doubt`, `needs_person`, holds, next, jobs, misses) plus the unstarted `todo` cards of each configured board: no active job and no job started strictly after the card's latest firing event (a start in the same second as the event does not count).

- Checkpoint `jobs` covers runner jobs (`source=runner`) and builders started outside the runner (`source=journal`). A journal entry is listed only while two facts hold: the lease holder recorded the start of an unvoided, unfinished `thread` step for a card (`started` or `waiting`; it stays listed when a later epoch marks the step `in_doubt`), and that card is in `doing` on the same board under a live claim. Each entry carries the card, step key, assignee, claim end and the step's evidence. Nothing is stored and no card or step is changed; the hub does not verify that the thread exists.

- Explicit `external_thread` evidence adds opaque receipt references to that existing path. Initial registration requires the real board/card's live doing claim, its exact owning session, the authenticated actor and the current lease epoch/token. The server stamps owner/session; refs cannot be replaced or the same attempt registered under another key within that board role. These reports remain outside verified runner/card evidence.

- An optional `step_key` and owning `session_id` on the existing heartbeat operation append a progress event for that receipt. Initial start supplies the first report time; lease renewal, card bookkeeping and state transitions do not count as thread progress. The checkpoint uses one clock for report age, heartbeat age, state, jobs and next actions. A heartbeat report age of 1800 seconds, claim expiry or lease ownership loss leaves possibly running explicit work visible as `in_doubt`; this is uncertainty, not a task deadline or proof that a process stopped. A late heartbeat cannot revive it.

- Explicit receipt identity survives terminal records. `done`, `failed`, or `failed` with `code=cancelled` stop pending external work; they never contact or cancel a provider. Native job history, admission and readiness stay authoritative for native work. Receipt metadata supplies no PID or native capacity; `len(jobs)` is not a capacity counter. A linked native job is listed once.

- For an owned explicit thread receipt, a fresh `card_state` request can resolve `done` or `failed`, including from `needs_person`; `done` still requires the actual card to be done. The server appends `resolution={type:card_state,card,status,version}` to the unchanged receipt identity from the snapshot checked in that call. Retries retain the full snapshot. It does not prove continuously current state, grant process authority or verify a later transition; caller-supplied resolution metadata is refused.

- With explicit receipts, `next` stays a string and `next_step` a public step or null. They describe real unsettled actions, prioritizing uncertain external work over awaiting a fresh report while retaining native/other pending actions. Completed receipts cannot keep old free-text next notes pending. Unstructured legacy-only checkpoints retain their prior behavior.
