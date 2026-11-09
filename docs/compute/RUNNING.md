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
and a lost response does not release its durable reservation.

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
