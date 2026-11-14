- **Tests:**

  - `tests/test_commonplace_web.py::test_connections_wings_deadline` and `::test_connections_wings_deadline_slow_worker` assert a 1.5 s wall-clock budget, so they carry the `timing` marker: the default selection leaves them out, the serial timing lane runs them, and a load spike never blocks a merge.
