# Fact-first search

## Goal

Return matching, readable facts before verbatim drawers. MCP and hub search add
a `facts` list; CLI search prints Facts above drawers. Facts carry their original
thread/range or drawer pointer, origin, approval, lifecycle and successor ID.
The existing drawer search still runs with the same limits, filters and order.

## Scoring

Reuse the query sanitizer and lexical tokenizer, treating predicate underscores
as word separators. Ignore English question/predicate glue and configured locale
stop words. A filler-only query returns no facts; every returned fact must match
at least one remaining whole word.

If any query term occurs in at most half the facts, use those terms to seed the
lookup; otherwise keep all meaningful terms. This avoids scanning the corpus
for universal words when a selective term is available. All query terms still
contribute to final IDF relevance.

Use FTS5 BM25 to select at most 50 scoped seed facts in SQL. Expand only their
subject/predicate keys to retain visible history and current successors. Final
lexical relevance is the fraction of query IDF weight matched:

`idf(term) = ln(1 + (N - df(term) + 0.5) / (df(term) + 0.5))`

`lexical_relevance = sum(idf(matched_terms)) / sum(idf(query_terms))`

`score = lexical_relevance * status_weight * decay`

All ranking constants live in `mempalace/searcher/facts.py`:

| Status | Weight |
| --- | ---: |
| Current, approved | 1.0 |
| Current, proposal | 0.85 |
| Current, unknown | 0.75 |
| Ended, superseded or invalidated | 0.25 |

Decay is `max(0.5, 2 ** (-age_days / 180))`. Age starts at the latest
`fact_sources.added_at`, falling back to `valid_from`, then creation time.
Approved facts have decay 1.0. A future observation has age zero. Date-only
validity bounds retain KG semantics. Historical facts remain visible, but every
matching current fact precedes ended facts with the same subject and predicate.
An explicit as-of read limits candidates to the interval valid then.

## Scope and cost

Use the existing KG wing/sensitivity filters and gateway caller policy, including
permission to read KG facts. Never widen a scoped search. A local SQLite FTS
index supplies candidates; one indexed query also reads their term frequencies
and observed sources after the seed cap. FTS maps to stable triple IDs through
an INTEGER PRIMARY KEY mapping, so VACUUM cannot misalign facts and search hits.
The gateway performs one fact lookup across allowed wings, independently of its
existing drawer fan-out and uses the existing KG policy for scope enumeration.
The separate KG import/provenance work owns cheap wing listing. Callers that
discard facts explicitly disable their lookup. Missing graphs return no facts
and do not create a palace file. Any fact-side exception returns an empty facts
list and a generic `facts_error` marker; drawer search remains independent.

## Non-goals

No extraction, embeddings, AAAK, external services, source replication or import
repair. No fact-driven truncation or reordering of drawers; no source-drawer
boost in this step. Facts remain index entries pointing to the verbatim source.

## Eval gates

- Fact-first `fact_recall_bench.run(retriever=...)`: every positive bar 1.0,
  stale rate 0, private leaks 0, including a successor and matching stale value.
- Compare plain drawer search and fact-first search on the same corpus; no bar
  regresses. Report the exact numbers and the adapter's source mapping.
- Fact provenance benchmark passes unchanged.
- Existing searcher tests pass unchanged; a cheap local drawer benchmark keeps
  its recall. Tests cover weights, decay, boundaries, whole words, provenance,
  one indexed lookup, unchanged drawers and real gateway privacy/scope.
- Deterministic gates cover filler/noise rejection, rare-term ranking, the SQL
  seed cap with key history, source ordering, generic error isolation, and VACUUM.
- The seeded latency probe reports before/after added lookup time at 5k, 20k and
  50k facts, targeting at most 50 ms at 50k for common and selective queries.
- Run lint, format and the requested broad suite without changing budget tests.
