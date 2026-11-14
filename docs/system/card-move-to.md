### Moving a card is a plain move in the board's own words

Decision: a person moves a card with Move to… on the card, using the board's column
names (Active, In review, Shipped). Moving to In review is a move, not a question:
the card gets the ask text "Moved to review." with review kind `moved`, no recipient,
and the person can move it on again. The question flow remains only for a caller that
sends a question, which is how an agent asks a person. Needs you is derived from what
waits for a person, so it is never a destination.

A 409 on a card write now says which of three things happened: a stale version (the
reply carries the current card, so a second tap on a move that already landed counts as
moved and an unrelated version bump is sent once more), a refusal with its own code and
reason, or a busy workspace. Authority checks are unchanged: a person moves their own
cards, and an agent follows its claims.
