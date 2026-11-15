"""Per-workspace state in the gateway stays bounded, and a worker recycles above a ceiling.

The growth tests drive many workspaces (and many intervals) through each table the gateway
keeps and assert what is held at the end, not that something was freed along the way.
"""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import commonplace.gateway as gateway_module
import commonplace.workers as workers
from commonplace import plans
from commonplace.gateway import Gateway, Mediator
from commonplace.policy import Policy
from commonplace.workers import SubprocessLauncher, Worker, WorkerSupervisor
from mempalace import worker_memory
from mempalace.worker_lifecycle import ControlPipes, Coordinator, RestartCircuit

WORKSPACES = 1000
GIB = 1 << 30


# ── growth: every table that scales with workspaces ──────────────────────────


def test_tools_list_cache_stays_bounded_across_workspaces():
    tools = [{"name": f"tool_{i}", "description": "x" * 400, "inputSchema": {}} for i in range(60)]
    reply = {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}
    mediator = Mediator(None, SimpleNamespace(post=lambda principal, request: reply))
    policy = Policy.build(
        profile="standard", scopes=["memory:read"], read_wings=["shared"], trust="standard"
    )
    for index in range(WORKSPACES):
        principal = SimpleNamespace(workspace_id=f"ws_{index:04d}", policy=policy, person=False)
        try:
            mediator._tools_list(principal, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        except Exception:
            pass  # what follows the cache write needs a whole gateway; the cache is the subject
    assert len(mediator._tools_cache) <= 256


def test_tools_list_cache_still_serves_a_paused_workspace(monkeypatch):
    monkeypatch.setattr(gateway_module.extensions, "names", lambda: [])
    tools = [{"name": "mempalace_search", "inputSchema": {}}]

    def post(principal, request):
        raise gateway_module.WorkerPaused(30)

    mediator = Mediator(None, SimpleNamespace(post=post))
    policy = Policy.build(
        profile="standard", scopes=["memory:read"], read_wings=["shared"], trust="standard"
    )
    principal = SimpleNamespace(workspace_id="ws_a", policy=policy, person=True)
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with pytest.raises(gateway_module.WorkerPaused):
        mediator._tools_list(principal, request)  # nothing remembered yet: the pause is shown
    mediator._tools_cache["ws_a"] = tools
    assert mediator._tools_cache.get("ws_a") == tools
    reply = mediator._tools_list(principal, request)
    assert [tool["name"] for tool in reply["result"]["tools"]] == ["mempalace_search"]


def test_warm_up_table_keeps_only_the_last_interval(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(gateway_module, "monotonic", lambda: now[0])
    started = []
    fake = SimpleNamespace(
        _warm_lock=threading.Lock(),
        _warmed={},
        supervisor=SimpleNamespace(get=started.append),
    )
    for index in range(WORKSPACES):
        now[0] += 1.0  # a page open each second, each for a new workspace
        Gateway.warm_memory(fake, f"ws_{index:04d}")
    assert len(fake._warmed) <= 300
    # The throttle itself is unchanged: the same workspace within the interval asks once.
    before = len(started)
    Gateway.warm_memory(fake, f"ws_{WORKSPACES - 1:04d}")
    assert len(started) == before


def test_restart_circuits_go_with_their_idle_workers(tmp_path):
    class Launcher:
        def start(self, workspace_id, workspace_dir):
            return Worker(
                workspace_id=workspace_id, host="127.0.0.1", port=1, token="t", stop=lambda: None
            )

    supervisor = WorkerSupervisor(str(tmp_path), launcher=Launcher(), idle_stop_s=0)
    for index in range(WORKSPACES):
        supervisor.get(f"ws_{index:04d}")
    assert len(supervisor._circuits) == WORKSPACES
    assert len(supervisor.stop_idle()) == WORKSPACES
    assert len(supervisor._circuits) == 0


def test_a_failing_workspace_keeps_its_circuit_after_the_worker_goes(tmp_path):
    circuit = RestartCircuit(lambda: 0.0)
    assert circuit.clean()
    circuit.failed()
    assert not circuit.clean()
    for _ in range(2):
        circuit.failed()
    assert not circuit.available() and not circuit.clean()


def test_storage_meter_forgets_totals_past_their_minute(tmp_path):
    now = [0.0]
    meter = plans.StorageMeter(clock=lambda: now[0])
    for index in range(WORKSPACES):
        now[0] += 61.0
        meter.used(f"ws_{index:04d}", tmp_path)
        meter.audit_limit(
            Mock(),
            SimpleNamespace(
                workspace_id=f"ws_{index:04d}", user_id="u", connection_id="c", auth="pat"
            ),
        )
    assert len(meter._cache) <= 2 and len(meter._denied) <= 2


# ── idle stores give back their page cache ───────────────────────────────────


def test_idle_thread_stores_release_their_page_cache(tmp_path, monkeypatch):
    from commonplace.handoff.wiring import HandoffContext

    ctx = HandoffContext(gateway=None, control_db=None, data_dir=tmp_path)
    busy, quiet = ctx.store_for("ws_busy"), ctx.store_for("ws_quiet")
    statements = []
    for name, store in (("busy", busy), ("quiet", quiet)):
        real = store._conn
        store._conn = SimpleNamespace(
            execute=lambda sql, name=name, real=real: (
                statements.append((name, sql)) or real.execute(sql)
            ),
            close=real.close,
        )
    now = [1000.0]
    from commonplace.handoff import wiring

    wiring_time = wiring.time
    wiring.time = SimpleNamespace(monotonic=lambda: now[0])
    try:
        ctx._used["ws_busy"] = now[0]
        ctx._used["ws_quiet"] = now[0] - 700
        trims = []
        monkeypatch.setattr(worker_memory, "trim_heap", lambda: trims.append(True))
        assert ctx.release_idle_memory(idle_s=600) == ["ws_quiet"]
        assert trims == [True]
        assert statements == [("quiet", "PRAGMA shrink_memory")]
        # Released once: not again until it has been idle a whole interval more.
        assert ctx.release_idle_memory(idle_s=600) == []
        # A store a call holds is skipped, never waited for.
        ctx._used["ws_quiet"] = now[0] - 700
        with quiet._lock:
            held = []
            thread = threading.Thread(target=lambda: held.append(ctx.release_idle_memory(600)))
            thread.start()
            thread.join(5)
        assert held == [[]]
    finally:
        wiring.time = wiring_time
        ctx.close()


# ── the RSS recycle ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, "", "  ", "0", "abc", "-5", "1.5", "100", "511"])
def test_recycle_ceiling_is_off_unless_it_is_a_sane_number(raw):
    environ = {} if raw is None else {worker_memory.RECYCLE_ENV: raw}
    assert worker_memory.recycle_limit_bytes(environ) == 0


def test_recycle_ceiling_reads_mebibytes():
    assert worker_memory.recycle_limit_bytes({worker_memory.RECYCLE_ENV: " 2048 "}) == 2 * GIB
    assert worker_memory.recycle_limit_bytes({worker_memory.RECYCLE_ENV: "512"}) == 512 << 20


class Fixture:
    """A worker coordinator, a controllable clock and a controllable memory reading."""

    def __init__(self, *, anon=2 * GIB, limit=GIB, admitted=5, active=0, uptime=700.0):
        self.facts, self.exited = [], []
        self.coordinator = Coordinator(
            fact=self.facts.append, cleanup=lambda: None, exit=lambda: self.exited.append(True)
        )
        for _ in range(admitted):
            self.coordinator.admit()
        for _ in range(active):
            self.coordinator.acquire()
        self.now = [0.0]
        self.sample = {"rss_bytes": anon + (100 << 20), "anon_bytes": anon, "file_bytes": 100 << 20}
        self.watchdog = worker_memory.RssWatchdog(
            self.coordinator,
            limit,
            read=lambda: self.sample,
            clock=lambda: self.now[0],
        )
        self.now[0] = uptime

    def retiring(self):
        return [f for f in self.facts if f["kind"] == "retiring"]


def test_worker_over_its_ceiling_drains_and_exits_with_the_rss_reason():
    fixture = Fixture()
    assert fixture.watchdog.check() is True
    (fact,) = fixture.retiring()
    assert fact["reason"] == "rss" and fact["rss_source"] == "proc" and fact["admitted"] == 5
    assert fixture.exited == [True]
    # New calls are refused (the gateway answers them from the replacement); none was cut.
    assert fixture.coordinator.acquire() is False


def test_the_parent_accepts_the_fact_and_does_not_count_it_a_failure():
    fixture = Fixture()
    fixture.watchdog.check()
    pipes = ControlPipes(generation=fixture.coordinator.generation)
    try:
        assert pipes.accept(fixture.retiring()[0]) is True
        circuit = RestartCircuit(lambda: 0.0)
        circuit.ended(pipes.terminal, pipes.completed)
        assert circuit.failures == 0 and circuit.available()
    finally:
        pipes.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"anon": GIB - 1},  # under the ceiling
        {"uptime": 599.0},  # younger than the minimum uptime
        {"admitted": 1},  # the restart circuit would count this a failed start
        {"anon": GIB + 1, "active": 1},  # over, but a call is running and it is not far over
    ],
)
def test_worker_is_not_recycled_when_it_should_not_be(kwargs):
    fixture = Fixture(**kwargs)
    assert fixture.watchdog.check() is False
    assert fixture.retiring() == [] and fixture.coordinator.acquire() is True


