# RFC 008: Compact workspace store

Status: proposed, 30 Oct 2026. Based on a storage and encryption study (its option B). Revised after review.

## Why

On 250 MB of real agent chats (130 Claude Code and Codex transcripts, 54,494 messages):

- Today's stores take 1,733 MB (6.9× the chats): the thread store 642 MB, the Chroma mirror 1,091 MB.
- A prototype of this design took 146 MB (0.59×), byte-for-byte lossless. That prototype kept no second copy of mirror text: its vectors pointed at thread chunks (the content references deferred under Open points). The first cut stores mirror drawer text compressed, plus metadata and its own word index; the B1b PR measures it (estimate 180–230 MB).
- Writing one chat into the Chroma mirror takes ~76 s (192 `add_drawer` calls, mean 398 ms). The prototype thread store writes a chat in 0.1 s, excluding embedding.
- 43% of raw chat bytes and 53% of message text are base64 (screenshots, encrypted reasoning, signatures). 51% of the drawers embedded today are mostly base64. 86% of text is tool output.

Decisions (30 Oct 2026): keep all agent chats; embed conversation and tool calls only after the recall gate; operator-blind token slots. A heavy multi-agent user writes ~9 GB of transcripts a month: 65 GB a month in today's format.

## Goals

- Store every verbatim byte once per store, compressed, and recover it exactly.
- Index text that is useful for search: base64 runs become placeholders in index and embedding text, never in storage.
- Let a record be keyword-only (no vector), so tool output stays findable without embedding it.
- One key per workspace later (B2): every file of a workspace encrypted with SQLCipher.
- No breaking change to MCP tool shapes (optional arguments only), the ThreadStore API, or search results apart from intended ranking changes.

## Non-goals

