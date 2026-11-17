### Bound the gateway allocator and retain an opt-in allocation diagnostic

The gateway runs one HTTP thread per connection. Unlike its memory workers, its
container image did not cap glibc's allocator arenas. Freed request buffers could
remain in those arenas even when the Python objects were gone. This reproduces a
source of gateway anonymous-memory growth; it does not establish that all growth
observed on a live instance has the same cause.

Decision: the gateway image starts with `MALLOC_ARENA_MAX=2`, matching the workers.
It is an image environment default, so an operator can override it. Setting it
inside an already-running Python process would be too late. This changes neither
stored content nor SQLite page-cache limits, and does not restart or recycle the
gateway. Fewer arenas can trade allocation concurrency for lower retention.

Decision: also ship `COMMONPLACE_MEMTRACE=1`, off by default. The gateway starts a
background tracer with five frames, retains one preceding snapshot and logs at most
ten positive file:line allocation differences every ten minutes. Application paths
are relative; other paths are reduced to filenames. Only locations and numeric
sizes/counts are logged. Failure messages are fixed. A tracer started elsewhere is
reused and left running. The Admin deploy payload adds `memory.gateway.thread_count`
and `memory.gateway.open_fd_count`, with unknown counters null and native Linux
thread counts preferred over the Python fallback.

#### Controlled transcript-upload experiment

Temporary local gateways, 25 synthetic workspaces, a separate stub memory worker,
eight request threads and five batches of 120 uploads each, using the same workload
generator and seeds. One tracing frame was enabled after seeding to compare live
Python allocations with anonymous process memory. These figures include legitimate
SQLite page caches; they are not a prediction of production memory consumption.

| Completed uploads | Original arena default, anon MiB | Two arenas, anon MiB |
| --- | ---: | ---: |
| 120 | 246.51 | 138.04 |
| 240 | 347.12 | 153.01 |
| 360 | 483.79 | 158.18 |
| 480 | 474.07 | 173.65 |
| 600 | 635.16 | 175.56 |

At 600 uploads, live traced allocations were 933.3 KiB with the original default
and 955.0 KiB with two arenas. Explicit heap trimming reduced anonymous memory from
635.16 to 288.06 MiB in the original run, and from 175.56 to 120.48 MiB in the capped
run. Shrinking open thread stores then gave 260.89 and 123.01 MiB respectively
(background work can resume between measurements). The capped endpoint used 72.4%
less anonymous memory before trimming. No periodic heap trim was added to the
request path.

The native regression starts a real gateway child with the image's allocator
environment and sends concurrent transcript uploads, then reads glibc's actual heap
count after each batch. With the original image setting it fails at **31 arenas**
against a maximum of two. It skips on platforms without glibc allocation statistics.
The new Admin payload assertion independently fails against the original memory
view because the two counter fields are absent.

#### Scope and remaining validation

The earlier untraced eight-minute mixed run sent 52,000 requests: anonymous memory
rose from 121.5 to 132.3 MiB, Python threads stayed at 18 and descriptors settled at
167. Its final two minutes grew by about one MiB. It did not establish a sustained
production-like leak or prove the deployment target.

The audit also checked the bounded rate-limit buckets, finite shipped-template
cache, fixed startup listeners, request-scoped callbacks, bounded private-reader
cache and lazily opened workspace stores. No additional unbounded request-keyed
Python container was demonstrated. Real model inference, provider streams and
production data were not used. A real memory-search timeout reported during this
investigation is an independent symptom, not proof of this allocator cause.

After an approved deployment, sample gateway anonymous memory, thread count and
descriptor count once a minute over comparable traffic. The target is below
**20 MiB/hour after warm-up**, without tracing. The live-instance slope remains to
be measured. `deploy/commonplace/DEPLOY.md` explains enabling the diagnostic,
reading the log, and removing the switch after capture. Snapshot comparisons measure
live Python growth, not native allocations or total resident memory; tracing itself
adds substantial overhead.

#### Per-route before/after growth

Each route receives two passes of 45 requests through four clients, across the 25
seeded workspaces. Figures below are the **second pass's change**, with 25 tracing
frames in both runs; they include native allocator and tracing overhead. The first
pass is warm-up, but mixed MCP calls still open workspace/tool combinations during
the second pass, so their row is not a fully warmed plateau measurement. Anonymous
and traced changes are different quantities; small negative values are collection
or cache release, not lost stored data.

