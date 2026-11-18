### Cold shell data does not wait for optional projections

The shell previously joined boards, the owner queue, the rules count and the bot
roster before dispatching the selected screen. Settings then joined six more
reads. The bot roster used the full board projection just to obtain names. That
projection expires claims and computes runner capacity and per-card waiting
reasons for each project, inside a write transaction. Settings wing discovery
can ask the memory worker; plan storage usage walks every regular workspace
file under a shared meter lock. These joins made unrelated screens pay the
slowest dependency. Bootstrap badge joins could keep a cold page waiting for an entire proxy
timeout. Ordinary card, list and search reads keep their previous timeouts;
they may legitimately take longer on a large workspace.

The initial board read is now `GET /api/boards?view=shell`: current readable
names and wings, with display counts from a small asynchronous snapshot. It
never expires claims or computes runner projections. The bot roster uses the
same directory without requesting counts. Detailed board summaries are fetched
only after the board or project list has rendered. The full board API and its
permissions remain available. The owner queue and rule/roster badges update
independently; queue screens still require the actual queue and Rules still
requires the actual rules. Settings replaces only the section whose read just
settled, preserving other sections' mounted forms and drafts. Bot conversations
open without waiting for the coworker directory or shared-bot management list.
Today also accepts queue, roster and activity independently.

Startup request inventory (requests depend on the selected route):

| Request | Server work | Role at startup / deadline |
| --- | --- | --- |
| `/api/me` | Session, workspace, people and admin capabilities in the control database | Authentication first; 10 s first read, 3 s retries; HTTP 401 alone opens sign-in |
| `/api/boards?view=shell` | Current active, policy-readable board metadata; asynchronous grouped board counts | Main directory; 3 s browser bound; counts refresh 3 s |
| `/api/boards` | Claim expiry, counts, runner capacity and waiting projections per project/card | Deferred until project main data has painted; original unlimited default |
| `/api/needs-me` | Board task/approval, coordinator, orchestration and model-role projections | Required queue content keeps its original budget; background badge has 3 s |
| `/api/rules/count`, `/api/rules` | Rule-store identity check and local SQLite count or rule rows | Background count has 3 s; full Rules content keeps its original budget |
| `/api/bots` | Authorized bot store, defaults, asks, readiness per project | Names from cheap directory; shell badge has 3 s; screen reads keep their original budgets |
| `/api/coworkers/bots`, `/api/coworkers/conversations`, `/api/coworkers` | Workspace-local directory and conversation stores | Independent of main content; original route budgets (10 s or unlimited) |
| `/api/bots/shared`, bot threads/messages | Bot lifecycle, access and bounded message pages | Shared management list is optional; selected thread follows personal roster; original budgets |
| `/api/boards/<slug>/cards`, `/api/cards/<n>` | Board/card projection in local stores | Required main data; card read already starts beside board refresh; original unlimited default |
| `/api/connections` | Current connection grants; optional memory wing index/worker fallback | A cold request joins one shared wing lookup for at most 1 s; stale wing hints return immediately |
| `/api/plan` | Account plan and transcription counters; workspace file-size walk | Counters return immediately; cached storage refresh 3 s, cooperative walk deadline |
| `/api/webhooks`, `/api/phone-push`, `/api/chat-settings`, `/api/settings/orchestration` | Local settings and usage stores | Independent Settings sections; 3 s each |
| `/api/voice` | Local voice configuration | Already started without awaiting it; original unlimited default |
| `/api/memory/rooms` | Policy-filtered note destinations; asynchronous worker prewarm | Memory paints before this optional request; original 8 s bound; no worker readiness wait |
| `/api/events` | Local event rows/cursor, filtered for members | Polling begins after routing; original budgets; Today activity is independent |
| `/api/workspaces` | Memberships and workspace-switch capability | Requested when the switcher opens, not at boot; original unlimited default |
| `/api/topology`, `/api/runners`, node detail, project/checkpoint reads | Local topology/runner/orchestration stores | Compute/project screens only; original route budgets (including 15 s compute reads) |
| MCP startup context / capacity probe | Separate MCP initialization (80 ms context budget) / memory-worker search path | Neither is called by shell metadata; worker warm-up remains asynchronous |

The fresh-user browser journey also reproduced a lock inversion: the rail's
`team_bots` read acquired the control database before the bot store, while the
normal bot roster held the bot store while consulting the sharing switch in the
control database. The rail now takes bot store then control database, retaining
the same authorization window and projection. A deterministic two-reader test
holds the roster lock, admits the rail, and checks that identity/settings can
still acquire the control database: pinned develop fails its 0.5 s bound; the
change acquires it immediately. This is a confirmed stall mechanism, although
assigning the exact live 60 s event still requires a production trace.

The complete-read deadline races transport **and** JSON decoding and aborts the
request even if the transport ignores abort. The shared API default remains
`timeoutMs: 0` (unlimited). The cheap board directory, optional shell badges
and independent Settings sections pass an explicit 3 s budget. Identity starts
with 10 s and uses 3 s on retry; see [identity bootstrap retry](identity-bootstrap-retry.md). Other reads retain their original unlimited or explicit 8/10/15 s
budgets, including memory, card, list, search and thread reads. Queue and Rules
content keep their original budgets when selected; only their shell badges
use the short deadline. No production trace assigns the observed 60 s outlier
to one exact request.

