- **Tests:** `tests/test_commonplace_session_initialize.py` gives its HTTP worker stub a
  generation id, so the deadline test proves again that the read deadline re-arms on every
  read and that the normal worker timeout returns afterwards.
