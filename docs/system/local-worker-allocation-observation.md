### Local worker allocation observation

Storage-serving MCP entrypoints enable metadata-only allocation observation by default;
`MEMPALACE_WORKER_MEMORY_OBSERVE=0` opts out. Imports, durable jobs and forwarding-only
proxies do not activate a storage observer. HTTP startup activates it before
preflight and optional eager warmup; later capacity probes reuse it. Other
entrypoints activate it on the first local storage dispatch.
Each activation has a random generation;
call ids are local counters, never client request ids. Fixed startup phases and actual
cache, collection, restricted-pickle and native-session branches report only observed
facts. Missing samples and untouched components stay null. No payload, result, content
hash, identity, path, arbitrary name, exception text or configuration value is recorded.

The resolved config directory owns private `diagnostics/worker-memory.jsonl` and one
backup, each at most 4 MiB. A stable nonblocking cross-process lock serializes append
and rotation; files are opened inside that lock. Symlink and nonregular destinations
are refused. There is no per-call stderr or general-log copy. At most 64 primitive
contexts and 128 serialized records of at most 4096 bytes are retained in memory.
Token-construction failures drop observation, release the active count and slot,
and let the request continue. Observer-construction failure is latched for the
process lifetime and counted in a saturating activation-loss counter, without
retrying on every request. End RSS source/status describe the same end read.
Begin and end delivery is asynchronous and best effort, without a producer-side
acknowledgment wait. A present unmatched begin is usable evidence that a scope
began; an absent begin proves nothing. A busy delivery lock, full queue or failed
sink drops observations and increases the saturating loss count without stopping
storage work. Idle HTTP startup now creates the diagnostics directory and queues
startup records even before a memory call. Shutdown attempts
only a bounded 50 ms flush. This adds no network destination, database write, memory
limit change or restart policy. Whole-process RSS deltas during overlapping calls
are not uniquely attributable. Rotation, losses and unmatched pairs leave unknown
coverage: these observations alone do not establish or exclude a production cause.

Observation does consume CPU on the request thread: a synthetic interleaved trivial
call measurement found median 3.18 ms disabled versus 3.98 ms enabled (about
+0.8 ms per call). A slow sink did not add request-thread waiting in that probe.
These measurements are informational, not a merge threshold or a guarantee of
zero latency overhead. Concurrent producer contention and a full queue can drop
records; coverage claims require loss counts and complete begin/end pairs for
the exported interval, not only sequential scratch results.
