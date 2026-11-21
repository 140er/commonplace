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

## Explicit external delegate receipt protocol

For structured references, use that same intent, then record `started` with
`evidence={"type": "external_thread", "thread_ref": "<opaque-thread-ref>",
"delegate_ref": "<opaque-attempt-ref>"}` and the card's owning `session_id`.
`delegate_ref` is optional; when omitted, `thread_ref` identifies the attempt.
References must contain 1–2000 characters. Registration checks the authenticated
lease holder and the real card's live doing claim with that exact session. The
server stamps the owner/session; another caller cannot gain authority by naming
them. Retrying the same receipt is idempotent and never refreshes progress;
conflicting refs or a second key for the same attempt in that board role are
refused. A new attempt can share a thread reference with a fresh delegate ref.

Report actual progress with `orch_heartbeat` using its usual lease arguments
plus `step_key` and owning `session_id`. Omitting `step_key` still renews only
the coordinator lease. Initial start supplies the first report time; unrelated
lease renewals, card heartbeats and state transitions cannot keep a thread fresh.
A journal job carries `thread_ref`, `delegate_ref`, `last_heartbeat_at`,
`age_seconds`, `heartbeat_age_seconds` and `liveness` (`reported` or `in_doubt`).
These ages measure caller reports, not verified process runtime. At a heartbeat report age
of 1800 seconds, or after claim/lease ownership loss, a possibly running receipt
remains visible as uncertain. This is not a task timeout and never stops work.
A late heartbeat is refused; resolve the actual result explicitly.

Record `done`, `failed`, or `failed` with `code=cancelled` for the truthful
terminal outcome. Identity remains immutable, terminal retries are idempotent,
and no external cancellation is sent. The hub makes no provider/network call,
infers no PID and grants no native readiness or capacity from a receipt.
To resolve an owned receipt from `needs_person`, record `done` or `failed` with
`evidence={"type": "card_state"}` and the owning session. The hub checks the
bound card in the actual workspace; `done` requires its status to be `done`.
The receipt keeps its refs/owner/session and gains a server-built
`resolution={type:card_state,card,status,version}` snapshot. Terminal retries
preserve it. This checked snapshot does not prove continuously current card
state or verify any later transition; callers cannot supply resolution metadata.
`jobs` includes native history, so its length is not an active-capacity count.
With explicit receipts, `next`/`next_step` describe real pending actions:
reconcile uncertain work before awaiting a fresh report, retain native/other
pending work, and remove stale next prose when the external work is finished.
Unstructured legacy entries retain the earlier claim-gated behavior.
