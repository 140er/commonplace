## boards

- **Paths:** `commonplace/boards/`, `commonplace/events.py`, `commonplace/webhooks.py`, `commonplace/holds.py`, `commonplace/routines.py`, `commonplace/callstack.py`
- **Owns:**

  - Canonical Space REST response projection and typed creation refusals.

  - Plain refusals for the owner REST Done move and handoff (`boards/refusals.py`): a 403 carries a reason in `error` and the next step in `next_step`; the web client (`web/static/api.js`) shows both, and a 403 without a JSON body gets a readable message. Who may move or hand off a card is unchanged.

  - Boards, cards, the append-only `task_events` log, approvals and decisions, the hub event log, signed webhooks and delayed person approvals.

  - The card's completion reviewer (`boards/reviewer.py`): only a signed-in workspace owner chooses, changes or clears it, it must be a current member, and a reviewer who left counts as none. With none the owner reviews as before. With one, every move to Done by anyone else opens a completion review addressed to that member, and only that member may answer it; the reviewer sees it in needs-me, and changing the reviewer re-addresses an open review.

  - Routines (`routines.py`, `/api/routines`): an owner-defined name, card template, schedule and owner that create one card per due time through `BoardService.task_add`, with a history of runs linking to the cards. Due times come from the shared `schedule_time.py`.

- **Promises:**

  - Owner-granted `cards:decide`: a live owner's top-level PAT or OAuth connection holding the scope answers only asks addressed to that owner, is refused for seats and board coordinators, needs `memory:write`, read access to the board's wing and the owner's person-level write access to it (not the app's own write wings), cannot escalate, and writes one `card.decide.delegated` audit row inside the board transaction (a failed write fails the answer; a later rollback appends `card.decide.rolled_back`; an audit row without an answer is possible after a kill between the two commits, or when the cancel write fails). Without the scope, refusals are unchanged.

  - A routine creates at most one card per due time, also across restarts (a unique run row per due time plus a marker label on the card); the owner is a workspace owner whose authority applies at run time, runs are bounded (25 routines per workspace, 60-minute minimum interval, 3 catch-up cards after downtime, 200 history rows per routine) and five failed runs in a row pause the routine. Only signed-in workspace owners read or change routines.

  - Member event pages use one readable-board snapshot, an opaque access epoch and bounded SQL projection. Restricted webhook subscriptions omit public sequence fields and use opaque ids from independent server-only subscription keys, initialized on first use and durable across restarts. Neither keys nor sequence fields are exposed; raw event keys remain internal for replay deduplication without a counter or backfill. Subscription lookup exceptions log their type and stack locations while retaining fail-closed authority.

  - Web feeds and webhooks share the current-principal event visibility rule; non-owner person feeds omit boardless/private events and use readable-log sequence numbers.

  - Owner assignments to active agent connections claim the card with a lease and a claim event.

  - Immediate assignment claims apply to todo cards; handoffs of doing cards return to todo without starting the replacement worker.

  - After commit they request the configured worker start, or persist a visible reason that it could not start; concurrent card updates are preserved.

  - Explicit takes of an already held card return that claim without renewing it.
  - Card schemas default to orchestration off when the control database is absent or settings reads fail.

  - Disabled orchestration preserves legacy capacity refusals and never rewrites coded limit errors through the enabled waiting projection.

  - Enabled blocker updates are versioned and audited; pure waiting reasons drive views and claims, and SQL skips unresolved edges.

  - Card lists share a runner read and per-board usage; a capacity refusal retains its original error when no display reason is available.

  - Board REST maps only database lock timeouts to busy; waiting summaries reuse one runner read and two board usage counts; split-key edits preserve child identity.
  - Card task types are owner-person edits; stable-ID grants in the workspace store guard new typed acquisition while existing holders retain their claims.
  - Owner todo assignments validate the target’s typed grant before claiming; repeat takes preserve an existing holder’s claim after grant removal.

  - Owner status changes preserve typed-card assignees and validate their grants; card task types are owner-person edits; stable-ID grants in the workspace store guard new typed acquisition while existing holders retain their claims.
  - Owner coordinator settings list eligible active PAT connections; signed-in owners see restricted-origin recovery once per visible board across backlog, todo and doing.
  - Owner notices count release and configuration separately; notices stay out of digest while actionable origin cards retain reasons and actions in it. Non-person callers receive no owner notice.
  - Metadata uses an exact active slug on a plain store connection; list retains expiry and create retains rules enforcement.
  - Non-done to done transitions capture metadata in the source transaction. Unavailable report settings reject before status or lesson writes; new boards without a coordinator remain history only. Raw events retain their existing matching and authorization.

  - Blocker refusals (`self`, `cycle`, `other_board`, `invalid_blockers`, `blocker_graph_too_large`) carry a plain sentence; a blocker on a card the caller cannot read gets the same `other_board` answer as a missing number.

  - Waiting reasons beyond blockers, person, release and capacity (`claim_expired`, `plan_review`, `no_agent`, `checks_pending`) share `boards.why`; only the claim-refusing codes in `why.REFUSING` stop a claim, and board lists add `waiting_notes`.

  - Every card change is one transaction with its `task_events` row; event emission happens after commit and a failed emission never undoes the change; on a configured board only `task_approve` removes `plan` and plan-check gates plan approval.

  - Trusted agents retain their title/body edit alternative on unassigned restricted-origin todo cards. Coordinator-derived edits require verified independence: creators, descendants and unsafe lineage cannot use that exception. Content edits never release origin or grant claims.

  - With workspace orchestration enabled, claims hold runner admission through board commit and person assignments or handoffs cannot reserve a busy agent.

  - The owner's `needs_me` lists boards nudged three times in a row without a done card (`orchestrator_stalled`); `board.stalled` and `orch.trigger.failing` are hub events with webhook labels.

  - The owner's `needs_me` lists boards whose orchestrator trigger sweep keeps failing (`orchestrator_failing`), limited to boards whose wing the owner can read.

  - Subagent and restricted-agent submissions remain inactive until deliberate origin release. An appointed independent coordinator needs current memory read/write, board-wing read/write and visible space_read, space_post, task_approve and needs_me; decide is not required. Ancestors remain eligible, while creators and their descendants cannot obtain new release authority. Missing, foreign or cyclic ancestry and unverifiable connection-creator stamps fail closed on every agent release path, including stored markers.
  - The appointed independent coordinator may release origin while holding the card, preserving status, holder, every lease timestamp, provenance and pending review. Its marker is revalidated against current appointment, liveness, lineage and grants. This grants no self-review, rejection or escalation rights; origin release requires an affirmative decision and a nonempty reason of at most 500 characters.
  - A status-only update lets a creator cancel its own todo reservation to backlog after handoff, even if origin release has lapsed. It clears the assignee, preserves provenance and the approval marker, and grants no activation or review authority. Other reservations and pending/rejected proposals or person decisions still refuse cancellation.
  - Signed-in workspace owners recover origin only when the configured record is absent/non-live, or verified independent but missing a required release grant. A live creator, descendant or unsafe lineage requires coordinator configuration and blocks owner release, even with missing grants. An unverifiable creator stamp plus any live configured record also blocks release: clear the coordinator first. Clearing affects the entire board and routes its other unreleased cards to owner recovery as well. Owner-role connection tokens gain no person fallback.
  - Owner recovery preserves pending proposals and plan reviews: release origin first, then review separately. Origin/configuration items appear once in approvals with release/you needs and approve/configure_coordinator actions; healthy coordinator work creates no owner origin item. Explicit person-targeted backlog proposals and plans retain their independent review route, including owner rejection outside fallback without origin release. Origin audit events keep kind=decide with choice="release origin"; that exact validated choice is reserved and refused in user decisions and ask options.
  - Person review items advertise reject only while origin awaits release that the viewer cannot perform, with a matching explanation; proposal Decline and plan Reject controls render independently of approval permission; other review actions remain intact. Owners retain pending-proposal rejection in configuration states via the card/API approve route with approve=false; the configuration item itself offers only board settings. Claim delivery excludes origin-release decide events by choice so an earlier return-routed decision, including a reply-only decision, is delivered unchanged.

  - Holders may annotate an inactive status line without activation.

  - All agent asks and review questions default to the authorized live board coordinator. Explicit person_reason is audited and routes to the owner; completion reviews and person handoffs retain their route. Missing or unavailable coordinators fall back to the owner without rewriting asks. Only a currently authorized coordinator may withdraw its own agent-directed decision to todo/doing; recipient, requester and current board appointment are checked. Person decisions and other requesters remain protected, and agents cannot answer their own asks. Withdrawal preserves history.

