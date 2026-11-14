- **Owns:**

  - A signed-in person's `PATCH /api/cards/<n>` to `review` with no question, options, context or person reason is a plain move: the card lands in Review with the ask text "Moved to review.", review kind `moved`, no recipient and no return address. It never needs the person's own decision, so `need` is not `you` for it. A PATCH that does carry a question still opens a decision as before, and an agent's move to Review is unchanged.
  - A person may move a card they put in Review (kind `moved`) on to todo, doing or done. A review that holds an agent's question still has to be answered with decide.
- **Promises:**

  - Moving to Review never asks a person anything unless the caller sent a question.
  - A 409 from the card routes means one of three things and says which: a stale version (body `error: card changed` with the current card), a refusal with its own code and reason (snoozed, blocked, limit), or a busy workspace (`code: busy`).
- **How it fails:**

  - A card that already holds an agent's question refuses a person's PATCH with "use decide to move a card from review".
  - Todo to done and backlog to doing or review stay refused for a person, with the plain reasons from the done-move explanations.
- **Tests:**

  - `tests/test_commonplace_card_move.py` covers the plain Review move, moving on from it, the unchanged decision paths and the three kinds of 409.
