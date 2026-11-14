### Live coworker path ownership

Coworker conversation list/detail reads now project declared board-card paths and current admission holds after conversation membership and project read checks. The additive `owns` object is bounded, read-only and recomputed per viewer; unavailable sources never imply unlocked paths. Field types, nullability, source limits and an example are documented in `docs/modules/chat-and-voice.d/coworker-owns.md`.
