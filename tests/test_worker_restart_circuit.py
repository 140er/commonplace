"""Restart failures belong to the persistent supervisor, not a child."""

from commonplace.workers import WorkerSupervisor


def test_three_failed_startups_latch_in_supervisor(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("commonplace.workers.time.monotonic", lambda: now[0])

    class FailingLauncher:
        calls = 0

        def start(self, *args):
            self.calls += 1
            raise RuntimeError("fixture startup failure")

    launcher = FailingLauncher()
    supervisor = WorkerSupervisor(str(tmp_path), launcher=launcher)
    outcomes = []
    for _ in range(4):
        try:
            outcomes.append(supervisor.get("ws_fixture"))
        except Exception as exc:
            outcomes.append(exc)
    assert launcher.calls == 3, "fourth request must remain latched without launching"
    now[0] = 60.0
    try:
        supervisor.get("ws_fixture")
    except RuntimeError:
        pass
    assert launcher.calls == 4
    assert len(outcomes) == 4
