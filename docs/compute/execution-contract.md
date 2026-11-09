# Execution and coworker handoff integration

2026-11-09. Build real durable components, not release/deployment claims.

## Ownership

Compute execution builder: new `commonplace/compute/executor.py`, any narrowly
required `commonplace/compute/fence.py`, owning `control.py` additions/migrations,
and `tests/test_compute_executor.py`. Keep existing public REST schema/semantics
and metadata tests. No changes to routes, credentials, protocol, transport,
chat/runtime or shared docs. Parent integrates worker installation and UI fields.

Coworker runner builder: `commonplace/chat/coworkers/runtime.py`, narrowly required
`commonplace/boards/service.py` changes, and dedicated new
`tests/test_coworker_dispatch.py`. Do not edit other builders' Compute files or
existing coworker store/routes/UI. Parent handles necessary store/result gluing.

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

Persist admission/approval consumption before first remote effect. Lost ack,
crash after admission and unknown outcomes become uncertain/interrupted; never
blindly repeat an execution. Retain uncertain capacity until reconciliation with
external evidence. Expose cancel requested separately from confirmed pre-dispatch
cancellation/observed exit. Finite concurrency/time/byte/output limits apply.
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