| Route | Anon MiB before | Anon MiB after | Traced KiB before | Traced KiB after |
| --- | ---: | ---: | ---: | ---: |
| `POST /mcp (mixed tools)` | 70.52 | 64.67 | 11685.4 | 11608.8 |
| `GET /api/events (poll)` | 0.01 | 0.00 | -2.0 | 16.6 |
| `GET /api/needs-me` | 0.14 | 0.00 | -5.2 | 8.5 |
| `GET /api/cards` | 0.30 | 0.00 | 7.2 | -23.8 |
| `GET /api/boards[/<slug>/cards]` | 0.10 | 0.12 | 10.9 | -15.6 |
| `GET /api/cards/<n>` | 0.01 | 0.00 | 7.2 | -3.7 |
| `GET /api/chat/<slug>/status` | 0.00 | 0.00 | -5.3 | -3.2 |
| `GET /api/memory/search` | -0.01 | 0.02 | -121.6 | -31.0 |
| `GET /api/me` | 0.77 | 0.00 | -27.0 | -0.3 |
| `GET /api/bots` | 1.37 | 1.28 | 43.9 | 42.7 |
| `GET /api/admin/instance/deploy` | 0.73 | 0.00 | 4.7 | 8.3 |
| `GET /api/connections` | 0.78 | 0.55 | 8.6 | 20.6 |
| `GET /api/workspaces` | 0.02 | 0.00 | 1.8 | 4.4 |
| `GET /api/plan` | 0.07 | 0.00 | 1.7 | 0.6 |
| `GET /api/console` | -0.01 | 0.00 | 1.4 | -12.5 |
| `GET /app/login (invalid token)` | 0.02 | 0.02 | -5.2 | -9.9 |
| `GET /app/ shell + static` | 0.06 | 0.00 | -14.1 | -32.3 |
| `POST /api/boards/<slug>/cards` | 0.68 | 0.59 | 77.0 | 9.2 |
| `POST /api/cards/<n>/comments` | 0.00 | 0.12 | -5.5 | -15.7 |
| `POST /api/capture (transcript upload)` | 50.20 | 30.11 | 86.7 | 62.9 |
| `GET /api/cards/<missing> (404)` | 1.16 | 0.01 | 37.7 | -15.2 |
| `GET /api/events bad token (401)` | 0.12 | -0.04 | 12.4 | 45.4 |

The successful chat read measured +0.13 MiB anonymous / -27.3 KiB traced after the
change; its earlier input combined incompatible cursors and returned 400, so those
two rows are not compared. Memory rooms measured -0.01 MiB / +8.0 KiB after the
change; the earlier nonexistent status URL exercised an error path instead.
The initial harness was corrected to isolate its admin socket, use a valid chat
cursor, serve worker busy responses with the real protocol code, and avoid filtering
entire 25-frame snapshots in the control request. These are harness corrections.

All comparable successful read routes returned 200 in both passes. Card creation
returned 201, invalid login links 302, missing cards 404 and bad tokens 401. The
mixed MCP group completed 85 HTTP responses and hit five ten-second client timeouts
in each run. Those timeouts are retained as experiment limitations, not hidden from
the comparison. A separate focused load exercises actual 503 retry responses.

#### Fifteen-minute allocation comparison

The original-arena mixed-load run completed with 25 tracing frames. Baseline:
2.18 minutes / 1,760 requests; final load sample: 15.37 minutes / 12,480 requests.
Live traced memory rose from 43,273.6 to 43,751.1 KiB (+477.5 KiB), while anonymous
memory rose from 526.67 to 831.82 MiB. Snapshot analysis itself subsequently raised
anonymous memory to 894.55 MiB; this is why the deploy target must use an untraced
run. FD count settled at 166–167; SQLite connections reached 52, sockets six and
asyncio tasks zero. Python threads increased from 26 to 41 and native threads from
32 to 47 as the workload touched more workspace services. GC objects increased
from 120,932 to 124,552. These short-run counts are observations, not proof that
all resources remain bounded indefinitely.

Largest positive line differences between those snapshots:

| Allocation site | KiB growth | Allocation count change |
| --- | ---: | ---: |
| `json/decoder.py:353` | 70.4 | 872 |
| `copy.py:221` | 36.3 | 285 |
| `thread_store.py:345` | 25.9 | 259 |
| `copy.py:218` | 25.6 | 409 |
| `store.py:348` | 19.9 | 191 |

The JSON/copy traceback includes the gateway worker response and bounded tools
schema paths. None of these live Python sites explains the hundreds of MiB of
anonymous growth. The separate allocator experiment and native arena regression
provide the evidence for the container default change. Captures append legitimate
persistent data; their SQLite caches still grow toward their existing bounds.
