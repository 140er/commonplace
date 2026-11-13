### Bounded memory for overview reads

Status, wing, room and taxonomy overviews retain backend facet aggregates when
available, including pgvector, qdrant and exact/compact when their fast path is
unavailable. Facet acquisitions have explicit distinct-key limits; bookkeeping
subtraction uses a fixed number of room facets per wing: registry, sentinel
union and their overlap. Only the bookkeeping facets' returned rooms are expanded;
ordinary rooms do not cause additional facet calls. Missing rooms are reconciled
from three global wing-keyed bookkeeping facets over those same predicates.
Global room facets supply the wingless room remainder; two additional scalar
facets (registry ingest_mode and non-registry sentinel is_sentinel) reconcile
missing wing keys without double subtraction or string/numeric flag confusion.
The exact/compact SQL filter pushes scalar $ne with an explicit NULL arm so
missing ingest_mode keys remain eligible for the disjoint sentinel count.
Facet taxonomy retains its existing explicitly keyed-wing enumeration; wingless
bookkeeping cannot subtract from a literal wing named "unknown". Boolean and legacy string
sentinel aliases share one union predicate, preventing duplicate subtraction.
Taxonomy per-wing facets use at most eight concurrent calls, releasing each map
after reduction. With W explicitly keyed wings, the unscoped facet-call bounds
are status <= 9 + 3W, list_wings <= 8 + 3W, list_rooms <= 9 + 3W, taxonomy
<= 8 + 4W, plus at most 2 calls when wingless rows exist. This conservative
baseline includes the three global room facets even without wingless rows.
These count facet calls, not backend requests or elapsed time. From backend
code, sqlite_exact executes two SQL statements per facet (collection lookup
and aggregate); pgvector and qdrant perform one existence probe per facet in
addition to its aggregate request. Live remote costs have not been measured.
Up to eight completed in-flight maps can transiently retain eight times 8 MiB,
plus the separately bounded tally; the 8 MiB map limit is not a combined
transient limit. Qdrant membership unions use homogeneous string/integer
MatchAny groups, single boolean matches and numeric equality ranges, keeping
bookkeeping filters server-side without mixed-type MatchAny bodies.

Only `UnsupportedCapabilityError` in bookkeeping acquisition preserves ordinary
facet results with `partial: true` and
`bookkeeping_error: bookkeeping_unavailable`, including unsubtracted bookkeeping.
Any other `BackendError`, including transient service or closed-backend errors,
returns `overview_unavailable` in the normal refusal envelope with no drawer total
and no aggregates. A transient error cannot publish inflated counts as partial.
Live Qdrant/pgvector facet support remains unverified; local wrapper tests use
isolated test clients and do not establish live payload-index/filter parity.

Two public count corrections are deliberate: facet overviews subtract legacy
string sentinels (`"true"` and `"1"`) as well as boolean flags, and explicit
facet limits replace the previous default-1000 silent truncation. Counts are
complete within the configured aggregate bounds; over-cap acquisitions refuse
explicitly rather than publishing truncated totals.

Missing-room registry/sentinel rows and rows without either locus key are
also excluded from facet counts. Pre-existing sqlite_exact fast-path handling
of the string "yes" and scoped first-page facet probe fan-out are unchanged.

Non-facet raw fallback uses a presence-preserving projection of wing, room,
is_sentinel and ingest_mode. Chroma reads these fields in one recovery-checked
SQLite snapshot, including a drawer without any projected key. The reader also
supports a deduplicated indexed wing scope. Room alias discovery and room counts
reuse one bounded aggregate pass, avoiding a second projected scan. Unrelated
metadata and documents never enter this acquisition. General keyed metadata
iterators keep their previous semantics.

Projected batches contain at most 500 rows and 1 MiB of canonical UTF-8 JSON
(compact separators, ensure_ascii=False). Complete rows split a batch before its
byte limit. SQL checks cell lengths before decoding; a projected cell over
64 KiB is refused. Aggregates have at most 16,384 distinct wing/room pairs and
8 MiB of deep sys.getsizeof container/key/value accounting, counting shared
objects once. Conservative preflight may refuse earlier; one bounded dictionary
insertion's allocation overhead can precede the final accounting check. These
are allocation-structure bounds, not a whole-process RSS cap. The primary grouped
Chroma/exact/compact path also enforces these caps: over-cap palaces now receive
an explicit diagnostic instead of an unlimited public overview. Facet result
maps are checked under the same aggregate/key limits; they are backend
aggregates, not the byte-bounded raw metadata acquisition contract.

Only one bounded taxonomy cache is retained, keyed by palace, collection and
database fingerprint, including only `overview_too_large` refusals; transient
`overview_unavailable` errors are never cached. Writes, reconnects and a changed
or lost fingerprint invalidate successful and refused entries alike. The
existing freshness TTL applies only while both cached and current fingerprints
are absent. Raw overview metadata is never cached.

A lazy SQLite acquisition error closes the failed snapshot and discards its
entire partial tally. The same byte-bounded projected reader is reopened once
from the beginning with identical where. A second acquisition failure, including
persistent MetadataRecoveryPending, returns explicit `overview_unavailable`.
This read-only retry can recover a transient failure; it does not apply queued
Chroma writes or call an unbounded SDK reader. There is no invented Chroma byte
capability. A non-facet backend without a bounded projected reader is refused
without calling get; no production backend advertises a byte-bounded page adapter.
Unsupported non-facet raw acquisition returns `overview_unavailable`; oversized
cells/aggregates return `overview_too_large`. These refusals carry the normal
empty aggregate keys and `partial: true`; status keeps its protocol/backend
fields and uses a null total. No partial tally is published as complete.

This fixes a measured fallback defect. Production worker memory attribution
remains open. Other full metadata consumers, including delete-by-source and the
palace-graph fallback, and model/vector allocations remain separate candidates.
Storage format and verbatim drawer retrieval are unchanged.
