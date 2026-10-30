"""Manual starts use real stores and launch claims, with no models or T3 server."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from commonplace import cli
from commonplace.control_db import ControlDB, Principal
from commonplace.handoff.db import HandoffDB
from commonplace.handoff.events import Events
from commonplace.handoff.manual import ManualHandoff, ManualHandoffError
from commonplace.handoff.models import LauncherConfig, SettingRow, SuccessorRef
from commonplace.handoff.service import HandoffService
from commonplace.handoff.settings import SettingsService
from commonplace.handoff.t3 import SourceThread, T3Error, Target
from commonplace.handoff.thread_store import ThreadStore
from commonplace.policy import Policy


@pytest.fixture
def manual(tmp_path, monkeypatch):
    for name in ("COMMONPLACE_HANDOFF", "COMMONPLACE_AGENT", "COMMONPLACE_TASK"):
        monkeypatch.delenv(name, raising=False)
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    user = db.create_user("owner@example.test", is_admin=True)
    workspace = db.create_workspace("home", user)
    principal = Principal(
        user,
        "owner@example.test",
        workspace,
        "conn_one",
        "builder",
        "t3",
        "local",
        "owner",
        Policy.build(profile="full", scopes=["handoff:write", "threads:read"], trust="trusted"),
    )
    records = HandoffDB(db)
    events = Events(db, records)
    settings = SettingsService(records, events)
    settings.set(SettingRow(workspace, "workspace", "", enabled=True), actor=user, is_owner=True)
    store = ThreadStore(str(tmp_path / "threads.sqlite3"))
    service = HandoffService(
        control_db=db,
        handoff_db=records,
        settings=settings,
        store_for=lambda _: store,
        events=events,
        prompt_dir=str(tmp_path / "prompts"),
    )
    path = tmp_path / "source.jsonl"
    path.write_text(
        json.dumps(
            [
                {"role": "user", "content": "Keep every word.\r\n"},
                {"role": "assistant", "content": "Ready to continue."},
            ]
        )
    )
    source = SourceThread(
        "mcp:source", str(tmp_path), "claudeAgent", "opus", "messages", (path,), "project"
    )

    class FakeT3:
        def __init__(self):
            self.starts = []
            self.settled = []
            self.fail_start = False
            self.fail_settle = False
            self.lock = threading.Lock()

        def resolve(self, thread_id):
            assert thread_id == source.thread_id
            return source

        def start(self, old, prompt, target):
            assert old == source
            with self.lock:
                self.starts.append((prompt, target))
            if self.fail_start:
                raise T3Error("failed to start")
            assert records.by_key(workspace, "thread:mcp:source").state == "launched"
            return SuccessorRef("t3:mcp:successor", thread_id="mcp:successor")

        def settle(self, thread_id):
            assert records.by_key(workspace, "thread:mcp:source").state == "done"
            if self.fail_settle:
                raise T3Error("settle failed")
            self.settled.append(thread_id)

    t3 = FakeT3()
    starter = ManualHandoff(service, t3=t3)
    state = SimpleNamespace(
        db=db,
        records=records,
        service=service,
        principal=principal,
        store=store,
        settings=settings,
        t3=t3,
        starter=starter,
        source=source,
    )
    state.start = lambda **kw: starter.start(
        principal, thread_id=source.thread_id, to="codex/gpt-6.1-sol", **kw
    )
    yield state
    service.wait_for_mirrors()
    store.close()
    db.close()


def test_parser_required_and_optional_arguments():
    args = cli.build_parser().parse_args(
        [
            "handoff",
            "start",
            "--thread",
            "mcp:old",
            "--to",
            "codex/gpt-6.1-sol",
            "--effort",
            "max",
            "--note",
            "  exact note\n",
            "--workspace",
            "home",
            "--dry-run",
        ]
    )
    assert args.func is cli.cmd_handoff_start
    assert (args.thread, args.to, args.effort) == ("mcp:old", "codex/gpt-6.1-sol", "max")
    assert args.note == "  exact note\n" and args.workspace == "home" and args.dry_run
    defaults = cli.build_parser().parse_args(
        [
            "handoff",
            "start",
            "--thread",
            "old",
            "--to",
            "claude-code/claude-opus-5-5",
        ]
    )
    assert defaults.workspace is None and defaults.effort == defaults.note == ""
    for missing in (["handoff", "start"], ["handoff", "start", "--thread", "old"]):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(missing)


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("claude", "claudeAgent"),
        ("claude-code", "claudeAgent"),
        ("claudeAgent", "claudeAgent"),
        ("codex", "codex"),
        ("local-codex", "local-codex"),
    ],
)
def test_target_maps_provider_aliases_and_preserves_model(provider, expected):
    assert Target.parse(f"{provider}/model-slug", "max") == Target(expected, "model-slug", "max")


@pytest.mark.parametrize(
    "value", ["", "codex", "/sol", "codex/", "-p/model", "codex/a/b", "codex/a\n"]
)
def test_target_rejects_bad_values(value):
    with pytest.raises(T3Error, match="--to"):
        Target.parse(value)


def test_double_call_saves_once_and_returns_existing_successor(manual):
    first = manual.start(note="  verbatim note\r\n", effort="max")
    second = manual.start(effort="low")
    assert second == first
    assert first["state"] == "done" and first["source_settled"]
    assert first["successor"]["agent_ref"] == "t3:mcp:successor"
    assert len(manual.t3.starts) == 1
    prompt, target = manual.t3.starts[0]
    assert target == Target("codex", "gpt-6.1-sol", "max")
    assert "  verbatim note\r\n" in prompt
    assert f"commonplace-handoff-id: {first['handoff_id']}" in prompt
    assert (manual.starter.prompt_dir / f"{first['handoff_id']}.md").read_bytes() == prompt.encode()
    messages = manual.store.messages(first["stored_thread_id"])
    assert messages[0].text == "Keep every word.\r\n"
    assert len(manual.records.list(manual.principal.workspace_id)) == 1


def test_concurrent_independent_services_claim_only_one_launch(manual):
    # Independent control connections, just as separate CLI/gateway processes.
    other_db = ControlDB(manual.db.path)
    other_records = HandoffDB(other_db)
    other = HandoffService(
        control_db=other_db,
        handoff_db=other_records,
        settings=SettingsService(other_records),
        store_for=lambda _: manual.store,
        events=Events(other_db, other_records),
        prompt_dir=str(manual.starter.prompt_dir),
    )
    start = ManualHandoff(other, t3=manual.t3)
    gate = threading.Barrier(2)

    def call(starter):
        gate.wait(timeout=5)
        return starter.start(manual.principal, thread_id="mcp:source", to="codex/gpt-6.1-sol")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, (manual.starter, start)))
        assert len(manual.t3.starts) == 1
        assert len({result["handoff_id"] for result in results}) == 1
        assert manual.start()["successor"]["thread_id"] == "mcp:successor"
    finally:
        other_db.close()


def test_completed_retry_does_not_need_the_old_transcript_or_session(manual, monkeypatch):
    first = manual.start()
    manual.source.transcript_paths[0].unlink()

    def unavailable(*args):
        raise AssertionError("A completed handoff must not resolve the old session again")

    monkeypatch.setattr(manual.t3, "resolve", unavailable)
    assert manual.start() == first
    assert len(manual.t3.starts) == 1


def test_completed_retry_replays_a_failed_completion_delivery(manual, monkeypatch):
    deliver = manual.service.events.deliver_pending
    calls = 0

    def fail_completion(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("event delivery failed after successor registration")
        return deliver(**kwargs)

    monkeypatch.setattr(manual.service.events, "deliver_pending", fail_completion)
    with pytest.raises(ManualHandoffError, match="registration failed"):
        manual.start()
    record = manual.records.list(manual.principal.workspace_id)[0]
    assert record.state == "done" and manual.records.pending_events(handoff_id=record.id)
    assert manual.start()["source_settled"]
    assert manual.records.pending_events(handoff_id=record.id) == []
    assert len(manual.t3.starts) == 1


def test_store_failure_never_launches_or_settles(manual, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk failed")

    monkeypatch.setattr(manual.store, "store", fail)
    with pytest.raises(ManualHandoffError, match="storage failed"):
        manual.start()
    assert not manual.t3.starts and not manual.t3.settled
    assert manual.records.list(manual.principal.workspace_id)[0].state == "failed"


def test_launch_failure_is_recorded_and_source_keeps_running(manual):
    manual.t3.fail_start = True
    for _ in range(2):
        with pytest.raises(ManualHandoffError, match="launch failed; source is still running"):
            manual.start()
    assert len(manual.t3.starts) == 1 and not manual.t3.settled
    record = manual.records.list(manual.principal.workspace_id)[0]
    assert record.state == "launch_failed" and record.stored_thread_id
    assert record.successor is None


def test_registration_retry_recovers_confirmed_start_without_relaunch(manual, monkeypatch):
    register = manual.service.register_successor
    monkeypatch.setattr(
        manual.service, "register_successor", lambda *args: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(ManualHandoffError, match="started but registration failed"):
        manual.start()
    assert not manual.t3.settled
    assert manual.records.list(manual.principal.workspace_id)[0].state == "launched"
    monkeypatch.setattr(manual.service, "register_successor", register)
    assert manual.start()["source_settled"]
    assert len(manual.t3.starts) == 1


def test_settle_failure_can_retry_without_relaunch(manual):
    manual.t3.fail_settle = True
    assert manual.start()["source_settled"] is False
    manual.t3.fail_settle = False
    assert manual.start()["source_settled"] is True
    assert len(manual.t3.starts) == 1


def test_manual_selection_does_not_run_configured_automatic_launcher(manual, monkeypatch):
    manual.settings.set(
        SettingRow(
            manual.principal.workspace_id,
            "workspace",
            "",
            enabled=True,
            launcher=LauncherConfig("command", ("must-not-run",)),
        ),
        actor=manual.principal.user_id,
        is_owner=True,
    )
    assert manual.start()["state"] == "done"
    assert manual.settings.effective(manual.principal.workspace_id).launcher.kind == "command"


def test_dry_run_has_no_handoff_storage_prompt_or_launch(manual):
    result = manual.start(dry_run=True)
    assert result["dry_run"] and result["target"]["provider"] == "codex"
    assert manual.records.list(manual.principal.workspace_id) == []
    assert not manual.starter.prompt_dir.exists()
    assert not manual.t3.starts and not manual.t3.settled


@pytest.mark.parametrize(
    "scope,ref,enabled,kwargs,allowed",
    [
        ("workspace", "", False, {}, False),
        ("agent", "builder", False, {}, False),
        ("task", "#42", False, {"task": "42"}, False),
        ("task", "#42", True, {"task": "42"}, True),
    ],
)
def test_scope_overrides_apply(manual, scope, ref, enabled, kwargs, allowed):
    manual.settings.set(
        SettingRow(manual.principal.workspace_id, scope, ref, enabled=enabled),
        actor=manual.principal.user_id,
        is_owner=True,
    )
    if allowed:
        assert manual.start(**kwargs)["state"] == "done"
    else:
        with pytest.raises(ManualHandoffError, match="handoff is off"):
            manual.start(**kwargs)
        assert not manual.t3.starts and not manual.t3.settled
        assert manual.records.list(manual.principal.workspace_id) == []


def test_run_off_override_refuses_even_with_enabled_workspace(manual, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_HANDOFF", "off")
    with pytest.raises(ManualHandoffError, match="off for this run"):
        manual.start()
    assert not manual.t3.starts


def test_other_connection_cannot_take_over_existing_handoff(manual):
    manual.start()
    stranger = replace(manual.principal, connection_id="other", connection="other", role="member")
    with pytest.raises(ManualHandoffError, match="in progress"):
        manual.starter.start(stranger, thread_id="mcp:source", to="codex/gpt-6.1-sol")
    assert len(manual.t3.starts) == 1
