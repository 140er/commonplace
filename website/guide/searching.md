# Searching Memories

MemPalace combines semantic vector candidates with BM25 lexical candidates by default on bounded lexical backends (`union`): Chroma, sqlite_exact and compact. Other backends default to vector candidates. This also finds exact paths, identifiers, and error strings that embeddings miss. When you search, you get **verbatim text** — the exact words, never summaries.

## CLI Search

```bash
# Search everything
mempalace search "why did we switch to GraphQL"

# Filter by wing (project)
mempalace search "database decision" --wing myapp

# Filter by room (topic)
mempalace search "auth decisions" --room auth-migration

# Filter by both
mempalace search "pricing" --wing driftwood --room costs

# More results
mempalace search "deploy process" --results 10

# Select vector candidates only
mempalace search "deploy process" --candidate-strategy vector
```

## How Search Works

1. Your query is embedded using the vector store's default model (`all-MiniLM-L6-v2` with the default ChromaDB backend).
2. On Chroma, sqlite_exact and compact, vector matches and bounded backend BM25 lexical matches form the candidate pool. Other backends use vector candidates by default. `candidate_strategy="vector"` keeps vector candidates only.
3. Optional wing/room filters narrow both candidate sources.
4. Hybrid ranking orders the merged candidates. Results include source metadata and similarity scores; a lexical-only hit can have `similarity: null` and `distance: null` when no vector distance was measured.

Union drawer retrieval selects up to eight index-normalized terms, prioritizing literals and proper names outside sentence starts. A term fetch applies the complete scope inside FTS and stops after the scoped cutoff plus one row. The cutoff is 5% of the scope, bounded between 1,000 and 2,000 records. There are at most sixteen term fetches, no separate frequency probes, and no shared query-statistics cache. The complete accepted-term pool is ranked using only in-scope text and statistics, then the top results are hydrated. Closets use the same scope-local path. Public backend `lexical_search` retains its signature.

Closets retain full-query scope-local BM25 without the drawer term selection or DF cutoff. They keep the query caps and quoted terms; reaching 5,000 matches for any term disables all closet boosts for that search.

Writable Chroma opens maintain a partial covering index over wing, room and source metadata. It indexes no document text. Read-only palaces use the same bounded retrieval without creating the optional index.

Queries are capped at 256 tokens and terms at 64 characters. Queries longer than twelve tokens after stop-word removal use literals and names only; without either they report `lexical_skipped: "long_query_no_literal"`. Stop-word-only selections report `no_terms_after_stopwords`. A long question with an incidental date can miss a lexical-only answer containing only ordinary topic words. Unsupported backend/filter shapes use vector candidates and report `lexical_unbounded_for_filter`, including explicit union requests. Corpus-dependent selection details and fallback causes are never returned. See [RFC 009](../../docs/rfcs/009-union-where-bounded.md).

Commonplace searches its readable wing union in one worker call using the public `wings` list. The gateway intersects requested wings with the connection's readable wings, including the separate private-read requirement. This produces one candidate pool and one ranking. `wing` and `wings` together mean their intersection.

The returned hybrid `score` is normalized within the candidate pool. Handoff recall can still merge thread scopes using the worker's sanitized query and explicit locale. Media/code task search always uses shared-space vector ranking and does not accept `wings`.

### Why Scoping Matters

Wing/room filtering is useful when a single palace contains many unrelated projects or people. Narrowing the search to a specific wing (or wing + room) means the vector store only scores candidates inside that scope, which keeps retrieval predictable as the palace grows.

This is a metadata-filter feature of the vector store, not a novel retrieval mechanism. Treat it as an operational convenience: clear scoping rules that a human or an agent can apply predictably.

## Programmatic Search

Use the Python API for integration:

```python
from mempalace.searcher import search_memories

results = search_memories(
    query="auth decisions",
    palace_path="~/.mempalace/palace",
    wing="myapp",
    room="auth",
    n_results=5,
)

for hit in results["results"]:
    print(f"[{hit['similarity']}] {hit['wing']}/{hit['room']}")
    print(f"  {hit['text'][:200]}")
```

The `search_memories()` function returns a dict:

```python
{
    "query": "auth decisions",
    "filters": {"wing": "myapp", "room": "auth"},
    "results": [
        {
            "text": "We decided to migrate auth to Clerk because...",
            "wing": "myapp",
            "room": "auth-migration",
            "source_file": "session_2026-01-15.md",
            "similarity": 0.892,
        },
        # ...
    ],
}
```

## MCP Search

When connected via MCP, your AI chooses a retrieval tool for your question. These are alternative starting points; stop as soon as the returned evidence answers the question.

For a known entity's relationships or a fact at a requested date, use `mempalace_kg_query`. Set `as_of` only when the question specifies a date. For current relationships without a date, use the returned `active_facts`; the query also returns historical and future facts.

For example, to look up Alice's relationships on March 1, 2026:

```json
{
  "name": "mempalace_kg_query",
  "arguments": {"entity": "Alice", "as_of": "2026-03-01"}
}
```

For narrative recall or the original source words, use `mempalace_search` with a short query and a small result limit. Add a wing or room only when its scope is known. For *"Why did we change authentication?"* in the known `myapp` wing:

```json
{
  "name": "mempalace_search",
  "arguments": {"query": "why we changed authentication", "wing": "myapp", "limit": 3}
}
```

For recent continuity, read a few diary rows directly using the name of the agent that wrote them:

```json
{
  "name": "mempalace_diary_read",
  "arguments": {"agent_name": "claude", "last_n": 3}
}
```

Omitting the diary's `wing` reads across that agent's wings. `last_n` bounds stored rows, so a long diary entry may span multiple results.

Expand retrieval only if the first result leaves the question unanswered. Search returns stored text; quote relevant passages verbatim, never summarize or paraphrase them. See the [Recall Protocol](https://github.com/MemPalace/mempalace/blob/develop/integrations/shared/recall-protocol.md#retrieve-only-the-context-you-need) for the shared workflow.

See [MCP Integration](/guide/mcp-integration) for setup.

## Wake-Up Context

Instead of searching, you can load a compact context of your world:

```bash
# Load identity + top memories (~600-900 tokens in typical use)
mempalace wake-up

# Project-specific context
mempalace wake-up --wing driftwood
```

This loads Layer 0 (identity) and Layer 1 (essential story) as bounded startup context before the first retrieval call.

See [Memory Stack](/concepts/memory-stack) for details on the 4-layer architecture.
