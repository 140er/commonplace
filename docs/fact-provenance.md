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

Handoff documents and thread recall include source, filing actor and proposal/unknown status. Uploaded transcript roles are unverified, including when a person uploads the transcript. Archived documents retain their exact text with an unknown-authority wrapper. Fact approval must be checked in the fact index before treating an excerpt as a user rule. Facts that handoff extraction files automatically stay proposals with their exact source range, even when a person uploaded the transcript; approving one is a separate, explicit save. Automatic extraction through a person's web session records the source thread as the origin, with the authenticated uploader retained in the caller stamp.

Run `uv run python benchmarks/fact_provenance_bench.py` for the small recall gate. It checks that the invented SimSteer 5% gate stays an agent proposal when repeated, a person's 2% decision has approval evidence, the old rule points to its successor, a later agent proposal cannot retire the approved decision, and a backdated person approval retires a later-dated proposal filed first.
