# RFC 008: Compact workspace store

Status: proposed, 30 Oct 2026. Research and measurements: `~/work/mempalace-notes/storage-encryption-research.md` (option B).

## Why

On 250 MB of real agent chats (130 Claude Code and Codex transcripts, 54,494 messages):

- Today's stores take 1,733 MB (6.9× the chats): the thread store 642 MB, the Chroma mirror 1,091 MB.
- The same content in the prototype of this design takes 146 MB (0.59×), byte-for-byte lossless.
- Writing one chat into the Chroma mirror takes ~76 s (192 `add_drawer` calls, mean 398 ms). The prototype writes a chat in 0.1 s, excluding embedding.
- 43% of raw chat bytes and 53% of message text are base64 (screenshots, encrypted reasoning, signatures). 51% of the drawers embedded today are mostly base64. 86% of text is tool output.

Jeffrey decided on 30 Oct (23:01 CET): keep all agent chats; embed conversation and tool calls only after the recall gate; operator-blind token slots. All agent chats at his rate are ~9 GB a month raw: 65 GB a month in today's format, ~5.5 GB in this one.

## Goals

- Store every verbatim byte once, compressed, and keep it exactly recoverable.
- Index text that is useful for search: base64 runs become placeholders in the index, never in storage.
- Let a record be keyword-only (no vector), so tool output can stay findable without embedding it.
- One key per workspace later (B2): every file of a workspace encrypted with SQLCipher.
- No breaking change to MCP tool shapes (optional arguments only), the ThreadStore API, or search results apart from intended ranking changes.

## Non-goals

- Changing the embedding model, chunk sizes or ranking weights.
- Server-side search over sealed records.
- Moving the thread store into MemPalace. The layers stay as they are; each gets a compact format.

## Phases

| Phase | What | Layer | Card |
|---|---|---|---|
| B1a | Thread store v2 format | Commonplace | #40 |
| B1b | `compact` palace backend | MemPalace | #40 |
| B1c | Mirror policy: placeholders in index text, tool output keyword-only (was QW4) | both | #39 |
| B1d | Flag, backfill, dual-read, switch | both | #40 |
| B2 | SQLCipher on every workspace file + keyslots + byte-copy backups | both | #41 |
| B3 | Operator-blind token slots | Commonplace | #42 |
| C | Capture all agent chats (after B1a + B1d) | Commonplace | #43 |

