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
Cancellation sends intent; lost permit/commit/result acknowledgements reserve
uncertainty and never automatically repeat work. Canonical tasks are not completed
by a script result. Only bounded redacted output and artifact metadata persist.

Synthetic signed receipts and the WireOnlyTarget socketpair fixture prove
controller/API/transport wiring. They never launch host scripts and do not prove
actual confinement. A disposable operator-provisioned non-root namespace/cgroup/
tmpfs/read-only runtime fixture is still needed for a real positive test. The
runtime contract names only implemented controls; no seccomp filter is claimed.

## Local verification limitation

Read-only coordinator-host checks on2026-11-09 found effective UID1000,
unprivileged_userns_clone=1, AppArmor's unprivileged-userns restriction=1,
a non-writable /sys/fs/cgroup and no configured disposable sandbox fixture.
These fail the mandatory local runtime preconditions. No kernel setting, mount,
cgroup delegation or privileged host configuration was changed. Actual confined
execution needs a separately provisioned disposable fixture and independent
runtime verification; the application must stay disabled without it.