- **How it fails:**

  - Hidden metadata is absent; hidden repo collisions are forbidden; unsupported shapes execute no domain method.
  - Report routing defers one event durably before advancing the global cursor; authorization retries indefinitely with fixed inspection code and persisted count/since, independent of HTTP attempts. Control persistence failures retain unacknowledged work.

  - A swallowed emission loses a notification but not the change (the trigger sweep reads `task_events`); webhook delivery retries and records failures.

  - Non-closure ProgrammingError follows the same recovery path as every other unexpected dispatcher-pass exception: it is contained and logged without its private message, then waits on the stop event for 5 s, doubling after consecutive errors up to 300 s, before continuing.

  - A successful pass resets the backoff; stopping interrupts the wait, and a closed database still ends the dispatcher.

- **Tests:**

  - `tests/test_spaces_contract.py` covers DTOs, isolation, authority, collisions and side effects.
  - `tests/test_project_reports.py::test_enqueue_deferral_raw_progress_and_backoff`, `test_send_retry_accounting_and_identity` and `test_auth_retry_inspection_beyond_latest_100` pin durable retries and inspection.

  - `tests/test_cards_blocked_by_app.py` with `tests/blockers_ui.mjs` covers the app's blocker section, the REST add/clear round trip, todo and take-next skipping blocked cards, and plain refusals.

  - `tests/test_cards_waiting_reasons.py` with `tests/board_why.mjs` covers each waiting reason (pure, card view, board summary) and its app rendering.

  - `tests/test_routines.py` uses a fake clock: one card per due time, restart recovery without duplicates, bounded catch-up and history, pause/resume/delete, owner-only refusals and limits.

  - `tests/test_commonplace_boards.py`, the `tests/test_commonplace_board_` files, `tests/test_commonplace_decide.py`, `tests/test_orch_plan_gate.py`, `tests/test_commonplace_webhooks.py`.

  - `tests/test_commonplace_subagent_asks.py` and `tests/test_commonplace_agent_asks.py` check generic question routing, owner fallback, explicit person reasons, recipient authority and withdrawal guards through MCP and HTTP.