B1a and B1b are independent and can be built in parallel. B1c needs B1a, B1b, the `union` default search (#37) and a passing recall gate (#38).

## B1a: Thread store v2

Same file (`threads.sqlite3`), same `ThreadStore` API and semantics: idempotent `store`, prefix-only `append`, `ThreadConflict`, one transaction per call, `canonical_sha256` unchanged.

Layout:

- `thread` as today.
- `line(thread, line, block)`: one row per source line of the transcript.
- `message(thread, seq, line, role, kind, name, call_id, at)`: message fields except `text`; `raw` is the message's source line. Several messages of one line share it (today `raw` is stored once per content block).
- `block(thread, idx, first_line, n_lines, first_seq, last_seq, zraw, ztext)`:
  - `zraw`: consecutive source lines up to ~64 KB, zstd level 3 with a trained dictionary.
  - `ztext`: the same lines' message texts in a second frame, so a text read never touches raw.
  - Base64 runs are cut out of both frames and replaced by a reference to `blob`.
- `blob(hash, kind, data)`: base64 payloads, deduplicated by hash, stored decoded as binary when the encoding is canonical (re-encodes to exactly the input), else as the original string.
- `dict(name, data)`: zstd dictionaries, versioned. A dictionary is never changed once a block uses it.
- `chunk(id, thread, idx, seq_start, seq_end, off, len)`: chunk boundaries only. Chunk text is rebuilt from messages with the existing `chunk_messages` format.
- `chunk_fts`: contentless FTS5 (`content=''`, `contentless_delete=1`), rowid = `chunk.id`, fed with index text (base64 runs replaced by placeholders, see B1c).

Rules:

- Reads return the exact `ThreadMessage` values the parser produced: every field, every byte of `raw`, lone surrogates included.
- A block is written once. `append` adds new blocks; it never rewrites an old one.
- Measured (prototype): file 128 MB for the 250 MB corpus with 4 KB pages; read 50 messages 0.8 ms (text) / 1.5–1.9 ms (with raw); keyword top 8 with text 9 ms.

## B1b: `compact` palace backend

A new backend registered as `compact`, built from `sqlite_exact` (same RFC 001 contract, same file per palace, own magic/format marker).

- `documents`: as in `sqlite_exact`, but `document` is a zstd-compressed BLOB (per-collection dictionary), `metadata_json` unchanged, `embedding` nullable.
- Vectors: int8 with one float32 scale per vector (388 B for 384 dims). Exact cosine scan, using the Rust crate when present. Measured recall@10 vs float32 exact: 0.984 (float16 scores 0.985 on the same set; the misses are near-duplicate drawers swapping places).
- Past ~1M vectors in a collection: 1-bit vectors in RAM (48 B each) plus int8 rescoring of the top 400 (recall@10 0.953, 68 ms at 1M in numpy). Add when a palace needs it, not in the first PR.
- `docs_fts`: contentless FTS5 over index text, rowid = document rowid. `lexical_search` implemented on it.
- Keyword-only records: a write may carry no embedding when the caller asks for it (B1c). Vector queries skip those rows; lexical and `get` see them.
- Filters (`where`) are evaluated on metadata as in `sqlite_exact`. A filter never turns a vector query into a slow path: today Chroma takes 519 ms for a `wing` filter that matches every drawer.
- Measured (prototype, 112k drawers): vectors 47 MB in SQLite; open + load all int8 vectors 0.16 s.

## B1c: Mirror and index policy (was QW4)

- Base64 detector: the validated rule from #38 (200+ characters, one alphabet, canonical, decodes, entropy > 4.5 bits/char), applied per message before chunking. Index text replaces each run with a placeholder like `[base64 image/png 245760 B]`. Stored text never changes.
- Embedding policy (Jeffrey, 30 Oct): embed conversation (user, assistant, thinking) and tool calls. Tool output and system messages are keyword-only records.
- `add_drawer` gains an optional `lexical_only: bool` and `index_text` is derived by the backend; backends that cannot do keyword-only records embed as today.
- Chroma cannot do either (it indexes the stored document and needs a vector per record), so B1c ships on `compact` only.
- Ships only when the #38 gate passes through default `mempalace_search` with the `union` default (#37).

## B1d: Flag, backfill and switch

- Per workspace setting `store: compact`. Commonplace passes `MEMPALACE_BACKEND=compact` to that workspace's worker and opens the v2 thread store. Default stays as today until switched.
- Backfill is a background job per workspace:
  1. Thread store v1 → v2: copy every thread, then compare `canonical_sha256` per thread and every message field. Any mismatch aborts that workspace.
  2. Palace → `compact`: copy every record with its existing vector (quantised to int8), apply B1c's policy to mirror drawers.
  3. Dual-read: answer from the new store, compare against the old one on a sample of live searches and reads, log differences.
  4. Switch, keep the old files 14 days, then delete them. They cannot be crypto-shredded (no per-workspace key), and restic keeps them until it prunes.
- `commonplace doctor` reports the store format and backfill state per workspace.

## B2: Encryption at rest (summary; own PR)

- SQLCipher for `threads.sqlite3`, the palace, the knowledge graph and `boards.sqlite3`, with one random 256-bit key per workspace.
- Pinned settings: 16 KB pages, HMAC-SHA256, 64 MB page cache, raw key (no passphrase KDF), `temp_store=MEMORY`. Untuned SQLCipher was 3–5× slower on reads.
- The key is wrapped in keyslots (server slot, recovery code), kept in a small key store outside the workspace and outside long-retention backups.
- Backups copy the file bytes after a checkpoint (or a snapshot), never the SQLite backup API: a new chat changed 2.9% of pages in a byte copy, 100% through the backup API.
- `drawer_revisions.jsonl`, entity registries and exports move inside the encrypted files or are encrypted with the same key.
- Measured cost: +0.4% disk, +10–22% write CPU, reads +0.2–0.5 ms, keyword search +9 ms.

## Tests and gates

- Lossless: round trip every message (all fields, raw bytes) on synthetic transcripts covering both formats, multi-block lines, lone surrogates, NUL escapes, non-canonical base64, duplicate blobs; property tests against the v1 store as oracle.
- Conformance: the RFC 001 backend suite passes for `compact`, plus keyword-only records.
- Search parity: recall@10 of `compact` vs `sqlite_exact` float32 on the existing benchmarks ≥ 0.98; the #38 gate for B1c.
- Budgets: hooks < 500 ms, startup injection < 100 ms on a 100k-drawer palace.
- Size: a benchmark on synthetic chats reports bytes per source byte for v1/v2 and chroma/compact.
- No private data in tests or fixtures.

## Open points

- Content references: mirror drawers duplicate thread text (compressed, ~30–50 MB per 250 MB of chats). A resolver that lets `compact` point at thread-store blocks would remove that. Measure after B1d; not needed for the first cut.
- Native kernels for the int8 and 1-bit scans in the existing Rust crate.
