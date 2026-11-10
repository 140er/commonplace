# Run and inspect this feature

Use the feature worktree and a separate development data directory. The commands
below are instructions; the overnight build did not start a persistent server or
change the running Commonplace instance.

```sh
uv sync --extra dev --extra compute
uv run commonplace --data /tmp/commonplace-coworker-dev init \
  --admin-email owner@example.test --workspace coworker-dev
uv run commonplace --data /tmp/commonplace-coworker-dev serve \
  --host 127.0.0.1 --port 8765
```

Use the existing local sign-in setup for that owner, then open
`http://127.0.0.1:8765/app/#/coworkers`. Initialization creates an owner and workspace;
it does not configure email delivery or an external model. Existing deployments
can inspect the feature against an isolated copy using their normal sign-in.

Create a named bot, then a DM or group. Group invitations currently use existing
workspace user IDs. Link a conversation to a project when its explicit work
requests should enter that project's canonical tasks. Messages, audience and
explicit memories persist locally; hiding memory suppresses future recall and
stale publication without erasing original messages. A new conversation never
inherits another DM's memory merely because it uses the same bot.

Configure the existing front-model settings deliberately for a local compatible
provider. With no provider configured, jobs report Needs configuration and produce
no invented answer. Intake always persists before provider work. Model calls are
bounded; actual latency depends on the configured provider.

`/task Title` followed by an optional exact body creates a canonical project task.
Only that request is shared with the project. An accepted configured runner can
receive durable admission without blocking other chats; no runner leaves the
card available with a truthful reason. Admission, launch and completed task work
are separate. Open the task for its current progress.

Workspace owners can open `#/compute` for public-key enrollment metadata, passive
scopes, exact action proposals, approval and cancellation. New enrollments remain
pending verification. Private keys never go into these forms. Independently
signed verification, protected credential provisioning and participating local
and gateway authority admission are required before remote effects can be enabled.
The default installation does not scan networks or dispatch remote operations.

Trusted host startup can select
`WorkspaceExecutionConfig(admission_mode="controller-ssh-permit-v1", ...)`
alongside its explicit enabled flag, protected files and independently pinned
provisioning verifier keys. The forced SSH helper must independently select that
same mode. This is constructor configuration, never a browser field. Changing
the mode changes the action digest and requires fresh approval. Existing local
authority leases do not certify remote admission; the adapter performs a fresh
one-use permit round trip over pinned SSH. A permit already issued is in flight,
and an unknown outcome after the stage-4 exec boundary retains its reservation.
Earlier stages prove the requested operation did not run.

The Passive observations section shows configured availability and persistent
collection history. Collection requires separate trusted passive admission: set discovery_enabled=True
and explicit discovery_sources (linux_arp and/or ip_neigh), with the trusted
admission_mode above. The independently configured helper must enable passive
admission in the same mode, protect its own scope ceiling/current resolver and
supply only approved caches. A separate signed gateway discovery receipt is
required. Execution can remain enabled=False in a discovery-only workspace;
enabling fixed-operation execution alone does not enable collection. Scope ceilings are
charged permanently at intake, including unused or cancelled reservations.
Unknown attempts retain their slots and never automatically recollect.

## Reconcile an uncertain execution

After independently confirming termination or a non-run on the target, the
trusted service operator can release one uncertain execution's reservation:

```sh
uv run python -m commonplace.compute.operator \
  --control-db /absolute/data/control.sqlite3 \
  --authority-directory /absolute/private/authority \
  --workspace-directory /absolute/data/workspaces/WORKSPACE_ID \
  --workspace WORKSPACE_ID \
  --note 'Operator checked target evidence and confirmed termination' EXECUTION_ID
```

Use the existing provisioned paths, service UID and workspace crypto context.
Before attaching any participant, the command requires every expected guard
on all four controller files, including control. An absent or unknown guard
refuses the operation without installing guards into an unconfigured workspace.
It opens no wrapping key or credential contents, creates no replacement control
plane, and sends no remote commands. It requires an uncertain row and a
nonblank note of at most 1000 characters. The private journal stores the note
(no secrets), an audit event and terminal reason operator_settled. It preserves
replay denial and does not publish withheld output or assert an observed exit.
Elapsed time alone is not external evidence. There is no browser or HTTP settle
operation. Recovery below stage 4 and confirmed non-runs release automatically;
real unknown outcomes keep their reservation until this reconciliation.

The same command settles uncertain controller discoveries with `--kind discovery`
and the discovery id in place of EXECUTION_ID. It requires state uncertain and
slot=1, records the note and an append-only discovery audit event, and sets terminal
reason discovery_operator_settled with slot=0. It works after a worker restart,
retains attempts/permits and permanently charged scope usage, and publishes no
observations. A valid permit-bound completion also releases its slot even if
current authority forbids publication; no durable permit means a failed non-run.

