### HTTP restart containment

All HTTP command entrypoints use one monitor and a runtime child; Commonplace's
workspace launcher is already a supervisor and selects internal child mode.
The monitor opens no storage, model or client. Its allocation is fixed imports,
one child and current generation facts; prior child objects and request records
are discarded. This is a restart contract for containment. It does not establish
the production memory-growth root cause; production attribution remains open.

The child owns the HTTP listener and writer lease. A parent-owned atomic marker
publishes starting, running, restarting, draining, cooldown or orphan_draining,
with PID/start identities and no token. Local proxies return retryable unavailable
immediately during gaps, including proxies first started during the gap; they
never open local storage behind a live monitor. Configured local-only operation
without a managed hub remains supported. Cleanup removes only the owner's record.
An expired parent marker cannot authorize takeover of a live recorded child.
A second monitor serializes startup and waits for an orphan's PID/start identity
to disappear, rechecking every 60 monotonic seconds without adopting it. A live
PID with an unreadable or missing start identity remains an orphan; missing
identity is not permission to launch over it. Marker publication failures retain
the current child and retry publication on the next tick; the monitor logs a
warning and recovery without request data. A new child is not launched until
its initial restart marker is published. Shutdown signal handlers only set a
stop flag; the ordinary monitor loop performs locking and drain work.

HTTP replies carry X-Mempalace-Generation, X-Mempalace-Admitted and
X-Mempalace-State (RUNNING or DRAINING). A successful DRAINING reply is final.
Refusals use HTTP 503, X-Mempalace-Dispatch: not-started,
X-Mempalace-Reason: recycling or busy, and generation identity. The JSON-RPC
error is -32071 with dispatch_started=false, generation and reason; a refusal
before body read has a null id. Only authenticated matching-generation proof
allows the gateway one retry after releasing its pin, draining other pins and
reaping the old child. Busy refuses without restart. Missing/mismatched proof,
connection loss, body loss and post-dispatch errors have uncertain outcome and
never automatically replay mutations. No new public route is added.

Each accepted transport holds a completion token through the final response
write and flush, including lock-free handlers. Storage admission and completed
successful storage calls are generation-local. Parent death closes a dedicated
private liveness pipe: EOF closes admission, drains all accepted responses,
runs registered cleanup and exits. Another private pipe sends at most three
versioned facts per generation, each at most 512 bytes: ready, completed_16 and
terminal retiring (admitted/completed, reason and RSS-source enum). Writes are
nonblocking where the runtime supports it and best-effort; missing facts stay
unknown. On older Windows Python without pipe set_blocking, the blocking
fallback is bounded to three records totaling at most 1,536 bytes, fitting the
private pipe buffer; lack of that API does not prevent startup. POSIX explicitly passes
only child pipe ends; Windows explicitly inherits those handles. No request
content, arguments, results, credentials or external telemetry enter the channel.

The first standalone generation preserves fail-fast startup: before any child
has reached ready, bind failure (status 1) and writer-lease refusal (status 2)
exit the monitor with the child's status and a clear log message. Starting a
second monitor for the same palace exits 2 with an already-running message,
not an uncaught lock exception. Once a generation has reached ready, later
failures retain supervised recovery. Latch, cooldown, recovery-probe and running
transitions are logged without request data.

The persistent restart owner counts launcher errors, other before-ready exits, startup
deadline expiry, unexpected death and confirmed RSS retirement after at most one
storage admission. Three consecutive failures latch for 60 monotonic seconds.
The standalone monitor then makes exactly one timer-driven probe; a failed probe
latches for another 60 seconds. Commonplace instead probes on its next request
through single-flight. At least 16 observed successful completions reset failures;
count-budget retirement is not a failure. HTTP idle expiry closes admission and
emits an idle retirement fact through the same completion coordinator, so a
confirmed idle exit is not an unexpected death. JSON-RPC error replies do not
count toward successful completions. Restarting the supervisor clears this
in-memory state. Backup pause and close override replacement. A pause that
arrives between get and pin retains the backup retry hint. A generation whose
reap wait times out stays marked retiring when it is re-registered. Replacement callers
wait at most 30 seconds; accepted/hung calls are not force-killed to make room.
Existing emergency write-stall exits still represent uncertain outcomes.

CLI search and mine also refuse a managed unavailable marker before capability,
health or local/daemon routing checks, including options that cannot be forwarded.
They print a retry-later message and exit 1; they never open local storage during
a live managed gap. Explicitly configured local-only operation remains possible.

On Windows, refusing before reading the body can produce a TCP reset that hides
the buffered 503. This is an accepted safe limitation: the consumer sees an
uncertain transport outcome and does not automatically replay; the proved retry
may simply not occur. No request body discard or mutation replay is added.

Operator note: after both monitor and child have exited, no live marker remains,
so discovery allows local execution until a monitor returns. Start the supervised
hub before its clients when the deployment requires forwarding-only operation.

Operator note: older proxy binaries ignore monitor markers and can execute
locally during gaps. Upgrade hub and local clients together before relying on
the restart/no-fallback contract; mixed-version containment is not guaranteed.

No count/RSS retirement, RSS default or eight-body limit is enabled here. Soft
RSS policy, uniform stdio admission/idle handling and host reconnect verification
belong to the later policy change. Stdio clients receiving a final response must
reconnect after clean worker EOF; no framing supervisor is introduced. Embedded
library/daemon calls remain outside this serving-only lifecycle. No per-probe
helper or startup-injection bookkeeping is added; existing bounded metadata and
capacity-cache budgets remain unchanged.

For reproducible scratch measurements, run the focused fixture-only process
smoke and record the git revision, Python/dependency versions, fixture/config,
PID/start identity, successful call count and monotonic child-kill-to-replacement
ready interval. Sample monitor and worker /proc/<pid>/status separately at first
ready, after identical cheap calls and after restart: VmRSS, RssAnon, RssFile and
VmHWM; report end-minus-start and warm drift per replica. The stable monitor
baseline is separate from worker storage/model RSS. Missing monitor measurements
on a revision without a monitor are unavailable, never zero. Timing/RSS are
informational and never test pass thresholds. The process smoke is reproducible
with `uv run --extra dev pytest tests/test_http_monitor_process.py`; it uses
isolated scratch homes, a fixed synthetic reply and no model/provider calls.
