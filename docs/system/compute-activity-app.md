### Compute Activity app

## Privacy and current authority

Activity is nested inside Compute. The four global destinations stay unchanged.
The app reads stored aggregate snapshots; Refresh never scans a source database.
Source-local collection, interval accounting and the wire schema live in
`activity_collector.py`, `activity_math.py` and `activity_schema.py`.

The report boundary permits bounded numbers, fixed categories, timestamps and
opaque thread/project identities. It does not accept prompts, titles, paths,
commands, file contents, source events or arbitrary labels. Unknown fields and
malformed values are rejected without reflection. The collector has no default
destination or credential discovery; its caller supplies the authorized transport.

An enrolled machine needs explicit Activity consent from its enrolling person.
Enumeration, consent, recap and stored detail reads revalidate the current owner
session, workspace membership, machine connection, registry binding and grant.
Reports require that machine identity and the same consent. Changed grants,
expired or revoked connections, disabled machines and other enrolling owners
cannot reveal the retained data. Anonymous callers, members, worker connections
and external agent tokens cannot grant or read this person's activity.

Account and owned-workspace exports include only the requesting enrolling owner's
Activity rows. Account purge removes that person's rows from retained shared
workspaces; workspace purge removes all remaining Activity rows for that workspace.
No Activity read, consent or report grants execution, heartbeat, readiness or capacity.

## Stored summaries and scope

Recap shows measured values or unavailable reasons, daily activity, model totals
and source coverage. Retained PR state counts show their as-of provenance beside
the counts; they are not merge/open/close events in the selected window. Missing
timing and missing usage remain unknown, and partial or capped scans retain their
coverage. Reported tools and thinking are not interchangeable measures.

Time sinks ranks retained leaders for the selected source and window across all
models, independently of the retained thread list's pages and model filter. The
Trace tab initially contains thread summaries. Live filters retained active or
queued summaries. These states describe observed work, not host liveness.
Thread identifiers are plain text in this app slice. There is no detail action or
phase-bucket view; the protected aggregate detail API retains the same owner fence.

Each tab reloads its own query scope. A shrinking filtered total resets the offset
and range together. An empty list means no retained summary matches this filter;
collection and retention limits can omit other work. Refresh only rereads stored
snapshots and cannot restore omitted source detail.

## Provenance, refresh and accessibility

The visible collection timestamp and window belong to the aggregate response being
displayed. Collection age, source-update age and partial coverage remain visible.
A missing collector, missing window, failed request or stale snapshot is distinct
from a real zero-activity interval. Failed refreshes disclose retained stale data.

Polling stops when the page is hidden, paused or departed; requests do not overlap.
The phone layout retains the Activity heading, compact named controls, visible
token/time/percentile labels and semantic table-column associations. Keyboard
pagination restores visible focus and announces the new range. Source text never
enters rendered labels, and charts use the existing content security policy.

The app tests use synthetic data, the normal protected gateway and normal product
sign-in. Browser dependencies are required; unavailable browser tooling fails the
check rather than skipping it. Collector, app and lifecycle checks run as affected
subsets with the required guards.
