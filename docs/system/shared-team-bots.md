### Shared team bots

- `docs/design/shared-team-bots.md` is the design note for sharing a bot with workspace members (card #462). Decisions it proposes: a shared bot acts with the intersection of the bot's owner-set access, the talking member's rights and the owner's rights; private threads stay private to their person; sharing is off by default per workspace and a new share reaches nothing until the owner grants it. No code changed; slices follow one PR each.
