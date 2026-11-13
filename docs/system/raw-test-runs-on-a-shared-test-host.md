### Raw test runs on a shared test host

- A host that caps concurrent test runs (its test runner holds a slot per run) opts in with `~/.config/commonplace/require-test-runner`, naming the variable the runner sets; there `tests/conftest.py` stops a pytest run without it, so agents cannot overload the host by bypassing the cap. Machines without the marker run unchanged; `COMMONPLACE_ALLOW_RAW_PYTEST=1` is the one-run override.
