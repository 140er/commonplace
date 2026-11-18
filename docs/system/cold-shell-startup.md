### Cold shell data does not wait for optional projections

The shell previously joined boards, the owner queue, the rules count and the bot
roster before dispatching the selected screen. Settings then joined six more
reads. The bot roster used the full board projection just to obtain names. That
projection expires claims and computes runner capacity and per-card waiting
reasons for each project, inside a write transaction. Settings wing discovery
can ask the memory worker; plan storage usage walks every regular workspace
file under a shared meter lock. These joins made unrelated screens pay the
slowest dependency. Most browser reads had no deadline, including bot directory
and thread reads, so a stalled dependency could keep a cold page waiting for
an entire proxy timeout.

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
| `/api/me` | Session, workspace, people and admin capabilities in the control database | Authentication first; 3 s complete-read bound |
| `/api/boards?view=shell` | Current active, policy-readable board metadata; asynchronous grouped board counts | Main directory; 3 s browser bound; counts refresh 3 s |
| `/api/boards` | Claim expiry, counts, runner capacity and waiting projections per project/card | Deferred until project main data has painted; 3 s browser bound |
| `/api/needs-me` | Board task/approval, coordinator, orchestration and model-role projections | Required only for queue content; elsewhere a background badge; 3 s |
| `/api/rules/count`, `/api/rules` | Rule-store identity check and local SQLite count or rule rows | Count is a background badge; full rows only on Rules; 3 s |
| `/api/bots` | Authorized bot store, defaults, asks, readiness per project | Names from cheap directory; no full board projection or palace read; 3 s |
| `/api/coworkers/bots`, `/api/coworkers/conversations`, `/api/coworkers` | Workspace-local directory and conversation stores | Rail, home and thread enrichment, independent of main content; 3 s |
| `/api/bots/shared`, bot threads/messages | Bot lifecycle, access and bounded message pages | Shared management list is optional; selected thread follows personal roster; 3 s default |
| `/api/boards/<slug>/cards`, `/api/cards/<n>` | Board/card projection in local stores | Required main data; card read already starts beside board refresh; 3 s default |
| `/api/connections` | Current connection grants; optional memory wing index/worker fallback | A cold request joins one shared wing lookup for at most 1 s; stale wing hints return immediately |
| `/api/plan` | Account plan and transcription counters; workspace file-size walk | Counters return immediately; cached storage refresh 3 s, cooperative walk deadline |
| `/api/webhooks`, `/api/phone-push`, `/api/chat-settings`, `/api/settings/orchestration` | Local settings and usage stores | Independent Settings sections; 3 s each |
| `/api/voice` | Local voice configuration | Already started without awaiting it; now 3 s default |
| `/api/memory/rooms` | Policy-filtered note destinations; asynchronous worker prewarm | Memory paints before this optional request; no worker readiness wait |
| `/api/events` | Local event rows/cursor, filtered for members | Polling begins after routing; 3 s default; Today activity is independent |
| `/api/workspaces` | Memberships and workspace-switch capability | Requested when the switcher opens, not at boot; 3 s default |
| `/api/topology`, `/api/runners`, node detail, project/checkpoint reads | Local topology/runner/orchestration stores | Compute/project screens only; 3 s startup reads |
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
request even if the transport ignores abort. Read defaults are 3 s; explicit
operation budgets and write/stream timeouts retain their existing contracts.
The former unlimited browser reads are the reproduced mechanism for a long
first-load stall; no production trace is available to assign the observed
60 s outlier to one exact request.

Display snapshots retain at most 256 entries, coalesce each key and allow at most
two simultaneous refreshes per gateway. Their cooperative deadline is 3 s, with
30 s reuse/backoff (60 s for storage), except that a failed wing lookup has no backoff once it finishes. A dependency that ignores its deadline
cannot accumulate readers; its result is discarded after expiry. Counts and
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
| `/api/boards?view=shell` / full board projection | 30.0032 s | 0.0064 s | Expiry and runner/per-card projection |
| `/api/bots` / project-name projection | 30.1817 s | 0.0225 s | Full board listing used only for names |
| `/api/connections` / memory wing discovery | 1.0037 s | <1.1 s | Settings no longer waits for the wing lookup to render other sections |
| `/api/plan` / workspace storage walk | 30.0027 s | 0.0016 s | File scan and storage-meter lock |
| Rail waiting on roster / control database for identity | Blocked for entire 0.5 s test bound | <0.001 s | Opposite lock acquisition order |
| Shell main data / queue, rule count or roster | 30 s virtual clock | 0 ms virtual clock | Badge join before route dispatch |

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
These are dependency-isolation timings, not a production cold-load measurement.
The project owner must still measure the large workspace after release against
the <2.5 s cold-start target, including static module loading and real rendering.
