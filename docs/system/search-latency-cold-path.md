### Search latency: why a spaced search was cold

The memory search tool re-ran the HNSW capacity probe (an embeddings `COUNT(*)`
plus a full unpickle of the segment metadata, both proportional to the palace)
for every search that came more than 10 seconds after the previous one. The 10
second ceiling on a cached verdict was meant as a backstop for filesystems with
coarse timestamps, but it expired a good verdict on every filesystem, so a
person searching every few seconds or minutes paid the probe each time while a
direct call to the searcher (which skips the probe) looked fast. The worker was
not recycled between requests; the same worker process answered every query.

The verdict is now reused while the probed files keep their signature wherever a
timestamp proves sub-10 ms resolution. Coarse-timestamp filesystems keep the 10
second ceiling. One verdict still expires with time: a standing gap between the
database and an aging metadata pickle turns "diverged" once the pickle is old
enough, so it is served only until then.

A gateway-launched worker also warms itself up in a background thread right
after it binds: it runs the ordinary search path once with a sentinel query,
between two files-only capacity probes, so the collection open (which writes to
the database and so invalidated the verdict recorded before it) has settled. The
state (`off`, `running`, `done`, `failed`) is in the worker's status payload, and
a search that arrives while it runs carries `warming_up: true`, which
`GET /api/memory/search` passes on. The field is additive; results are never
changed. `COMMONPLACE_WORKER_WARMUP=0` on the gateway turns the warm-up off. The
operator's own warm-up variables still never reach a worker, so the decision in
`eager-warmup-scope` stands for them.

Measured on a synthetic 120,000-drawer palace through a real worker process
(local harness figures, not production): searches spaced 11 seconds apart took
0.11 to 0.14 s before and 0.034 to 0.044 s after; the first search took 2.0 to
2.6 s before and 0.6 s after, flagged as warming up. The cost removed is linear
in the palace (about 0.1 s per 120,000 drawers). Whether it explains the full
12 to 19 seconds seen on the live palace is not established here: that needs a
measurement on the live host, with the page cache and other load in the picture.
