### Release block: members see who can release

On a card waiting for release, a person who may not release it sees the owner-only reason
("Only the workspace owner, or this project's coordinator, can release this card.") instead of an
empty space, and never the Release button or the reason field. The node harness
`tests/release_block.mjs` checks both halves.
