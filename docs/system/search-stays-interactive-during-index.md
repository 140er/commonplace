### Search stays interactive while the index backfill runs

After a deploy the search-index backfill catches up on conversation threads that are not
indexed yet. It used to take up to 20 threads and 2000 message units per sweep with a 1 s
rest, so the memory worker stayed saturated and a search took many seconds. The `threads=20`
in its log line is the number of conversation threads per sweep, not a thread pool.

The backfill now takes small steps and gives way to people. Defaults: 3 threads and 192
units per sweep, a 3 s rest between sweeps, and a rest after each worker write of twice
that write's run time. While a person's call is running, and for 0.25 s after, the next
backfill write waits (at most 5 s). The sweep thread and the worker's index-write request
thread run at niceness 10, and a refused write backs the sweep off (doubling to 60 s). A
palace read that hits a transient SQLite `OperationalError` is replayed up to three times.

Settings (environment): `COMMONPLACE_SEARCH_INDEX_THREADS`, `_UNITS`, `_PAUSE` (seconds),
`_REST_RATIO` (0 disables the rest), `_NICE` (0 disables) and the worker's
`MEMPALACE_MIRROR_NICE`. `COMMONPLACE_SEARCH_INDEX=off` still stops the backfill. Nothing about
what is indexed or the stored words changes; the backfill is still incremental and idempotent.

Known limit: the niceness of the embedding runtime's own thread pool is fixed when that pool is
created, so pacing, not priority, is the main lever.
