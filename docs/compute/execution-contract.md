# Execution and coworker handoff integration

2026-11-09. This contract describes durable execution and coworker runner integration;
implementation and synthetic verification do not establish deployment readiness.

## Components

Compute execution spans `commonplace/compute/executor.py`, its authority fence in
`fence.py`, control-store additions/migrations in `control.py`, and
`tests/test_compute_executor.py`. Preserve existing public REST schemas, metadata
tests and the interfaces to routes, credentials, protocol, transport and chat/runtime.
Worker installation and UI fields must remain consistent with these interfaces.

Coworker runner integration spans `commonplace/chat/coworkers/runtime.py`, the
canonical task methods in `commonplace/boards/service.py`, and
`tests/test_coworker_dispatch.py`. Preserve existing coworker store/routes/UI and
Compute interfaces, including the storage and result projections between them.

## Durable Compute execution

Use the real control Store and canonical tasks, credential custody and
SSHTransport. A background service (disabled by default) must consume approved
proposals exactly once into durable execution attempts, rechecking exact origin
and approver sessions, current owner/board/task authority, exact fresh binding
and approval expiry. No new task/identity engine. Running/terminal execution
facts are distinct from approved action consent. Prefer a separate execution_runs
journal rather than weakening existing proposal state constraints/migrations.

Only service-verified enrollment is eligible. Add no public verification setter
or body-supplied verified flag. A trusted explicit provisioning/verification seam
may record independently verified exact bindings and epochs; unverified pending
records cannot dispatch. Changing enrollment after proposal must invalidate its
approval. Do not perform an unapproved probe to manufacture verification.

Production dispatch requires a real authority fence covering current relevant
control/board/Compute mutations. Existing process-local locks do not prove that
cross-process guarantee. Default deny if an application-wide fence is absent;
implement a narrow injected fence contract with deterministic tests, and clearly
leave live capability disabled until all production mutation paths participate.
A fake fixture fence is not release evidence. Do not edit unrelated global auth
mutation paths to assert unproven compliance. No token, ambient keys or alternate
transport fallback. Credential handles must be exact bound/version references,
short-lived and only converted internally after fresh checks. Missing keys,
verification, transport or authority produces a safe local reason.

Persist admission/approval consumption before first remote effect. Never blindly
repeat an execution. Stage 4 is committed before either fixed-command or script
exec channel opens. A returned outcome or recovery at stage <4 is a confirmed
non-run and releases its reservation. Missing durable permits prove non-runs only
in explicit wire mode; a legacy stage-4 unknown retains its reservation.

After claim, `_fresh` re-reads exact binding and consumed consent, origin and
approver sessions, current membership, task/origin release, enrollment/runtime and
active custody credentials at each boundary. Discovery similarly rechecks its
owner session, scope/agent connection, membership, task/origin release, gateway
receipt and custody. Discovery's pre-pin connect check does not touch custody;
after the independent gateway pin, custody is rechecked before authentication,
collection, permit issuance and publication. Permission loss denies dispatch and
withholds publication.
The coordinator-wide epoch is not compared with the claimed epoch: real agent
heartbeats renew claim leases, and sign-ins/new cards insert rows that can advance
it without revoking this attempt. Narrowed UPDATE guards remain installed.
Task snapshots bind assignee, status and the canonical `claim_expired_at` marker,
excluding the renewable `claim_until`. A holder's heartbeat or comment on the
bound card preserves agent-bound discovery dispatch and observations. A holder
change, status change or recorded lease expiry still denies. Older immutable
bindings compare without `claim_until`, retaining their original consent digests,
attempts and permits through upgrade.
A task-less fixed-probe proposal may still be approved; dispatch requires a
startable card. Task-snapshot presence must match on both sides of comparison.
Permits and their lookup retain the request's epoch. Current-fence exclusion and
checks for writes during a single fenced operation remain intact.

Authenticated target script results in denied/cancelled state with confirmed
termination are known non-runs. The target's pre-claim script_error codes
runtime_digest_mismatch, binding_mismatch, replay_denied, target_capacity and
sandbox_unavailable are also known non-runs, with their specific reason retained.
An unlisted error or helper_unavailable does not prove a non-run.

Unknown outcomes after the stage-4 exec boundary retain uncertain capacity until
reconciliation with external evidence. The trusted service operator can use
python -m commonplace.compute.operator (see RUNNING.md) to settle only an
uncertain execution, supplying its id and a nonempty note of at most 1000
characters describing the evidence. This operation participates in the local
fence, atomically stores operator_note and an execution.operator_settled audit
action, and records terminal/known with reason operator_settled and reserved=0.
It works after worker restart, preserves the immutable attempt and never retries
or republishes withheld output. It is not an HTTP route or owner-browser action.
Notes belong in the private workspace journal and must contain no secrets.
Expose cancel requested separately from confirmed pre-dispatch cancellation or
observed exit. Finite concurrency/time/byte/output limits apply.
No locks may span network/model work. Do not claim Python force-stops a thread.

Only the fixed compiled system.info.v1 operation may run. Store bounded redacted
result/evidence under workspace crypto and authorized object audience, no raw
output in logs/global control rows. Do not mark canonical tasks complete merely
because a probe or admission finishes. Provide public result projection through
existing inventory methods with current owner/object checks; no new effectful
HTTP endpoint. App startup must not auto-initialize wrapping keys or enable work.

Meaningful tests against actual control/custody/protocol seams and synthetic
transport: valid verified+approved admission/result/reopen, no self-grant,
changed pins/epochs/task/session/expiry before effect, double claim/restart/lost
ack no repeat, cancellation truth, bounded redaction, no network/key reads while
disabled, missing-fence fail closed. No real device or network endpoints.

## Coworker to existing runner

Current /task command atomically creates a canonical todo and records typed
admission. Extend the existing explicit task intent to the existing configured
runner admission path when authorized and configured, without delaying HTTP or
front conversation availability. Use existing orchestration configs,
runner_bridge.person_start/fenced_start/job dedup and real current person;
never synthesize connections, tokens, worker rows or a second task registry.
Creating a persona grants no authority; its display name is not an assignee.

Task creation and actual runner admission are separate durable facts. Use stable
origin keys across lost acknowledgements; do not create/launch duplicates after
crash or retry. Recheck exact originating session, membership, board/task and
configured runner binding immediately before admission. Do not hold an HTTP,
model, or broad control writer lock during external start. Prefer existing
canonical APIs over direct task status hacks. An unconfigured/unavailable runner
leaves the real task available and reports a truthful reason; no paid/CLI/API
fallback and no fabricated launch. No /task free-form output execution or bot
message loops. Never automatically approve guarded plans or mark work complete.

Tests should use real canonical board/runner job stores with synthetic admission
and no actual runner process/provider/network: configured admission, no config,
current revocation, stable replay and changed configuration, blocked worker A
while new HTTP/store intake/B conversation progress. Keep old runtime tests valid.
Return actual limitations; do not claim natural-language/tool parity or installed
worker capabilities without evidence. No new natural-language side effects are
authorized by model output.
