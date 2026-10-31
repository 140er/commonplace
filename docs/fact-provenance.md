# Fact provenance

A fact's deciding source is separate from the account an agent uses. An agent token belonging to Avery still writes an agent proposal.

`fact_save` requires `source: {thread, seq_start, seq_end}`. For a chat outside Commonplace or T3, include `source.url` with its chat link. Stored `thr_…` IDs link to `/app/threads/<id>`; T3 `mcp:…` IDs link to `t3-thread://v1/<id>`. The exact source messages remain verbatim.

Every KG recall returns:

- `origin_kind`, `origin_id`, `origin_link`: who first filed the decision and the originating thread. Merging another observation preserves these fields.
- `approval_status`: `proposal`, `approved`, or `unknown`.
- `approved_by`, `approval_link`: the signed-in person and source establishing approval, separately from original authorship.
- `fact_status`: approval status while active, or lifecycle status such as `superseded`. `superseded_by` identifies the successor. Existing `status` and validity dates retain their lifecycle meaning.
- `sources`: exact observed thread ranges, with each observation's origin and approval evidence. Approval citing an already recorded range updates its approval evidence while keeping the range's original author; a later agent restatement cannot clear that evidence.

Agent saves default to `proposal`. A transcript's `role=user`, an agent claiming “Avery approved”, and an account's `hub_user` stamp cannot establish approval. Client-supplied authority fields are refused.

A signed-in person saves or approves a fact with `POST /api/facts`, using the same subject, predicate, object, theme and source shape as `fact_save`. Saving the existing triple adds the person's approval and preserves its original author. Person sessions use the gateway's JSON and same-origin checks. Agent bearer requests to this endpoint remain proposals. Approval needs a navigable source link.

An agent proposal cannot automatically close an approved competitor. Both remain visible with their status. A person approving a competing single-valued value retires the previous value and records its successor. Only a later approved value can file a person's decision as history. Competing proposals link `superseded_by` to the approved fact and end at the approval boundary or their own start, whichever is later. This preserves chronological intervals while preventing a future-dated proposal from taking over or expiring the approval. Raw agent supersede/invalidate attempts against approved facts are refused.

Existing rows migrate to `origin_kind=unknown`, `origin_id=unknown`, `approval_status=unknown`; authorship stamps never imply approval. Compatibility writes through the older low-level graph API without known authorship or a navigable chat pointer remain visibly unknown in those source fields and cannot establish user approval. Replication carries provenance columns; old peers missing them do not erase existing provenance.

Graph imports first match one exact fact within the same wing and validity window. Only when no exact row exists may a superseded source match a single open active target; multiple open candidates are never reconciled. For both exact matches and open fallbacks, a source may merge only into the same instance: either the ids match, or the target has no row with the source id and the candidate target id is not held by another source row. Divergent history under the source id goes to `id_conflicts`; another source's instance cannot receive its end, approval or observations. Each target row can be claimed by only one source instance per import. A second eligible claimant is skipped and reported in `triples.conflicts` with reason `target_already_claimed` and its `id`, `target_id` and original source claimant (`claimed_by`); its observations are not merged. Identical duplicates within the source (same content, validity window, lifecycle status and successor) instead alias an already accepted instance for observation deduplication only, provided the target does not hold their own id. These aliases do not stage another end or copy fact metadata. Exact history therefore keeps its own approval, observations and successor links without changing a later reassertion of the same value. A source's superseded end can close that open target only after its successor resolves to a target id. Otherwise the target stays open and `triples.end_not_carried` lists the source, target and successor ids. Unapproved source history cannot close an approved target; the import reports an `approved_target` conflict. Existing target approval proof and original authorship stay intact. Successor links resolve by triple identity to the target's id. For newly imported history without an accepted successor, the pointer stays unset and `triples.unresolved_links` lists the source fact and successor ids; `unresolved_link_count` gives the count. Imports run in one transaction, including evidence and lifecycle changes, and previews run only against an in-memory snapshot.