Display snapshots retain at most 256 entries, coalesce each key and allow at most
two simultaneous refreshes per gateway. Their cooperative deadline is 3 s, with
30 s reuse/backoff (60 s for storage), except that a failed wing lookup has no
backoff once it finishes. Loaders own their internal/transport timeouts. A loader
that ignores its cooperative deadline retains a slot until it exits; two such
loads make optional metadata degrade to retained stale values or loading. They
cannot accumulate threads or block main data. An expired result is discarded.
The shared storage walk checks `remaining()` only against an ambient deadline:
without one it returns its argument unchanged, so synchronous admission keeps
its original behavior. Counts and
wing hints are keyed by viewer policy, workspace, user, role and connection;
storage totals are workspace-scoped and shown only after the existing owner
check. Visible boards and connection grants are always read afresh. Missing
values are `loading`/`unavailable`, never fabricated zero totals; `wings` is
`null` while unknown, and `[]` only after a successful empty lookup; retained values
are `stale` while refreshing. No search, storage-admission or grant decision uses
these display snapshots.

Fixture evidence on pinned develop `d2b5d750`, with each suspicious dependency
replaced independently by a 30 s stand-in (loopback HTTP, disposable stores):

| Request / dependency | Before | After | What leaves the critical path |
| --- | ---: | ---: | --- |
| `/api/boards?view=shell` / full board projection | 30.0032 s | 0.0044 s | Expiry and runner/per-card projection |
| `/api/bots` / project-name projection | 30.1817 s | 0.0210 s | Full board listing used only for names |
| `/api/connections` / memory wing discovery | 1.0037 s | 1.0039 s | Settings no longer waits for the wing lookup to render other sections |
| `/api/plan` / workspace storage walk | 30.0027 s | 0.0014 s | File scan and storage-meter lock |
| Rail waiting on roster / control database for identity | Blocked for entire 0.5 s test bound | <0.0001 s | Opposite lock acquisition order |
| Shell main data / queue, rule count or roster | 30 s virtual clock | 0 ms virtual clock | Badge join before route dispatch |

`GET /api/connections` always returns the current `connections` rows. Its
`wings` field is an array with `wings_state: "ready"` after a successful read
(including `[]` for a known empty result), a retained array with `"stale"` during
refresh, or `null` with `"loading"`/`"unavailable"` when unknown. Clients must
check `wings_state` before treating the array as a completed inventory. Settings
shows loading, failure and retry messages; unknown states remain visible.
`agents.js` normalizes null in both `agentsView` and mutation refresh. The other
connection consumers, `agent-schedules.js` and `projects.js`, only use the
`connections` rows and ignore wings. The agent rendering/mutation harness and
Settings harness exercise null; route tests distinguish null from successful
empty arrays and retain the transport-budget and single-flight assertions.

Wing-budget follow-up: a cold wing request may wait up to the existing one-second
transport deadline so available results are returned in that response. Concurrent
cold callers join the same future and receive the same result. A stale successful
value still returns immediately during refresh. A timed-out worker retains its
physical single-flight slot until it exits, but its failure is not cached for
30 seconds: a later request can retry as soon as that read releases the slot.
Known empty results remain distinct from unknown/loading results. Regression
coverage includes the worker's propagated transport budget, joined HTTP readers,
retry after a timed-out read is released, and visible pending/unknown UI states.

The same regression selection runs against both revisions. The virtual-clock
harness executes the shipped refresh and API code and models a 30 s server that
ignores abort; separate Settings cases verify useful sections before a stalled
section and preservation of an already edited form. Snapshot tests verify stale
reuse, single flight, failure backoff, a propagated deadline and resource caps.
The table records one loopback run, with identical figures in the PR body.
Virtual-clock tests also prove a valid eight-second ordinary GET completes,
while a stalled bootstrap directory falls back at three seconds. Wall-clock
route assertions carry the `timing` marker and run explicitly with that marker
enabled. These are dependency-isolation timings, not a production cold-load measurement.
The project owner must still measure the large workspace after release against
the <2.5 s cold-start target, including static module loading and real rendering.


