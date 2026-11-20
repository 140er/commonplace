### Compute Activity

## Provenance and privacy boundary

Activity is a subpage of the existing Compute screen. Collection is opt-in on the
source computer. Its read-only SQLite connection uses URI `mode=ro` and
`query_only=ON`, with a bounded page cache, no memory mapping and short streamed
queries. It never copies, migrates, indexes or modifies the source database.
Sensitive JSON parsing and trace-file streaming happen on that computer.

The existing authenticated machine-report path carries only a versioned,
strictly validated aggregate snapshot. There is no database proxy, event export,
provider transcript export or remote source-file access. The outbound schema
permits finite numbers, bounded timestamps, fixed categorical identifiers and
hashed thread/project identities. It rejects unknown fields and arbitrary
labels without reflecting their values. Prompt-derived titles, prompts, file
contents, paths, command arguments, exception messages and tool results never
enter a snapshot. Tool categories are calculated locally. Unknown tools and
models have fixed `other` identifiers. The browser derives its copy from fixed
labels; it receives no source text.

An enrolled machine needs explicit Activity consent from the person who enrolled
it. Reports and reads recheck the active workspace membership, machine grant,
machine connection and consent. Account and owned-workspace exports apply
the same enrolling-owner filter to Activity rows. Account purge removes that
person's rows even in retained shared workspaces; workspace purge removes all
remaining Activity rows for that workspace. Another owner of the workspace cannot read that
person's host activity. Anonymous callers, members, worker connections and
external agent tokens cannot read or grant Activity access. Revocation, disabled
machines, expired connections, a new connection or grant changes invalidate access.
Enumeration, consent, recap and trace all revalidate the current connection's
machine kind, registry binding, workspace and enrolling owner. Reports grant no
execution, capacity, heartbeat or readiness authority.

The collector has no default destination or credential discovery. A caller must
explicitly supply an existing authorized machine transport; no provider/session
credential is read or reused. Local inspection uses the same protected app with
a separate temporary data directory and a synthetic account. Real local
aggregates are labelled local. The existing review instance is never altered.

## Counting and limits

The selected interval is half-open. Actual starts, rather than requests, define
run time. Intervals are clipped, unioned per executing agent, and split across
local days in Europe/Amsterdam, including daylight saving changes. A native
subagent with its own projected child run is counted once. Requested work without
a start stays separate. Run outcomes are not task completion. Task and pull
request states need an attributable persisted record; unavailable values remain
visible with an exact reason.

Linked PR counts describe the latest dated retained state for each inspected PR,
not open, close or merge events during the selected window. Source/as-of dates
sit beside the counts. Window event counts remain unavailable because retained
state snapshots do not establish event dates. Overflow in link collection makes
state counts unavailable too; inspected unique links are not a complete denominator.

Time shares use exclusive precedence: recorded user-input wait, compaction,
tool/command execution, then unattributed run time. Summed call time remains a
separate measure because overlapping calls can exceed wall time. Runtime minus
tools is never called thinking. No-run gaps stop at the last observed run; they
are not attributed to a person. Cancelled asks are separate from answered asks.
Retries require an explicit attempt reason. Failed runtime is not described as
entirely wasted. Percentiles use the nearest-rank definition and name the sample
count. Missing timing or usage is unknown, never a zero.

Only explicitly attributable per-turn token deltas are additive. Context totals
are snapshots, not deltas; cached input overlaps input. Scope, subagent inclusion
and incomplete usage coverage are retained. Recorded high-context snapshots do
not prove every pre-compaction peak. Recorded edit lines are distinct from net
repository changes. Trace phases are time-bucket aggregates, never raw tool
events. Rotated server spans have a separate retained coverage window and are
not added to agent hours without attribution.

The host calls `collect_and_report` with its source path, selected interval and
an explicitly supplied machine transport. Its only outbound call is
`POST /api/runner/activity`; the receiver checks consent again. No background
source discovery, credential discovery or execution grant is installed.

Collection filters selected identities and eligible time windows before applying
row budgets, including input requests, retries, provider turns and handoffs.
Intervals and percentile samples cannot exceed their source's row budget. Each
source reports rows inspected, its budget and an overflow signal; the budget is
not a total count. PR links have a global metadata budget and a 512-link limit per
thread. A one-row overflow probe marks omissions partial without retaining them.
Older snapshots without this per-source coverage say that it is unavailable.

Collection has explicit row, identity and bucket limits. A capped scan reports
partial coverage. Recap metadata and detailed scan latency, peak resident memory,
source size, source freshness, timed/untimed runs, missing data and retention limits are measured
independently. Polling reads stored aggregates only, stops when the page is hidden
or paused, and offers manual refresh. A missing collector, stale snapshot or
failed request is distinct from a real zero-activity interval.

Time sinks ranks the leading retained threads across all models for the selected
source/window, independently of Trace pagination and model filters. Each tab
reloads its own scope. Phone rows preserve token, time, percentile and call labels.
An empty filtered Trace list means no retained traces match; collection/retention
caps can omit observed work. A missing detail explains that Refresh only rereads
the stored snapshot and cannot restore omitted detail.
