- **Tests:** `tests/test_commonplace_session_initialize.py` builds its HTTP worker stub
  with a generation id, as the supervised worker always has one, so the deadline test
  again proves that the read deadline re-arms on every read and that the normal worker
  timeout comes back afterwards.
