- **Tests:**

  - `tests/test_db_worker_shutdown.py` closes the control database with a live answer-hold worker and checks the thread is dead and no `answer_holds` SQL runs afterwards, and runs `Gateway.serve` with live hold and chat front workers to check both threads are dead by the time `server_close` runs.
