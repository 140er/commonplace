---
name: mempalace-recall
description: "Search MemPalace before answering anything earlier sessions may have covered: what was decided, why, by whom, unfinished work, and \"anything I should know about X\", \"safe way to do Y\", gotchas or warnings about this repo or machine. Search before saying you don't know. If memory has nothing, answer from the code. Requires the mempalace-mcp server."
---

# MemPalace Recall

Search-before-answer protocol for MemPalace. The `mempalace` skill covers
install / mine / status; this one covers recall only. Wrong is worse than
slow: a palace hit beats a confident guess from model memory.

## When to recall

Search the palace **before answering** whenever earlier sessions may have
covered the question, even if the code could answer part of it:

- Past work or decisions: "what did we decide / try / do?", "why …?",
  "who …?", "what branch / what was left unfinished?"
- Warnings and know-how about this repo, machine, or setup: "anything I
  should know about X?", "what's the safe way to Y?", gotchas, things not
  to touch, why something is the way it is
- A person, project, or entity: "who is …", "what is …"
- An earlier session: "remember when …", "last time …"
- A preference or fact that could have changed over time

Always search before saying "I don't know" about earlier work, and before
relying on other memory files. Do **not** search on pure greenfield edits
("rename this variable", "fix this typo"); one search per question is
enough.

## Protocol

1. If a session-start hook injected `additional_context`, honour its wing
   scoping.
2. Use `mempalace_search` with a short keyword query (not a pasted
   conversation), `mempalace_kg_query` for relationships or time-bound
   facts, `mempalace_diary_read` for recent continuity. Stop when answered;
   see the
   [query guide](https://github.com/MemPalace/mempalace/blob/develop/integrations/shared/recall-protocol.md#retrieve-only-the-context-you-need).
3. Quote the drawer's **verbatim** text. Never summarize or paraphrase
   stored content.
4. Combine sources: the palace for why / who / what was agreed, the repo
   (code, `git log`, `git diff`) for what exists now. Say which is which.
5. After a substantive session, record continuity with
   `mempalace_diary_write` (skip if a background hook already saved).
6. When a fact changes: `mempalace_kg_supersede` for single-valued
   replacements, `mempalace_kg_invalidate` for facts that ended without
   replacement, `mempalace_kg_add` for coexisting facts.

## Tool selection

| You need | Tool |
|---|---|
| Find any memory by meaning | `mempalace_search` |
| Relational / time-bound facts about an entity | `mempalace_kg_query` |
| The chronological story of an entity | `mempalace_kg_timeline` |
| Recent session continuity | `mempalace_diary_read` |
| Which wings / rooms exist | `mempalace_list_wings`, `mempalace_list_rooms` |
| Record this session | `mempalace_diary_write` |

`mempalace_search` takes a short `query` plus optional `wing` / `room`
filters and `limit` (default 5).

**Active coordination is not recall.** To delegate to another agent on the
shared hub or wait for its reply or patch, use the logstream tools
(`mempalace_event_append`, `mempalace_event_wait`,
`mempalace_patch_submit`, `mempalace_artifact_get`); see the
[coordination protocol](https://github.com/MemPalace/mempalace/blob/main/integrations/shared/coordination-protocol.md).

## When memory has nothing

- **Empty palace or no hit.** One empty filtered search does not prove the
  palace has nothing; widen once (drop the wing filter, try other
  keywords). Still nothing: say so in one line, then answer from the repo
  (code, `git log`, `git diff`) and label it as such. Never stop at "the
  palace is empty", and never answer from model memory.
- **No record of a decision.** Code or a commit existing is not a decision.
  If neither the palace nor the repo records one, say none was recorded.
- **MCP tools missing or erroring.** Say the server is not connected (fix:
  `mempalace status`, `/mempalace-init`), then answer from the repo as above.
- **Index corrupt** (HNSW segment-writer error, ChromaDB compaction
  failure, "Not connected" after a write). Drawers in `chroma.sqlite3` are
  intact. Tell the user to stop the server and run
  `mempalace repair --mode from-sqlite --archive-existing --yes`, then
  `mempalace repair-status`. Do not re-mine (it drops MCP-added drawers and
  diary entries, #1843) or repair in-process.
- **Conflicting facts.** Trust the knowledge graph's time-valid answer.

## References

- MemPalace: <https://github.com/MemPalace/mempalace>
- Full recall protocol: [`integrations/shared/recall-protocol.md`](https://github.com/MemPalace/mempalace/blob/main/integrations/shared/recall-protocol.md)