- Changing the embedding model, chunk sizes or ranking weights (except where B1c's gate requires it, stated there).
- Server-side search over sealed records.
- Moving the thread store into MemPalace. Each layer gets its own compact format.

## Phases

| Phase | What | Layer | Needs |
|---|---|---|---|
| A0 | Embed index text (base64 → placeholder) instead of raw text, all backends | MemPalace | PR #39 detector |
| B1a | Thread store v2 format | Commonplace | detector (or index version 0, reindex later) |
| B1b | `compact` palace backend | MemPalace | – |
| B1c | Mirror unit + policy: one drawer per message class, tool output keyword-only | both | B1a, B1b, PR #38, PR #39 gate |
| B1d | Flag, verified backfill, switch, rollback | both | B1a, B1b |
| B1e | Vector tier for 1M+ vectors per collection with recall@10 ≥ 0.98 | MemPalace | B1b |
| B2 | SQLCipher on every workspace file, keyslots, byte-copy backups | both | B1d |
| B3 | Operator-blind token slots | Commonplace | B2 |
| C | Capture all agent chats | Commonplace | B1c, B1d, B1e |

C waits for B1c and B1e. Before B1c the mirror embeds everything: about 4M vectors a month at the measured rate, ~7 CPU-hours of embedding a day. Even under the B1c policy a heavy user passes 1M vectors in about 7 weeks.

## A0: Placeholder embeddings (all backends)

- Core derives index text from a drawer's whole content before it is split or embedded: extend `stored_text.index_text` with the PR #39 detector. The embedder and every lexical index get index text; the stored document stays verbatim.
- Chroma accepts explicit embeddings, so this ships on Chroma too. It removes base64 from embedding input; CPU savings depend on the model, and fixed-length padding can retain most inference cost. It does not change Chroma's own word index (Chroma indexes the stored document) or the vector count.

### A0 indexing policy

New and updated drawers retain their verbatim documents. Core detects validated
base64 on the whole input before chunking. Each physical chunk indexes its own
range: a span emits its placeholder where it starts, continuation fragments are
removed, and a chunk wholly inside a span indexes the placeholder. Padding-only
intersections create no span and stay literal in index text, whether they would
otherwise emit or continue a placeholder. No vectors are removed in A0; the vector
count is unchanged. Keyword-only message policy is B1c.
Queries use the same placeholder rule when they contain validated base64.
At visible candidate edges, Unicode letters (L*), numbers (N*), marks (M*),
connector punctuation (Pc) and characters in the base64 alphabet continue a word;
everything else delimits. Both edges look past format characters (Cf), except
ZWSP (U+200B), to the nearest visible character. A document or chunk edge counts
as a delimiter, and ZWSP is a separator. Look-past is capped at 32 Cf characters
per edge; longer stretches conservatively count as touching a word. Word edges
behave identically; elsewhere NFD never gets a placeholder NFC would not.
Decomposable symbols such as ≠ can retain more text in NFD. The detector and hint
validator share this rule and inspect original code points without normalization,
preserving span offsets.
The index view also replaces NUL and lone surrogates with spaces for inference,
rather than the previous surrogate-to-U+FFFD embedding-only cleanup. Stored
strings retain those characters through the existing reversible encoding.

The reserved `_mempalace_index_spans` metadata key carries version 1 JSON with a
SHA-256 of the verbatim chunk and clipped local spans. This preserves boundary
context during repair, migration and replication without storing another prose
copy. An empty span list records a whole-input rejection of a base64-shaped
run, so a valid-looking fragment cannot override that decision after splitting.
Hints are trusted provenance from server code or a replicated palace belonging
to the same owner; drawer ingress strips caller-supplied hints. The SHA-256 binds
a hint to its chunk but does not authenticate it. Complete runs are revalidated
by the detector. Fragments at chunk edges cannot be fully validated without the
whole content, so their acceptance, media label and decoded size rely on that
trusted provenance. Every fragment must contain at least one payload character:
padding-only hints are rejected whether emitting or continuing, and zero-byte
placeholders are invalid. Validation still checks fragment characters, visible word
boundaries using the shared original-code-point predicate, and placeholder
format and size bounds (150 to `sys.maxsize` decoded bytes, with an implied
encoded length large enough to cover the fragment). Edge fragments can therefore
remove or replace short tokens in the index; hints are not a security boundary
against a malicious palace writer. Stored documents remain verbatim.
Removed hints are tombstoned on merging writes. A stale or invalid hint
logs a rate-limited warning and falls back to detection on the supplied document. A projection failure logs a warning and stores the
verbatim chunks without hints rather than losing the source. The hint is removed
from drawer arguments and public responses; it never changes the document returned by `get_drawer`.

The embedding wrapper supplies explicit vectors on Chroma and the other vector
backends. The `sqlite_exact` and `compact` backends' `docs_fts` rows receive the same core index view, including persisted span hints. Chroma's own FTS
continues to index the stored document: its internal FTS cannot be given separate
index text. A0 therefore does not remove base64 from Chroma's internal word index. Remote
plugin lexical paths (Qdrant, Milvus and pgvector) also retain their stored-text
behavior; separate remote index fields and schema migration are follow-up work.

Existing data is not re-embedded or reindexed automatically. An operator can
optionally run `mempalace repair rebuild-index` on a backed-up Chroma palace to
rebuild vectors with this policy. Existing span hints survive that rebuild.
Pre-A0 fragments without provenance can only be detected independently; to recover
whole-input context, re-ingest the original source through an explicit operator
workflow. This change does not run either operation.

The mirror currently calls `add_drawer` with 4,000-character transcript chunks.
A0 protects each whole `add_drawer` input and its subsequent physical split.
A base64 run already split at the upstream 4,000-character boundary can leave
short fragments without enough context for validation. B1c replaces that mirror
unit with message-class groups and supplies whole-message context; A0 does not
change the mirror transport or the ThreadStore lexical policy.
Source adapters that split their own input before calling `upsert_drawer` have
the same limitation: each supplied chunk lacks whole-source context, so A0 can
only detect base64 independently within that chunk.

## B1a: Thread store v2

Same `ThreadStore` API and semantics: idempotent `store`, prefix-only `append`, `ThreadConflict`, one transaction per call.

Files and versions:

- New file name `threads.v2.sqlite3`, `PRAGMA application_id` and `user_version` set. An old binary never sees it, so it cannot create empty v1 tables over it. The v1 file stays until B1d removes it.
- `canonical_sha256` is computed with `surrogatepass`. For every thread v1 can store today the digest is identical; threads with lone surrogates (which v1 rejects with an encode error) become storable.

Layout:

- `threads`: as in v1 (same ids, `parent_stored_id`, counts, `sha256`, wing, stamps).
- `line(thread, line, block)`: a source line. The API receives messages, not lines: a line is a run of consecutive messages with equal `raw`. Today `raw` is stored once per message.
- `message(thread, seq, line, role, kind, name, call_id, at)`.
- `block(thread, idx, first_line, n_lines, first_seq, last_seq, raw_dict, zraw, text_dict, ztext)`:
  - `zraw`: consecutive source lines up to ~64 KB, zstd level 3.
  - `ztext`: the same lines' message texts, length-prefixed, so a text read never touches raw.
  - Base64 runs are cut out of both frames. Each frame carries a span list (offset, blob key); there are no inline markers, so nothing needs escaping.
- `blob(key, kind, data)`: key = hash(kind ‖ payload). `kind` is `decoded` (canonical base64, stored as bytes) or `text` (stored as the original string). The span carries the key, so a decoded blob and a string with the same bytes never collide.
- `dict(id, frame, data)`: zstd dictionaries trained from this workspace's own data only. Bootstrap: blocks are written without a dictionary until enough data exists. A dictionary is immutable once used.
- `chunk(thread, idx, seq_start, seq_end, off, len)`: the stored chunk boundaries. Appended threads keep their append-time boundaries (they differ from `chunk_messages` over the whole thread). Chunk text is rebuilt from messages in the exact `chunk_messages` format, split pieces included.
- `chunk_fts`: contentless FTS5 (`content=''`, no `contentless_delete`: the thread store never deletes), rowid = chunk rowid. UNINDEXED columns read NULL in contentless tables, so every query joins `chunk` on rowid.
- `index_meta(index_version)`: which index-text rules built `chunk_fts`. A reindex rebuilds `chunk_fts` from stored text; storage never changes.
- `handoff_docs` and `fact_extraction`: as in v1.

Rules:

- Reads return exactly what the transcript parser produced: every field, every byte of `raw`, lone surrogates and NUL included, for all three formats (`claude-code`, `codex`, `messages`).
- Every block is read back and compared before its transaction commits.
- Blocks are immutable, except the last block of a thread: an `append` may extend it in the same transaction, verified by read-back, so per-turn appends do not leave thousands of tiny blocks.
- `append` checks the incoming prefix against `threads.sha256` without decompressing the thread.
- A query containing NUL uses the existing literal-scan fallback over rebuilt text.
- Measured on the prototype: 128 MB for the 250 MB corpus (4 KB pages); read 50 messages 0.8 ms (text) and 1.5–1.9 ms (with raw); keyword top 8 with text 9 ms. B1a's PR measures small-block overhead under per-turn appends.

## B1b: `compact` palace backend

A new backend `compact` with the RFC 001 contract. It reuses `sqlite_exact` code where it fits, but has its own file name (`compact.sqlite3`) and `application_id`, is not part of the exact-vector family, and is added wherever the code lists backends by name.

- Selection: workspace config `backend: compact` (the config value wins over `MEMPALACE_BACKEND`), or `--backend compact`.
- `documents`: `document` as a zstd BLOB (per-collection dictionary from the palace's own data, immutable once used, bootstrap without one); `metadata_json` unchanged; `embedding` nullable.
- Keyword-only records: marked in metadata (a reserved key), so update, repair, migrate and replication never re-embed them. Vector queries skip them; `get` and lexical search see them.
- Vectors: int8 plus one float32 scale (388 B for 384 dims); the in-RAM cache stays int8. Exact cosine scan in numpy first; the Rust crate has no int8 kernel yet. Do not advertise `supports_embeddings_passthrough`: int8 does not return vectors as given (RFC 001 §1.5).
- `docs_fts`: contentless FTS5 with `contentless_delete=1` (requires SQLite ≥ 3.43; the backend refuses to open on older SQLite), rowid = document rowid, fed with core's index text. Queries join on rowid.
- Filters: today `sqlite_exact` evaluates `where` in a per-row Python loop (37 ms for a wing filter, 114 ms for wing + room on 112k rows). `compact` must:
  - build filter masks vectorised or in SQL, then scan only matching rows;
  - push `$or` on ids and parent ids into SQL, with an index on the parent drawer id;
  - decompress only the rows it returns, including in `get` and filtered `lexical_search`;
  - keep a LIMIT on filtered lexical queries (PR #38's review found filtered lexical paths that score every match in Python).
- `get_drawer` hydration of split drawers (`$or` over parent keys, used by `palace_mirror` for every split mirror drawer) gets its own latency gate.

## B1c: Mirror unit and index policy

- Today the mirror writes 4,000-character chunks of mixed messages, and `add_drawer` cuts them into 800-character pieces, often through a base64 run. A per-kind policy cannot apply to mixed pieces.
- New mirror unit: consecutive messages of one class (conversation = user, assistant and thinking; tool call; tool output; system), grouped up to 4,000 characters. Index text is derived on the whole unit before it is split.
- Policy (decided 30 Oct): conversation and tool-call drawers are embedded; tool output and system drawers are keyword-only.
- This policy applies only to `compact`; other backends continue embedding every class. A dedicated internal mirror operation takes the unit identity, message class and index-span provenance. The class decides the compact policy, and the identity determines stable drawer IDs even for identical continuation bodies. Only the authenticated internal gateway channel or direct server-side callers can invoke it; it is absent from MCP schemas. Public `add_drawer` retains its content-derived IDs, and client ingress strips forged policy arguments and reserved metadata.
- Mirror groups stop at class, checkpoint and append boundaries. Oversized messages retain one header and verbatim continuation pieces, with whole-message index provenance projected through both the 4,000-character mirror limit and the 800-character drawer split. Source thread and inclusive message ranges remain exact.
- Existing mirrored ranges are read without rewriting or re-adding drawers. One `mirror_progress` row per thread records the completed sequence and pending batch end; a retry finishes that same batch before starting later appends. Progress advances after every unit in the batch commits. Both thread-store formats retain this row, and verified store switching and rollback copy and compare it field by field.
- Ranking: `_hybrid_rank` caps a hit without a vector distance at 0.4 × BM25. Keyword-only by design must not be penalised like "outside the vector pool". If the gate shows it, rank keyword-only records on BM25 alone.
- Compact's intentional keyword-only records use the normalized scope-local BM25 contribution alone; ordinary lexical additions retain the existing hybrid weights. Recall deduplicates overlapping source message ranges rather than assuming mirror units and thread FTS chunks share boundaries.
- Gate: the PR #39 gate through default `mempalace_search` (`union`, PR #38), plus end-to-end recall@10 on the existing benchmarks against today's default search, not only overlap with float32.
- The vector-count saving (112k → ~18k on the corpus) was measured per message. B1c's PR measures it with the real mirror unit and restates it.

## B1d: Flag, backfill, switch, rollback

- Thread store v1 → v2 is a row copy, never `store()`: same thread ids, chunk boundaries, parent chains, `handoff_docs`, `fact_extraction`. The copy is verified before it counts:
  - every message field equals v1;
  - every rebuilt chunk text equals v1 `chunks.text`;
  - every digest equals v1.
  - A mismatch aborts that workspace.
- Palace → `compact` is built in a sibling directory, never next to the live files: a Chroma file and a compact file in one palace directory make `resolve_backend_name` raise `BackendMismatchError`.
  - Hold the palace writer lock, or copy first and then replay the write-log entries written since.
  - Verify every record of every collection: ids, metadata, document text, vectors within quantisation error.
  - Carry over the knowledge graph, `logstream.sqlite3`, `replica.json` and sidecars the way `migrate._carry_over_palace_files` does.
  - Then rename-swap.
- Dual-read after the switch compares a sample of live reads and searches with the old store. Logs hold ids and hashes only.
- Rollback: the copier works in both directions with the same verification. Writes made after the switch exist only in `compact`, so rolling back copies them back.
- Old files are deleted after 14 days. They cannot be crypto-shredded (no per-workspace key), and existing backups keep them until they are pruned.
- `commonplace doctor` reports store format, index version and backfill state per workspace.

## B1e: Vectors past 1M per collection

- Gate: recall@10 ≥ 0.98 against float32 exact at 1M and 4M vectors, with query p95 within the search budget.
- The 1-bit + int8 rescore of the top 400 measured 0.953, which fails. Candidates: a native int8 kernel (exact), a larger rescore pool, partitions by wing and time. B1e's PR picks one by measurement.

## B2: Encryption at rest (summary; own PR)

- SQLCipher for the v2 thread store, the palace, the knowledge graph and `boards.sqlite3`, with one random 256-bit key per workspace.
- Pinned settings: 16 KB pages, HMAC-SHA256, 64 MB page cache, raw key (no passphrase KDF), `temp_store=MEMORY`. Untuned SQLCipher was 3–5× slower on reads.
- The key is wrapped in keyslots (server slot, recovery code) kept in a small key store outside the workspace and outside long-retention backups.
- Backups copy file bytes after a checkpoint (or a snapshot), never the SQLite backup API: a new chat changed 2.9% of pages in a byte copy, 100% through the backup API.
- `drawer_revisions.jsonl`, entity registries and exports move inside the encrypted files or are encrypted with the same key.
- Measured cost: +0.4% disk, +10–22% write CPU, reads +0.2–0.5 ms, keyword search +9 ms.

## Tests and gates

- Lossless: the transcript parser's output is the oracle. Synthetic transcripts in all three formats with multi-message lines, long messages split across chunks, appended threads, lone surrogates, NUL, non-canonical base64, duplicate blobs, and a decoded blob whose bytes equal a text blob.
- Backfill: row-copy verification as in B1d, on v1 files made by today's code.
- Conformance: the RFC 001 backend suite for `compact`, plus keyword-only records through update, repair, migrate and replication.
- Search: end-to-end recall@10 against today's default search on the existing benchmarks; the PR #39 gate for B1c.
- Latency at 100k and 1M drawers: unfiltered and filtered vector queries, filtered lexical search, `get_drawer` hydration of split drawers. Budgets: hooks < 500 ms, startup injection < 100 ms.
- Size: bytes per source byte for v1/v2 and chroma/compact on synthetic chats, first cut as built.
- No private data in tests or fixtures. `zstandard` becomes a required dependency.

## Open points

- Content references: let `compact` point mirror drawers at thread-store blocks instead of storing their text again. Needs a resolver across the layers. Measure the first cut's duplicate cost first.
- A native int8 (and 1-bit) scan kernel in the Rust crate.