`fact_sources` observations travel with imports and replication, including their origin and approval evidence. Observation ids are local to each graph: receivers resolve the target fact by triple identity and dedupe by the existing source-range/drawer/file key. A duplicate may gain person approval, while its original author, timestamp and existing approval proof remain intact. Imported approval requires the stored `approved` status, separate person id and valid approval link; the importing caller and legacy author stamps cannot supply missing authority. Legacy sources stay unknown. A skipped fact's observations are listed under `fact_sources.skipped`. Replication snapshots include `_triple` identity and the source fact id on each observation; apply triples before their observations. The source fact id also travels in `triple_id` for older snapshots. An observation cannot match a different target instance when its own source fact id already exists locally with divergent history. Follow-up: individual observation rows do not carry the peer graph's complete set of fact ids. Replication needs batch context before remaps can also reject target ids held by other peer rows, as bulk imports already do.

Replication's `apply_row` returns an `applied` flag and a skip `reason` when needed. Existing fact identity, original authorship, first-source fields and approval proof are preserved. Lifecycle fields only advance: an unset `valid_to` or `superseded_by` may be filled, but a stored non-null end date or successor link is never cleared or replaced. A non-active `status` is not rewritten. Such conflicting peer rows are skipped with `lifecycle_regression`, even when they carry person approval; stale snapshots cannot reopen a fact or unlink its successor. An invalidated fact whose status is still active may acquire superseded status and a successor without changing its stored end date. These allowed changes to an approved fact still require person-approved evidence, and a superseding successor must also be approved. Missing successor links stay unset and appear in `unresolved_links`. An existing open target is skipped with `unresolved_successor` and `end_not_carried` until its successor arrives; retry the row afterward. New history can arrive before its successor with a null link, which a later replay resolves. An observation without an exact target fact returns `no_matching_triple` instead of stopping the replication loop.

Scoped KG query and timeline reads use `mempalace_kg_list_wings`, a single indexed wing query, rather than calculating graph statistics. Direct access to the full fact-wing list, like KG statistics, requires read access to every wing.

Handoff documents and thread recall include source, filing actor and proposal/unknown status. Uploaded transcript roles are unverified, including when a person uploads the transcript. Archived documents retain their exact text with an unknown-authority wrapper. Fact approval must be checked in the fact index before treating an excerpt as a user rule. Facts that handoff extraction files automatically stay proposals with their exact source range, even when a person uploaded the transcript; approving one is a separate, explicit save. Automatic extraction through a person's web session records the source thread as the origin, with the authenticated uploader retained in the caller stamp.

Run `uv run python benchmarks/fact_provenance_bench.py` for the small recall gate. It checks that the invented SimSteer 5% gate stays an agent proposal when repeated, a person's 2% decision has approval evidence, the old rule points to its successor, a later agent proposal cannot retire the approved decision, and a backdated person approval retires a later-dated proposal filed first.

Search also returns matching facts before drawers: a separate `facts` list in
`mempalace_search` and hub responses, or a Facts section in the CLI. The drawer
list keeps its existing order and limit. Facts include `source_link`, exact
thread ranges, `source_drawer_id`, all observed `sources`, origin and approval
fields, and `superseded_by`. An ended fact keeps its separate approval evidence
but displays a historical/superseded status. Legacy CLI-capture responses omit
an empty facts list to preserve their existing shape.

The gateway includes facts only for callers allowed to use `mempalace_kg_query`,
with that tool's wing and sensitivity scope. Source links still open through the
existing source reader's access checks. `mempalace_kg_query` with `search=true` uses its `entity` argument as search
text and exposes the same ranker for fact-only, optionally as-of reads. Ranking details and constants are
documented in [RFC 009](rfcs/009-fact-first-ranking.md).

Run `uv run python benchmarks/fact_recall_bench.py --threads 120 --retriever fact-first`
for the fact-first gate, and `uv run python benchmarks/fact_first_bench.py --threads 120`
for the plain-drawer comparison and the existing `search_benchmark` drawer gate.