Production delivery and first paint (card #595)

#### Deploy note

Build the production image from the repository with
`docker build -f deploy/commonplace/Dockerfile .`. Its separate Node 22.14.0
stage copies `scripts/web-build/*.mjs` and the static sources, then runs
`node scripts/web-build/build.mjs`. There is no npm install, package lock or
third-party JavaScript build dependency. The script enables Node's built-in V8
module parser flag itself; callers need no extra flags. Only generated
`commonplace/web/static/build/` output is copied into the Python runtime before
its existing package installation. The runtime needs no Node. For a standalone
wheel, run the same Node command before building the wheel; the wheel explicitly
retains the generated assets despite source-control ignores. The offline wheel
regression uses the existing Python build backend, declared in the development
extra, directly: no installer or network runs inside the test.

A clean checkout with Python and no manifest keeps the existing unbundled
document and all 23 original stylesheet links. The production build is optional
for development. This deliberately preserves the Workbench source-link tests.
Build tests require those exact styles in the same order within one CSS file,
followed by optional route styles; there are no CSS imports. Both local variable
fonts are preloaded and keep `font-display: swap`.

The server reads the manifest into the entry document. Its CSP-hashed module
selector sees the URL fragment and imports one content-addressed entry containing
the shell, rail and that route's controller set. Other screens remain dynamic
imports. The compiler uses Node built-ins only. It preserves literals and comments,
wraps the known static graph in isolated factories, and rewrites relative module
bindings and dynamic imports to the shared registry. V8 validates native module
syntax, linking and exported names without executing application code. Named
imports, const/function/class exports and named re-exports are supported; mutable
exported bindings, static cycles and unsupported syntax stop the build. It does
not minify or rename source identifiers. The shared runtime evaluates each module
once across route changes. Deferred closures register missing factories without replacing
live exports, so identity/people maps and component state stay shared. Paths in
`import.meta.url` retain their source location. Generated JS and CSS use the
existing content-fingerprint immutable cache policy; the document and development
sources revalidate. The module watchdog, neutral loading document, capped module
reloads, 6000/minute public static bucket, ten-second initial identity read and
three retries remain the earlier contracts. HTTP 401 alone opens sign-in.

The foreground board wave requires only the cheap board directory and card list,
which run in parallel. Queue/rules/roster badges, project panel/chat decoration
and full board summaries wait until main data has had browser frames to paint.
The rail coalesces directory reads and reuses results for two seconds; event
polling starts two seconds after routing instead of immediately replaying the
initial backlog. Settings sections and required queue/rule content keep their
existing independent reads and deadlines. No ordinary GET timeout is shortened.

A 1000-card fixture exposed a remaining 1.3 MB card-list transfer. Board REST JSON
responses of at least 4096 bytes now support explicitly negotiated gzip at level
one, with deterministic gzip bytes, `Vary: Accept-Encoding` and `Cache-Control:
no-store`. This is lossless transfer encoding, not storage compression or a
content projection: all original words and fields survive decoding. A client
without gzip support, or with quality zero/invalid quality, gets plain JSON.
The measured fixture falls to about 34 KB on the wire.

The shell retains at most four mounted board trees and their display data for
the signed-in visit. A revisit reattaches the tree immediately, rebinds handlers
and revalidates data. An identical response skips markup generation; a changed
response preserves card nodes and only changes affected contents. Cached values
never authorize a write; mutation versions and server permissions still apply.
A 401/403/404 evicts the retained board view; sign-out clears it. A cold
`#/board` discovers the readable directory before choosing the saved or first
board, matching in-session navigation and retaining the empty-workspace start
screen when no board exists.

Reproduction: run `node scripts/web-build/build.mjs`, then
`python -m tests.cold_bundle_browser --output /tmp/cold-small.json --samples 3
--check` and the same command with `--cards 1000` for the large fixture. Use
Playwright 1.65.2 and `PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright`. Each run
uses disposable stores, a fresh Chromium context per sample, 1280x800, 4x CPU,
100 ms latency and 1.25 MB/s. The key milestone follows two animation frames;
request counts include issued requests through that milestone. The six keys
are Home composer, bot readiness, Memory search interface, card details, board
counters and Settings. Memory's worker is deliberately unavailable in this
frontend fixture. The large store has 1000 cards and 20000 historical events.
The project owner must still remeasure the live workspace after release.

Measured before/after medians on development base `60a7de90` and this change:

| Route | Small requests | Small seconds | Large requests | Large seconds |
| --- | ---: | ---: | ---: | ---: |
| Home | 74 → 14 | 1.74 → 1.60 | 74 → 14 | 1.74 → 1.58 |
| Bot | 74 → 18 | 1.84 → 1.69 | 74 → 18 | 1.84 → 1.71 |
| Memory | 78 → 13 | 1.82 → 1.60 | 78 → 13 | 1.82 → 1.58 |
| Card panel | 79 → 15 | 1.86 → 1.61 | 79 → 15 | 1.86 → 1.62 |
| Board | 75 → 13 | 1.75 → 1.53 | 80 → 13 | 3.83 → 2.28 |
| Settings | 79 → 23 | 1.88 → 1.65 | 79 → 23 | 1.86 → 1.65 |

The small fixture board revisit median is 208 → 30 ms. The original run replaced the tree three times; this change renders it once and keeps the same card node across the revisit.

The large fixture board revisit median is 2786 → 728 ms. The original run replaced the tree three times; this change renders it once and keeps the same card node across the revisit.

Three unchanged browser-free tests fail on the development source: five API reads before the board paint (budget two), two renders of an unchanged 1000-card board (budget one), and cold `#/board` resolving to `#/start`. All three pass on this change. The Chromium request budget also fails on development (74–80 requests, threshold below 40) and passes here. Production bundle 429, 503 and network failures retain the neutral retry message and recover through manual Retry in all three real-browser cases.
