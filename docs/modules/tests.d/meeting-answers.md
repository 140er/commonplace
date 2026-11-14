- **Tests:**

  - `tests/test_meetings_answers.py` covers in-meeting answers over the fake provider and a scripted model (see the `meetings` module block): the clean workspace, the addressed filter, the scope at each disclosure with a sentinel leak test, the refusal for a pinned project the inviter cannot read, no write from chat, the closed reply schema, the reply format, replay, cooldown and cap, and `bot stop` with a slow model.
