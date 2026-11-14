- **Owns:**

  - Deck default-look regressions (`tests/commonplace_deck.mjs`, wrapped by `tests/test_commonplace_deck.py`): first visit, stored `classic` or `deck`, `?ui=classic` kept across a reload, unreadable storage, the pre-paint script and the Look switch, plus mutation checks that put the old `=== 'deck'` rule back and must fail. Harnesses and browser cases that test classic markup name the classic key explicitly.
