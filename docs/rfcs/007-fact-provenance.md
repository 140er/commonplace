# Fact provenance and user approval

An agent proposal must remain distinguishable from a user-approved fact or rule. The authenticated account on an agent connection identifies its caller; it does not prove that the user made or approved the statement. A transcript's `role=user` is also insufficient evidence of human approval.

Extend existing KG triples with origin kind/id/link, approval status, approved-by and approval-source fields. Keep the existing lifecycle status and superseded_by pointer. Reads expose an effective fact status: unknown, proposal, approved, or superseded. Existing records migrate to unknown origin and approval; legacy caller stamps are never interpreted as user approval.

New agent writes default to proposal and record their verified connection identity plus originating thread/link. User-originated or explicitly user-approved writes require a verified person context and a source link; agent-supplied approval or user-origin claims cannot elevate authority. A proposal cannot automatically supersede an approved fact. Repeated saves preserve the original source and any verified approval.

Use the existing fact-save merge/supersede path, with a human-authenticated save/approval path. Source links are validated, and a stored thread gets its existing authenticated thread-reader link. Unknown source details stay visibly unknown. Facts, rules and decision quotes share this representation.

KG recall and handoff output show source and status alongside the exact words. Handoff excerpts without verified human provenance remain proposals or unknown, including legacy handoff documents. Source access checks continue to apply.

Tests cover legacy migration, agent/user defaults, forged approval attempts, merge and supersession safety, scoped reads, caller-versus-speaker attribution, handoff labels, and import/replication compatibility. A small recall eval plants an agent-invented performance gate and proves it is returned as a proposal rather than a user rule, then verifies explicit human approval and supersession.

This builds on handoff extraction (#29). Run the project checks and obtain a separate review of the final revision before merging.
