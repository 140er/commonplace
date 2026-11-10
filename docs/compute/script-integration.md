# Approved workspace script integration

Source3ab9fe17b5d110762b1024c3a69111c4dfaf6589, integrated2b380ed5.
This is feature implementation, not a review, deployment or confinement verdict.

Owner-session REST preserves exact UTF-8 source:

- GET /api/compute/scripts returns at most100 authorized bundle manifests.
- POST /scripts accepts node_id, task_number, request_id, source, arguments and
  output_paths;201 creates,200 replays the same exact payload. The body is at
  most64KiB, source32768 UTF-8 bytes,16 arguments of256 bytes each and8 safe
  relative output paths. Input uploads and runtime/root/key fields are rejected.
- GET /scripts/<id> returns its manifest and original source after current
  owner/task/object checks.
- POST /executions accepts workspace.script.v1 and arguments exactly{bundle_id}.
  The server binds the immutable manifest and runtime receipt into the existing
  exact proposal digest, then rechecks them before approval and dispatch.

Trusted WorkspaceExecutionConfig adds scripts_enabled=False and script_runtimes
of ScriptRuntimeConfig(node_id,runtime_digest,key_principal). key_principal is the
64-character hexadecimal SHA256 of the actual target-controller SSH public wire
bytes. ScriptRuntimeReceipt uses the existing independently pinned provisioning
keys and binds target enrollment/receipt, runtime inventory, key identity,
contract and expiry. Only ExecutionService._accept_script_runtime accepts this
receipt; HTTP cannot verify a runtime or enable a capability.

Logical ScriptBinding.authority_epoch=None keeps approved source/arguments/paths/
runtime/enrollment immutable across proposal and approval mutations. Fresh
ScriptRequest authority_epoch/approval_digest and immutable attempt are bound to
outer ExecutionRequest arguments{bundle_id,manifest_digest,script_digest} and
its operation-specific one-use SSH permit. Old integer-epoch request documents
remain unchanged. A script permit cannot authorize another operation.

After independent outer and inner SSH pins/authentication, transport invokes only
TARGET_HELPER_COMMAND and strict ScriptWire transfer. Target safe upload, claims,
confinement and result semantics remain in target_helper.py/script_sandbox.py.
Cancellation sends intent. Failures before target authentication settle as known
non-runs, including lost permit acknowledgements. After target authentication,
lost commit/result acknowledgements reserve uncertainty and never automatically
repeat work. Authenticated denied/cancelled results with confirmed termination
and the documented pre-claim script_error codes settle as known non-runs; unknown
helper errors keep the reservation. See execution-contract.md for the exact codes
and trusted operator reconciliation. Canonical tasks are not completed by a
script result. Only bounded redacted output and artifact metadata persist.

Targets retain at most64 claims at once. New claims record request expiry; under
the target admission lock, a finished claim can be pruned only after its expiry
plus a five-minute grace period and only with a protected regular result.json.
Unexpired, recently expired, unfinished and legacy claims without expiry metadata
remain, including across restarts. Expired requests are rejected before pruning;
requests still eligible for acceptance retain replay denial. Unknown claims can
still exhaust capacity and require independent operator reconciliation.

After confirmed termination, scratch cleanup restores owned directories to0700
and walks at most64 levels/4096 entries. Cleanup failure cannot replace the
observed exit, output or stored result metadata. A tree beyond those bounds or a
filesystem error can leave scratch data in the bounded tmpfs for operator cleanup.

Synthetic signed receipts and the WireOnlyTarget socketpair fixture prove
controller/API/transport wiring. They never launch host scripts and do not prove
actual confinement. A disposable operator-provisioned non-root namespace/cgroup/
tmpfs/read-only runtime fixture is still needed for a real positive test. The
runtime contract names only implemented controls; no seccomp filter is claimed.

## Confinement verification requirements

Actual confined execution requires a separately provisioned disposable non-root
fixture with namespace support, delegated cgroups, bounded tmpfs and a verified
read-only runtime. The positive test skips only when no fixture is configured;
a configured fixture must pass prerequisite checks and execute successfully.
Synthetic wire tests do not replace this verification. The application must stay
disabled without independent runtime verification.
