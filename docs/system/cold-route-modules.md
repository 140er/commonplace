### Cold route modules and first-hit API work

The authenticated shell previously waited for every controller's static imports before requesting identity. The entry now imports shared shell helpers and selects route controllers on navigation. The asset response emits a flat `modulepreload` closure for the shell; an asynchronous route selector preloads only the current route's closure alongside it, without blocking the entry if prefetch fails. Closed chat panes load their transcript controller when opened. Imports share an in-flight promise and failed imports can retry. A route generation check rejects late navigation completions. Direct card and classic default routes resolve against the cheap shell directory; project cards and that directory load in parallel.

Shared CSS and `deck-prepaint.js` retain their order before `app.js`. Other styles load for the named route, and content waits for those styles before rendering. A direct card loads its backdrop's styles too. The initial document shows “Loading Commonplace…” until identity resolves; only a real 401 selects sign-in. The first `/api/me` attempt still gets ten seconds after module evaluation, followed by three three-second attempts with 1/2/4-second backoff. The short optional bootstrap API budgets remain three seconds; ordinary card, memory, list and search budgets remain unchanged. Route styles have a ten-second deadline and an explicit Retry on failure.

Static files use content ETags and `Cache-Control: no-cache`; matching validators return 304. A file whose name actually contains its content hash gets `public, max-age=31536000, immutable`. This change uses unbundled modules and revalidation, without a generated bundle or a build dependency.

The API trace found repeated first-hit work rather than a memory-worker dependency: card projection joined each row individually, then created each missing share link in its own transaction; the roster's decision badge built unrelated setup attention; the queue rechecked the same wing for every card; and an initial owner event read replayed all historical rows. Card joins are now batched, URLs share one transaction with lookup batches of 400, wing checks are scoped to the request, and the badge requests the card-only queue. The initial owner feed selects the latest 200 rows with the workspace/sequence index, returns ascending order and advances to the current cursor. Incremental reads and member ordinal feeds keep their existing semantics. Optional shell lists retain their existing client-side stale-while-revalidate behavior; authorization and complete card contents are never replaced by a shared stale projection.

A disposable Chromium fixture, 4× CPU throttling, 100 ms network latency and 1.25 MB/s, measured an empty browser context per route, a signed-in synthetic workspace and one card. These are visible shell/key-element observations, not the document's loading-placeholder paint. The baseline is develop `9ca4c069`; times below are one measured set, rounded to milliseconds.

| Route | Shell before / after | First key element before / after | JS completed before shell before / after |
| --- | --- | --- | --- |
| Settings | 2874 / 1309 ms | 3269 / 1760 ms | 81 / 32 |
| Project board | 2864 / 1294 ms | 3183 / 1501 ms | 81 / 34 |
| Bots | 2884 / 1264 ms | 3125 / 1571 ms | 81 / 34 |
| Memory | 2879 / 1244 ms | 3048 / 1578 ms | 81 / 31 |
| Card panel | 2935 / 1237 ms | 3026 / 1563 ms | 81 / 36 |

A separate disposable HTTP fixture contained 1,000 cards and 20,000 historical events. The table reports each endpoint's first request from the same ordered workload; subsequent samples are retained in the measurement output. Reproduce it with `python -m tests.cold_api_measure` (an optional archived-source directory measures a baseline).

| Request | First read before | First read after |
| --- | --- | --- |
| `/api/boards/project/cards` | 319.12 ms | 101.53 ms |
| `/api/bots` | 60.64 ms | 32.33 ms |
| `/api/events?after=0&epoch=` | 73.27 ms, 20002 rows | 1.46 ms, 200 rows |
| `/api/needs-me` | 60.35 ms | 21.31 ms |

Reproduce the browser trace with `python -m tests.cold_modules_browser --output /tmp/cold-modules.json --assert-lazy` using the release browser dependencies. The fixture creates its own instance, users and data and never targets an existing service. `tests/test_cold_modules.py` covers graph exclusion, import coalescing/retry, preloads, cache validators and event resumption; `tests/test_cold_api_work.py` pins query and transaction budgets without wall-clock assertions. The initial graph/cache/event regressions failed twelve times on develop; all four repeated-work cases also failed on an archived develop fixture.

Remaining work: the project owner must remeasure the large live workspace after release. Complete card lists and project readiness still scale with actual records; member event pages retain their authorized ordinal counting; shared shell presentation helpers and the Deck appearance renderer remain in the core graph. A production bundle and pagination would be separate changes. This fixture does not model a 22/24 GB store, and no production measurements are claimed here.