## Recover helper journal capacity

Run on the helper host as its service operator, after independently stopping or
confirming quiescence of the named helper invocation. Controller settlement does
not reconcile a separate helper journal. A failed route open releases automatically;
so does any relay ending once target.close succeeds. A close failure or a
crash-orphaned consumed row stays counted until this reconciliation:

```sh
uv run python -m commonplace.compute.operator --kind tunnel \
  --journal /absolute/private/helper-replay.sqlite3 \
  --workspace WORKSPACE_ID --gateway GATEWAY_ID \
  --note 'Operator confirmed helper process and sockets stopped' EXECUTION_ID
```

Use `--kind passive` and its discovery id for a counted passive helper row. Both
operations serialize with helper admission using the journal's BEGIN IMMEDIATE
fence, require a named consumed/uncertain row, append the private operator note
to the helper audit, and mark operator_closed. Replay remains denied and late
completion cannot re-reserve the row. No controller files or keys are needed on
the helper host; its existing journal and parent directory must be service-owned
and private (0600 and 0700). The command never creates a replacement journal.
A closed socket proves no target process termination. Confirm external evidence
separately before settling a controller execution that reached stage 4.

## Verification

Run focused checks with pytest. Custody and concrete fence fixtures require
native POSIX ownership semantics. Environments that remap ownership must fail
their safe-open preconditions; do not weaken ownership validation.

```sh
uv run --extra compute --extra sqlcipher pytest -q \
  tests/test_compute_fence.py tests/test_compute_executor.py tests/test_compute_admission.py \
  tests/test_compute_passive_admission.py tests/test_compute_discovery_lifecycle.py \
  tests/test_compute_credentials.py tests/test_compute_ssh_fixture.py
uv run --extra compute pytest -q \
  tests/test_compute_discovery.py tests/test_compute_transport.py \
  tests/test_compute_scopes.py tests/test_coworker_dispatch.py
```

Optional Chromium tests are `tests/test_coworker_browser.py` and
`tests/test_compute_browser.py`. Supply Playwright and its browser cache explicitly
before pytest, whose fixtures isolate HOME.
They use real app/gateway/storage with synthetic providers and public enrollment
fixtures, not real endpoints, operator keys or paid model calls.

## Workspace scripts

The owner Workspace scripts section saves immutable Python source, arguments and
relative output paths, then exposes source/manifest inspection and a separate
exact execution proposal. Scripts remain unavailable without trusted
scripts_enabled, ScriptRuntimeConfig and a separately signed runtime receipt;
private provisioning and verification are not browser actions. See
script-integration.md for the contract and operator prerequisites. No host-shell
fallback is supported. The actual positive confinement fixture remains unverified
unless explicitly provisioned; signed synthetic receipts prove wiring only.

The target keeps unconfirmed or uncertain claims as operator evidence, including
after request expiry. Automatic pruning needs an expiry more than300 seconds
past and a protected, parseable result.json whose termination_confirmed field is
the JSON boolean true and whose state is observed_exit, denied or cancelled.
Malformed/unreadable results and legacy claims without expiry remain counted
toward the64-claim limit. Elapsed time does not establish termination.

Scratch admission requires at least16 available inodes and1MiB of available block
space on the protected scratch filesystem, checked before any claim/job creation.
Low resources or an unreadable filesystem-statistics query return
sandbox_unavailable; authenticated controller handling records a known non-run
and releases the reservation. The check does not reserve space against concurrent
jobs. After confirmed termination, iterative cleanup removes trees within the
4096-entry ceiling without a depth cutoff or descriptors per ancestor.

A lost control channel is cancellation intent at the target. The target kills
the process group, polling emptiness after child/pipe exit within the original
one-second kill bound. The final observation determines state and confirmation;
a populated group stays uncertain/unconfirmed with scratch retained. Cleanup
runs only after observing emptiness. Before spawn it records confirmed
cancellation without starting a process. Setup/spawn
failures with no process record confirmed non-runs. These confirmed target claims
can be pruned after expiry/grace. A lost result acknowledgement still leaves the
controller uncertain and requires the reconciliation procedure above; inspect
target evidence before it expires. Cancellation binding mismatches remain errors.

If scratch remains occupied after an actual filesystem error or unconfirmed
termination, stop admissions and independently confirm the relevant processes
have ended before removing their scratch data. Preserve claim/result evidence
and reconcile uncertain controller runs with the operator command above. Never
remove an unconfirmed claim merely to recover capacity; restore adequate scratch
headroom before resuming admissions.
