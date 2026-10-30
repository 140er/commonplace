# Fact-first search

## Goal

Return matching, readable facts before verbatim drawers. MCP and hub search add
a `facts` list; CLI search prints Facts above drawers. Facts carry their original
thread/range or drawer pointer, origin, approval, lifecycle and successor ID.
The existing drawer search still runs with the same limits, filters and order.

## Scoring

Reuse the query sanitizer and lexical tokenizer, treating predicate underscores
as word separators. Lexical relevance is the fraction of distinct query words
present in the subject, predicate or object. Only whole words match.

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
index supplies candidates; one indexed SELECT also reads their observed sources.
The gateway performs one fact lookup across allowed wings, independently of its
existing drawer fan-out. Scoped KG reads use a cheap wing list instead of full
KG statistics. Missing graphs return no facts and do not create a palace file.

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
- Run lint, format and the requested broad suite without changing budget tests.
