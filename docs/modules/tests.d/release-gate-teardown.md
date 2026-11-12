- **Tests:**

  - `tests/test_commonplace_release_gate.py::test_throwaway_instance_closes_supervisor_and_handoff_on_exit` checks that leaving `throwaway_instance()` permanently closes the supervisor and the handoff service; with a plain `stop_all` teardown a handoff job could restart real memory workers after the test.
