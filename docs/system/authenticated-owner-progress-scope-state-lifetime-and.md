### Authenticated owner progress scope, State lifetime and quota causality

- The owner-only `/api/search-index` route always carries its authenticated
  principal. Counts include every currently readable thread in the workspace,
  including another member's readable threads, using the existing indexed and
  pending predicates. Owner status grants no extra thread read authority.
- Each poll makes one current authorization decision per thread over metadata
  pages of at most 500 rows; the allowed descriptor map lives only for that response. Concrete metadata
  descriptors are revalidated in the aggregation snapshot; changed or newly
  encountered descriptors are excluded. Message and chunk content is not read.
- Successful sweep writes accrue units by stored thread id for the process
  State lifetime. Every poll reauthorizes those ids; revocation hides accumulated
  units, and a fresh State starts at zero without recovering historical totals.
  Hidden-only activity and hidden-only quota refusals do not set the public state.
- State precedence is configured `off`, then `deferred_storage` for readable
  pending work actually refused by quota, then readable active `running`, then
  `idle`. Active means inside a thread's write loop; queued or paused work
  alone does not set `running`. The global workspace storage meter is unchanged: hidden storage may
  cause genuinely blocked readable work to report `deferred_storage`. This
  causality is explicitly accepted; there is no unconditional state-equality
  claim when readable quota eligibility differs.
- Polling has one one-second proof budget, including timed snapshot/attribution
  lock acquisition and SQLite progress interruption. Owner progress uses a
  separate strict Recall probe: store and private-reader faults propagate to the
  proof boundary, while ordinary Recall consumers retain deny-on-fault behavior.
  Incomplete enumeration, authority faults, missing strict reader context or expired proof returns the fixed HTTP 503
  `{"error":"Progress unavailable"}`, with no partial totals, raw errors or
  global fallback. Successful and unavailable responses carry `no-store`.
  The proof budget is a failure boundary, not a latency guarantee.
- Local operator CLI status/run explicitly use `principal=None`, retain their
  unscoped count keys and existing enrolment behavior, and do not supply the
  authority for the HTTP projection. Other provenance and read residuals below
  are unchanged.
