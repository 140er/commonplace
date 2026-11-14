### Gateway search closet preview for owners

- Decision (owner, 10 Nov): the closet_preview strip on gateway search applies to
  non-owners only. A person session with the owner role, a trusted policy and
  access to every wing keeps the field; members, API tokens (including tokens
  issued by an owner), scoped tokens and untrusted principals still never receive it.
- The leak the strip closes is a closet line from a room the caller cannot read.
  An unrestricted owner already holds workspace-wide authority, so the preview
  adds no disclosure for that session.
- Only the outgoing preview field changes. Ranking, closet boosts and every other
  citation or provenance scrub are unchanged for all principals.
- Supersedes the "every result" wording in the closet preview refusal note for the
  owner session; the refusal still applies to everyone else.