def test_far_over_the_ceiling_drains_even_with_a_call_running():
    fixture = Fixture(anon=int(GIB * 1.3), active=1)
    assert fixture.watchdog.check() is True
    assert fixture.exited == [], "the running call finishes first"
    fixture.coordinator.release(storage=True, completed=True)
    assert fixture.exited == [True]


def test_only_anonymous_memory_counts_and_an_unreadable_sample_is_ignored():
    mapped = Fixture()
    mapped.sample = {"rss_bytes": 8 * GIB, "anon_bytes": GIB // 2, "file_bytes": 7 * GIB}
    assert mapped.watchdog.check() is False
    unreadable = Fixture()
    unreadable.sample = None
    assert unreadable.watchdog.check() is False
    unsplit = Fixture()
    unsplit.sample = {"rss_bytes": 2 * GIB}
    assert unsplit.watchdog.check() is True


def test_recycle_is_not_started_without_a_ceiling():
    coordinator = Coordinator()
    assert worker_memory.start_rss_recycle(coordinator, {}) is None
    assert worker_memory.start_rss_recycle(None, {worker_memory.RECYCLE_ENV: "2048"}) is None
    thread = worker_memory.start_rss_recycle(coordinator, {worker_memory.RECYCLE_ENV: "2048"})
    assert thread is not None and thread.daemon and thread.name == "mcp-rss-recycle"


def test_the_worker_loop_starts_the_recycle_with_its_coordinator(monkeypatch):
    from mempalace import mcp_server as mcp

    started = []
    monkeypatch.setenv("MEMPALACE_HTTP_CHILD", "1")
    coordinator = Coordinator()
    monkeypatch.setattr(mcp, "_http_coordinator", None, raising=False)
    monkeypatch.setattr(
        "mempalace.worker_lifecycle.child_coordinator", lambda **kwargs: coordinator
    )
    monkeypatch.setattr(mcp, "_READ_ONLY", True)
    monkeypatch.setattr(mcp, "_activate_worker_observation", lambda: None)
    monkeypatch.setattr(mcp, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp, "_start_idle_exit_watchdog", lambda: None)
    monkeypatch.setattr(mcp, "_start_write_stall_watchdog", lambda: None)
    monkeypatch.setattr(mcp, "_serve_http", lambda host, port: None)
    monkeypatch.setattr(
        "mempalace.worker_memory.start_rss_recycle", lambda c, *a: started.append(c)
    )
    mcp._run_http_loop()
    assert started == [coordinator]


def _launch(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "_share_model_cache", lambda home: None)
    monkeypatch.setattr(workers, "_free_loopback_port", lambda: 12345)
    monkeypatch.setattr(workers, "_proc_starttime", lambda pid: None)
    monkeypatch.setattr(workers, "wait_until_ready", lambda worker, timeout: None)
    spawn = Mock(return_value=Mock(pid=123, poll=Mock(return_value=None)))
    monkeypatch.setattr(workers.subprocess, "Popen", spawn)
    SubprocessLauncher().start("ws_test", tmp_path / "workspace")
    return spawn.call_args.kwargs["env"]


def test_the_launcher_passes_the_ceiling_only_from_the_gateway_switch(tmp_path, monkeypatch):
    monkeypatch.delenv(workers.RSS_RECYCLE_SWITCH, raising=False)
    # An operator variable of the worker's own name never reaches it.
    monkeypatch.setenv(worker_memory.RECYCLE_ENV, "4096")
    assert worker_memory.RECYCLE_ENV not in _launch(tmp_path, monkeypatch)
    monkeypatch.setenv(workers.RSS_RECYCLE_SWITCH, "6144")
    assert _launch(tmp_path, monkeypatch)[worker_memory.RECYCLE_ENV] == "6144"


# ── the readings ─────────────────────────────────────────────────────────────


def test_process_memory_reads_its_own_split():
    memory = worker_memory.read_process_memory()
    assert memory["rss_bytes"] > 0 and memory["anon_bytes"] > 0 and memory["file_bytes"] >= 0
    assert worker_memory.read_process_memory(2**31) is None


def test_cgroup_memory_splits_anonymous_from_file(tmp_path):
    (tmp_path / "memory.stat").write_text(
        "anon 1000\nfile 5000\nshmem 40\ninactive_file 3000\nactive_file 2000\nslab 9\n"
    )
    (tmp_path / "memory.current").write_text("6500\n")
    (tmp_path / "memory.max").write_text("max\n")
    (tmp_path / "memory.events").write_text("low 0\nhigh 0\nmax 2\noom 1\noom_kill 1\n")
    assert worker_memory.read_cgroup_memory(str(tmp_path)) == {
        "current_bytes": 6500,
        "limit_bytes": None,
        "anon_bytes": 1000,
        "file_bytes": 5000,
        "shmem_bytes": 40,
        "inactive_file_bytes": 3000,
        "working_set_bytes": 3500,
        "oom_kill": 1,
    }
    (tmp_path / "memory.max").write_text("34359738368\n")
    assert worker_memory.read_cgroup_memory(str(tmp_path))["limit_bytes"] == 32 * GIB


def test_cgroup_memory_is_none_without_cgroup_files(tmp_path):
    assert worker_memory.read_cgroup_memory(str(tmp_path / "missing")) is None
    (tmp_path / "memory.stat").write_text("garbage\n")
    assert worker_memory.read_cgroup_memory(str(tmp_path))["anon_bytes"] is None


def test_supervisor_snapshot_lists_workers_with_uptime_and_memory(tmp_path, monkeypatch):
    import os

    now = [100.0]
    monkeypatch.setattr(workers.time, "monotonic", lambda: now[0])

    class Launcher:
        def start(self, workspace_id, workspace_dir):
            return Worker(
                workspace_id=workspace_id,
                host="127.0.0.1",
                port=1,
                token="t",
                stop=lambda: None,
                pid=os.getpid(),
                started=100.0,
            )

    supervisor = WorkerSupervisor(str(tmp_path), launcher=Launcher())
    supervisor.get("ws_a")
    now[0] = 190.0
    (row,) = supervisor.worker_snapshot()
    assert row["workspace_id"] == "ws_a" and row["pid"] == os.getpid()
    assert row["uptime_s"] == 90 and row["rss_bytes"] > 0 and row["anon_bytes"] > 0
    assert row["retiring"] is False
    # A worker that cannot be read still lists, with unknown memory; a broken one never raises.
    monkeypatch.setattr(worker_memory, "read_process_memory", lambda pid="self": None)
    assert supervisor.worker_snapshot()[0]["rss_bytes"] is None
    monkeypatch.setattr(supervisor, "_lock", None)
    assert supervisor.worker_snapshot() == []
