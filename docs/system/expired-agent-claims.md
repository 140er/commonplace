### Expired agent claims return to work without renewing another run

An agent claim first expires in place: `claim_expired_at` records when expiry was
observed, while the card stays in `doing` during a recovery grace period. The
workspace's `workspace.json` setting `claim_expiry_grace_minutes` defaults to 30;
zero allows immediate recovery. It must be a non-negative integer. The grace
starts at the first observed expiry, including for old rows, so an upgrade does
not immediately redistribute every old claim.

After the grace, eligible agent work returns to `todo`, clears its claim deadline
and session identity, and appends a `release` history row with the exact text
`claim expired, returned to the pool` and the `doing` → `todo` transition. Existing
history and the expiry timestamp remain. Ordinary work becomes unassigned. A
restricted or unresolved legacy origin retains its reservation and origin-release
requirements: it returns to its reservation holder's queue, and a coordinator
cannot take that expired reservation. The project owner may deliberately hand it
to a different connection through the existing assignment controls.

Origin release is an authority grant and never renews or reacquires a claim.
Within the grace it preserves the expired Doing card and its deadline. After the
grace, its ordinary expiry pass first returns the card to reserved Todo and logs
that return; the separate origin-release event follows. The former Doing deadline
is cleared, the expiry timestamp and assignee remain, and the project report
revision advances for the lane change. The origin grant adds no claim deadline.
The pinned origin-release regression covers both sides of this boundary.

Only `doing` cards held by a connection are returned. Person-held cards, review
cards, open questions or review context, pending/rejected proposals, archived
cards, active time/dependency snoozes and unfinished `blocked_by` dependencies
stay where they are. A resolved dependency allows a later sweep to return the
card. Protection is checked inside the same write transaction as the return.

Board reads that already expire claims and the configured board's existing
trigger sweep both process due returns. The append-only transition is an existing
`card.todo` firing event; no scheduler-side admission policy is changed. Automatic
return events retain the previous connection's authenticated requester stamps so
the existing start path can revalidate its current access, machine grant and
origin release. A revoked or ineligible requester produces the existing coded
miss rather than bypassing those checks. A restricted reservation remains on the
card through start admission.

A successful MCP `initialize` response now issues `Mcp-Session-Id`; clients send
that header on subsequent calls. New claims carry this session identity in
`claim_session`, separately from their connection actor. Stateless clients can
instead send a stable `session_id` on task write tools, `orch_resume` and
`orch_checkpoint`; this also supports a run identity that survives a transport
reconnect. These optional schema fields are recorded in the tool inventory;
tool names, profiles and visibility stay unchanged. The HTTP handler scopes the
identity around the existing two-argument mediator call and resets it on success,
notification and error paths, preserving adapters and keepalive isolation.
The identifier narrows existing connection authority and is not an
authentication credential. It is not included in public card replies.

Every acquisition path records the identity when the caller claims for itself.
Heartbeat, held-card retake and holder mutations require both the connection and
the matching session. Other sessions may append comments, but cannot thereby
renew the claim; memory links without a matching session also cannot renew it.
Resume carries its original context into heartbeats and renews only matching
live claims. It never renews expired claims. The additive, idempotent database
migration supplies `claim_session TEXT NOT NULL DEFAULT ''`: existing claims,
headerless legacy clients and assignments made on another worker's behalf remain
session-less and can only be renewed by the same connection. Clients must use
the session header or explicit run identifier to isolate new claims.

Before this change, a coordinator's checkpoint put another connection's expired
card in `cards.not_held_by_caller`, and `unstarted` omitted it indefinitely because
it remained in `doing`. Now every reader sees expired doing cards in
`cards.expired`, including during the grace. Checkpoints themselves stay read-only.
After a board read or trigger sweep returns the card, it leaves `cards.expired`
and appears in `unstarted` with its new todo event boundary until a current start
covers that occurrence. A live claim owned by another session on the same
connection appears in `cards.not_held_by_caller` and is not heartbeaten by resume.
