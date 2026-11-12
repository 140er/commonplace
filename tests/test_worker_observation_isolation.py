"""The worker observer's process-global state must not leak between tests."""

from types import SimpleNamespace

from mempalace import mcp_server as server


def test_stub_config_latches_observer_failure_inside_the_test(monkeypatch):
    # A stub config without ``config_dir`` makes the observer constructor path
    # fail, which latches ``_worker_observer_failed`` for the whole process.
    monkeypatch.setattr(server, "_config", SimpleNamespace())
    assert server._activate_worker_observation() is None
    assert server._worker_observer_failed is True
    assert server._worker_observation_lost == 1


def test_observer_state_starts_clean_after_a_latching_test():
    assert server._worker_observer is None
    assert server._worker_observer_failed is False
    assert server._worker_observation_lost == 0
