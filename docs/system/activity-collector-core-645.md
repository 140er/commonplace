### Activity collector core and untimed active work (#645)

The collector is a separate prerequisite for Activity API and UI wiring. Its three
modules depend only on each other and the standard library. It reads SQLite in
read-only, query-only mode and emits a validated aggregate with fixed categorical
labels and opaque identities. It neither discovers credentials nor sends data on
its own. The optional report callback requires an explicitly authorized caller.

Operational evidence stays private. Public validation reports use repository-relative
test names and aggregate results; personal source locations and internal runner
commands are not public evidence references.

Preparing, starting, running and waiting requests can lack `startedAt`. When their
valid request timestamp precedes the selected window's end, they remain eligible
before the metadata scan limit, even if requested before the window starts. Python
retention keeps the same records. Active statuses and counts remain observable;
request time anchors visibility only and never supplies an execution interval.
`runs_untimed` exposes missing duration coverage. If no execution duration is known,
`agent_hours` is unavailable with reason `no_timing`. Existing aggregate duration
fields describe measured intervals, with coverage retained alongside them.

The collector retains bounded scans, partial-scan disclosure, strict timestamp and
aggregate validation, exclusive interval accounting, model-switch attribution,
token-scope rules, read-only behavior and privacy canaries. Queued work stays
distinct from started work; future requests and old completed requests remain
excluded according to the existing rules. No owner, API, browser or runner wiring
is part of this prerequisite.

Focused tests use `tests/activity_core_fixtures.py`. Collector tests and the first
six collector-only mixed regressions retain their original assertion identities,
as do the collector/math round-two tests. The dedicated unified-diff hunk-counter
regression stays with the later provenance change; the core already carries its
corrected implementation.
