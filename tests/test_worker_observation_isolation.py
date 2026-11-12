"""The worker observer's process-global state must not leak between tests."""

from types import SimpleNamespace

from conftest import _reset_worker_observer

from mempalace import mcp_server as server


def _latch_failure(monkeypatch):
    # A stub config without ``config_dir`` makes the observer constructor path
    # fail, which latches ``_worker_observer_failed`` for the whole process.
    monkeypatch.setattr(server, "_config", SimpleNamespace())
    assert server._activate_worker_observation() is None
    assert server._worker_observer_failed is True
    assert server._worker_observation_lost == 1


def test_reset_clears_the_latch_and_the_loss_counter(monkeypatch):
    # Order independent: latch, reset, observe the state in one test.
    _latch_failure(monkeypatch)
    _reset_worker_observer(server)
    assert server._worker_observer is None
    assert server._worker_observer_failed is False
    assert server._worker_observation_lost == 0


def test_latching_test_leaves_state_for_the_reset(monkeypatch):
    # First half of an end-to-end pair: it latches and exits without cleaning
    # up. The suite-wide autouse reset must clear it before the next test.
    _latch_failure(monkeypatch)


def test_state_starts_clean_after_a_latching_test():
    # Second half of the pair; it only proves the autouse reset when it runs
    # after the test above (file order), the direct test covers the helper.
    assert server._worker_observer is None
    assert server._worker_observer_failed is False
    assert server._worker_observation_lost == 0
