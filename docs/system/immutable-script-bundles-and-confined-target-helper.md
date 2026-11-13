### Immutable script bundles and confined target helper

The workspace.script.v1 component stores exact UTF-8 Python source in a
crypto-aware immutable bundle store (32KiB/script,256 bundles,100 listed).
Manifests bind existing identities/enrollment, exact script/runtime digests,
arguments, input hashes/paths, output paths, unprivileged runtime and finite
resource limits. Value construction grants no authority. Owner script REST and existing Compute approvals/ExecutionService now consume
these bundles. Trusted scripts_enabled and ScriptRuntimeConfig bindings require
an independently signed ScriptRuntimeReceipt, accepted only through the private
offline verifier seam and stored in guarded enrollment_verifications.script_runtime.
No public request supplies runtime identity, confinement flags, roots or keys.

A separately provisioned target helper uses a fixed SSH command after the pinned
inner connection. Its strict begin/ready/chunk/commit/result/data/done exchange
is bounded by64 frames,192KiB and40 seconds, with exact digest checks and a
separate cancellation frame. Private atomic uploads use protected openat paths;
durable target claims prevent replay of incomplete or restored attempts.
The64-claim cap counts retained claims: finished claims with recorded expiry and
a protected, parseable terminal result.json with termination_confirmed exactly
true can be pruned only more than300 seconds after expiry. Unconfirmed/uncertain,
malformed/unreadable, unfinished, unexpired and legacy claims remain fail closed.
Before creating a claim, scratch needs16 available inodes and1MiB available block
space; low resources/statistics failures deny as sandbox_unavailable, an
authenticated known non-run. This check does not reserve space across jobs.
After confirmed termination, iterative cleanup restores directory permissions
and removes up to4096 entries without a depth cutoff or descriptors per ancestor.
Cleanup I/O errors preserve observed results; later admissions still require
resource headroom.
Lost control-channel receives become target cancellation intent, including before
spawn. After child/pipe exit, group emptiness is polled within the remaining
original one-second kill bound. One final observation determines state and
confirmation; populated groups remain uncertain/unconfirmed with scratch
retained. Running groups must be observed empty before scratch cleanup; failures
with no spawned process are confirmed non-runs. Target result persistence/cleanup
does not settle a controller run whose result acknowledgement was lost.

Execution defaults off. It requires a non-root capability-free host identity,
mandatory namespaces, no network, a digest-bound read-only root-owned runtime,
bounded tmpfs, delegated cgroup-v2 CPU/memory/pids controls and descendant kill.
No raw host-shell fallback exists. Missing provisioning or kernel features deny.
Results distinguish observed exit, cancellation intent and unknown termination;
controller projections redact bounded output and expose artifact metadata only.
Actual positive confinement has not been verified without a separately
provisioned disposable fixture; deterministic wire/storage tests do not prove it.
The owner script UI follows the frozen REST surface for exact UTF-8 upload,
source/manifest inspection, replay-safe creation and immutable bundle proposals.
No runtime roots, verification flags, keys or upload inputs appear in the form;
real API/browser tests cover upload, source inspection, immutable proposal,
exact approval and reconnect using signed synthetic runtime metadata. Actual
outer/inner SSH wire fixtures never execute host scripts. Logical manifest epoch
is None; the fresh upload request carries authority epoch and approval digest,
covered by the outer immutable operation and purpose-bound one-use permit.
Old integer-epoch script documents and the fixed-probe wire remain unchanged.
