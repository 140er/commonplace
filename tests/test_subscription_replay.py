"""Real git snapshots/tests, fake subscription CLI binaries. Never calls a model."""

import copy
import json
import os
import subprocess
import sys
from dataclasses import replace

import pytest

from commonplace.orch.benchmark import Benchmarks, TaskResult, Thresholds, decide
from commonplace.orch.replay import Replays, specification
from commonplace.orch.store import OrchError, Store
from commonplace.runner.replay_cli import SubscriptionCLI, child_environment
from tests.test_orch_store import BOARD, CONNECTIONS, MEMBER, OWNER, accept_text, config_text


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    control, log = tmp_path / "control.json", tmp_path / "calls.jsonl"
    control.write_text("{}")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    script = f"""#!{sys.executable}
import json, os, pathlib, sys
control = pathlib.Path({str(control)!r})
log = pathlib.Path({str(log)!r})
options = json.loads(control.read_text())
cli = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
status = ('auth' in args or 'login' in args or 'usage' in args)
entry = {{'cli': cli, 'status': status, 'argv': args, 'environment_keys': sorted(os.environ)}}
if status:
    mode = options.get('auth', 'subscription')
    if cli == 'claude':
        print(json.dumps({{'loggedIn': mode != 'signed_out', 'authMethod': 'api_key' if mode == 'api' else 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'max'}}))
    elif cli == 'codex':
        print('Logged in using ChatGPT' if mode == 'subscription' else 'Logged in using an API key', file=sys.stderr)
    else:
        print(json.dumps(options.get('status_payload', {{'plan': {{'subscription': mode == 'subscription'}}}})))
    with log.open('a') as f: f.write(json.dumps(entry) + '\\n')
    sys.exit(0)
data = json.loads(sys.stdin.read())
entry['task'] = data.get('task')
entry['instructions'] = data.get('instructions')
with log.open('a') as f: f.write(json.dumps(entry) + '\\n')
calls = sum(not json.loads(line)['status'] for line in log.read_text().splitlines())
if options.get('limit_at') == calls:
    print(json.dumps({{'is_error': True, 'error': 'usage_limit reached; private diagnostic never retained'}}))
    sys.exit(1)
files = {{k: v for k, v in data.get('files', {{}}).items() if k in data.get('editable_files', [])}}
if 'repair' in data.get('instructions', ''):
    target = int(data['task'].split()[-1])
    files = {{k: 'def answer():\\n    return ' + str(target) + '\\n' for k in files}}
if options.get('escape'):
    files = {{'../outside': 'forbidden'}}
answer = json.dumps({{'files': files}})
usage = {{'input_tokens': 10, 'output_tokens': 4, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 2}}
if cli == 'grok': usage = {{'total_tokens': 16}}
if options.get('unknown'): usage = {{}}
if cli == 'codex':
    print(json.dumps({{'type': 'item.completed', 'item': {{'type': 'agent_message', 'text': answer}}}}))
    print(json.dumps({{'type': 'turn.completed', 'usage': usage}}))
else:
    print(json.dumps({{'type': 'result', 'result' if cli == 'claude' else 'text': answer, 'usage': usage}}))
"""
    for name in ("claude", "codex", "grok"):
        path = binaries / name
        path.write_text(script)
        path.chmod(0o700)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME"):
        monkeypatch.setenv(key, str(tmp_path / "fake-login"))

    class Fake:
        def options(self, **values):
            control.write_text(json.dumps(values))

        def calls(self, *, models=False):
            entries = (
                [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            )
            return [e for e in entries if not e["status"]] if models else entries

    return Fake()


@pytest.fixture
def historical_tasks(tmp_path):
    repo = tmp_path / "history"
    repo.mkdir()
    env = {**child_environment(), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "Replay fixture")
    git("config", "user.email", "replay@example.invalid")
    tasks = []
    for n in range(1, 6):
        (repo / "solution.py").write_text("def answer():\n    return 0\n")
        (repo / "test_answer.py").write_text(
            f"from solution import answer\nassert answer() == {n}\n"
        )
        git("add", ".")
        git("commit", "--quiet", "-m", f"fixture: past task {n}")
        tasks.append(
            {
                "id": f"past-{n}",
                "revision": git("rev-parse", "HEAD"),
                "request": f"Return {n}",
                "files": ["solution.py"],
                "test_files": ["test_answer.py"],
                "test_argv": [sys.executable, "test_answer.py"],
                "known_passed": False,
            }
        )
    return repo, {"cli": "claude", "role": "builder", "tasks": tasks}


@pytest.fixture
def experiment(tmp_path, historical_tasks):
    store = Store(tmp_path / "workspace")
    live = accept_text(store, config_text(prompt="Preserve the original files exactly.")).version
    candidate = accept_text(
        store, config_text(prompt="repair the defective implementation"), channel="exp:repair"
    ).version
    replays = Replays(store)
    repo, manifest = historical_tasks
    replay = replays.create(OWNER, BOARD, candidate.channel, manifest)
    return replays, replay, repo, manifest, live, candidate


@pytest.mark.parametrize("cli,tokens", [("claude", 16), ("codex", 14), ("grok", 16)])
def test_every_adapter_uses_subscription_json_and_strips_credentials(
    cli, tokens, fake_cli, monkeypatch
):
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "ANTHROPIC_BASE_URL",
        "CODEX_CONFIG",
        "GROK_CONFIG",
        "COMMONPLACE_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "AWS_ACCESS_KEY_ID",
        "HTTPS_PROXY",
    ):
        monkeypatch.setenv(key, "synthetic-value-must-not-reach-the-child")
    reply = SubscriptionCLI(cli).run(
        json.dumps({"files": {"solution.py": "pass"}, "editable_files": ["solution.py"]})
    )
    assert reply.state == "complete" and reply.tokens == tokens and reply.tokens_reason is None
    calls = fake_cli.calls()
    assert len(calls) == 2 and calls[0]["status"] and not calls[1]["status"]
    assert all(
        not any(
            "API_KEY" in k
            or k in ("GROK_CONFIG", "CODEX_CONFIG", "HTTPS_PROXY", "COMMONPLACE_TOKEN")
            for k in c["environment_keys"]
        )
        for c in calls
    )
    assert all(
        not any(arg == "--api" or arg.startswith("--base-url") for arg in c["argv"]) for c in calls
    )


@pytest.mark.parametrize("cli", ["claude", "codex", "grok"])
@pytest.mark.parametrize("auth", ["api", "signed_out"])
def test_paid_or_unsigned_auth_never_starts_a_model(cli, auth, fake_cli):
    fake_cli.options(auth=auth)
    reply = SubscriptionCLI(cli).run("unused")
    assert reply.state == "refused" and reply.reason == "subscription_not_verified"
    assert not fake_cli.calls(models=True)


@pytest.mark.parametrize(
    "payload",
    [
        {"usage": {"tokens": 5}},
        {"plan": {}},
        {"plan": {"subscription": False}},
        {"plan": {"subscription": 1}},
        {"plan": {"subscription": True}, "error": "denied"},
    ],
)
def test_grok_requires_explicit_positive_subscription_proof(payload, fake_cli):
    fake_cli.options(status_payload=payload)
    reply = SubscriptionCLI("grok").run("unused")
    assert reply.state == "refused" and reply.reason == "subscription_not_verified"
    assert not fake_cli.calls(models=True)


@pytest.mark.parametrize("cli", ["claude", "codex", "grok"])
def test_cli_plan_limit_is_paused_without_retry_or_fallback(cli, fake_cli):
    fake_cli.options(limit_at=1)
    reply = SubscriptionCLI(cli).run("{}")
    assert reply.state == "paused" and reply.reason == "plan_limit"
    assert len(fake_cli.calls(models=True)) == 1
    assert {c["cli"] for c in fake_cli.calls()} == {cli}
    assert "private diagnostic" not in repr(reply)


@pytest.mark.parametrize("cli", ["claude", "codex", "grok"])
def test_unreported_tokens_are_unknown_not_zero(cli, fake_cli):
    fake_cli.options(unknown=True)
    reply = SubscriptionCLI(cli).run(
        json.dumps({"files": {"solution.py": "pass"}, "editable_files": ["solution.py"]})
    )
    assert reply.state == "complete" and reply.tokens is None
    assert reply.tokens_reason == "CLI did not report complete token usage"


@pytest.mark.parametrize(
    "value", ["--api", "--base-url=https://example.invalid", "x;unexpected", "api/model"]
)
def test_api_flags_cannot_be_injected_as_models(value, fake_cli):
    with pytest.raises(ValueError, match="API flags"):
        SubscriptionCLI("claude", model=value)
    assert not fake_cli.calls()


def test_unknown_metrics_do_not_create_a_fake_gain_or_evade_a_budget():
    live = [TaskResult(str(n), True, None, 100, 100) for n in range(5)]
    candidate = [replace(r, tokens=None) for r in live]
    decision = decide(live, candidate)
    assert not decision.promote and decision.reasons == ("no_strict_gain",)
    assert decision.summary["candidate"]["tokens"] is None
    assert "cost_unknown" in decide(live, candidate, Thresholds(budget_micro_usd=100)).reasons


def test_five_real_test_replays_promote_and_rollback(experiment, fake_cli):
    replays, replay, repo, manifest, live, candidate = experiment
    report = replays.run(OWNER, BOARD, replay, repo)
    assert report["state"] == "complete" and report["promote"]
    assert report["summary"]["tasks"] == 5
    assert report["summary"]["live"]["passed"] == 0
    assert report["summary"]["candidate"]["passed"] == 5
    assert report["regressions"] == []
    assert len(report["attempts"]) == len(fake_cli.calls(models=True)) == 10
    assert [a["side"] for a in report["attempts"]] == ["live", "candidate"] * 5
    assert all(a["test_exit"] == (0 if a["side"] == "candidate" else 1) for a in report["attempts"])
    assert (repo / "solution.py").read_text() == "def answer():\n    return 0\n"
    assert replays.store.accepted(BOARD).version == live.version
    promoted = replays.promote(
        OWNER, BOARD, replay, sha256=candidate.sha256, connections=CONNECTIONS
    )
    assert promoted.accept.version.body == candidate.body
    undone = replays.benchmarks.rollback(OWNER, BOARD, connections=CONNECTIONS)
    assert undone.accept.version.body == live.body and undone.accept.version.sha256 == live.sha256
    assert replays.run(OWNER, BOARD, replay, repo)["benchmark_id"] == report["benchmark_id"]
    assert len(fake_cli.calls(models=True)) == 10


def test_plan_pause_survives_reopen_and_resumes_only_unfinished_sides(experiment, fake_cli):
    replays, replay, repo, _manifest, _live, candidate = experiment
    fake_cli.options(limit_at=4)
    report = replays.run(OWNER, BOARD, replay, repo)
    assert report["state"] == "paused: plan limit" and report["benchmark_id"] is None
    assert len(fake_cli.calls(models=True)) == 4
    with pytest.raises(OrchError, match="replay_incomplete"):
        replays.promote(OWNER, BOARD, replay, sha256=candidate.sha256, connections=CONNECTIONS)
    with pytest.raises(OrchError, match="replay_not_ready"):
        replays.run(OWNER, BOARD, replay, repo)
    fake_cli.options()
    reopened = Replays(Store(replays.store.directory))
    report = reopened.run(OWNER, BOARD, replay, repo, resume=True)
    assert report["state"] == "complete" and report["promote"]
    assert len(report["attempts"]) == len(fake_cli.calls(models=True)) == 11
    assert sum(a["state"] == "complete" for a in report["attempts"]) == 10
    assert report["summary"]["candidate"]["tokens"] is None
    assert report["summary"]["candidate"]["latency_ms"] == sum(
        a["latency_ms"] for a in report["attempts"] if a["side"] == "candidate"
    )


def test_small_concurrency_is_bounded_and_workers_finish(experiment, fake_cli, monkeypatch):
    from threading import Barrier, Lock

    replays, replay, repo, *_ = experiment
    original = SubscriptionCLI.run
    barrier, lock = Barrier(2), Lock()
    active = peak = 0

    def run(self, prompt):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=10)
            return original(self, prompt)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(SubscriptionCLI, "run", run)
    assert replays.run(OWNER, BOARD, replay, repo, concurrency=2)["state"] == "complete"
    assert peak == 2 and active == 0 and len(fake_cli.calls(models=True)) == 10


def test_owner_cli_starts_shows_promotes_and_rolls_back(
    historical_tasks, fake_cli, tmp_path, capsys
):
    from commonplace.cli import main

    data = tmp_path / "data"

    def cli(*args):
        code = main(["--data", str(data), *args])
        out = capsys.readouterr()
        assert code == 0, out.err
        return out.out

    cli("init", "--admin-email", "replay@example.invalid")
    workspace = ("--workspace", "personal", "--board", "personal")
    for channel, prompt in (
        ("live", "Preserve the files exactly"),
        ("exp:repair", "repair the implementation"),
    ):
        path = tmp_path / "config.toml"
        path.write_text(config_text(board="personal", runner=False, prompt=prompt))
        version = json.loads(cli("orch", "submit", str(path), *workspace, "--channel", channel))
        cli("orch", "accept", str(version["version"]), *workspace, "--sha256", version["sha256"])
    repo, manifest = historical_tasks
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    from argparse import Namespace

    from commonplace.orch.cli import _context

    principal, st, _names = _context(
        Namespace(data=str(data), workspace="personal", by=None), owner=True
    )
    interrupted = Replays(st).create(principal, "personal", "exp:repair", manifest)
    with st.transaction() as conn:
        conn.execute("UPDATE orch_replays SET state='running' WHERE id=?", (interrupted,))
    recovered = json.loads(cli("orch", "benchmark", "recover", str(interrupted), *workspace))
    assert recovered["state"] == "refused" and recovered["reason"] == "replay_interrupted"
    assert not fake_cli.calls(models=True)
    report = json.loads(
        cli(
            "orch",
            "benchmark",
            "start",
            str(path),
            *workspace,
            "--repository",
            str(repo),
            "--channel",
            "exp:repair",
        )
    )
    replay = str(report["id"])
    assert report["promote"] and report["summary"]["candidate"]["passed"] == 5
    shown = json.loads(cli("orch", "benchmark", "show", replay, *workspace))
    assert shown["summary"] == report["summary"]
    promoted = json.loads(
        cli(
            "orch",
            "benchmark",
            "promote",
            replay,
            *workspace,
            "--sha256",
            report["candidate_sha256"],
        )
    )
    assert promoted["version"]["sha256"] == report["candidate_sha256"]
    undone = json.loads(cli("orch", "benchmark", "rollback", *workspace))
    assert undone["promotion"]["kind"] == "rollback"


def test_refusal_and_unknown_usage_are_recorded_without_credentials(experiment, fake_cli):
    replays, replay, repo, *_ = experiment
    fake_cli.options(auth="api")
    report = replays.run(OWNER, BOARD, replay, repo)
    assert report["state"] == "refused" and report["reason"] == "subscription_not_verified"
    assert report["attempts"][0]["tokens"] is None
    assert not fake_cli.calls(models=True)


def test_hard_interruption_has_explicit_owner_recovery_without_model_repeat(experiment, fake_cli):
    replays, replay, repo, manifest, _, candidate = experiment
    with replays.store.transaction() as conn:
        conn.execute("UPDATE orch_replays SET state='running' WHERE id=?", (replay,))
    with pytest.raises(OrchError, match="replay_not_ready"):
        replays.run(OWNER, BOARD, replay, repo, resume=True)
    with pytest.raises(OrchError, match="owner_only"):
        replays.recover(MEMBER, BOARD, replay)
    report = replays.recover(OWNER, BOARD, replay)
    assert report["state"] == "refused" and report["reason"] == "replay_interrupted"
    assert not fake_cli.calls(models=True)
    assert replays.create(OWNER, BOARD, candidate.channel, manifest) != replay


def test_report_construction_does_not_take_a_write_transaction(experiment, monkeypatch):
    replays, replay, *_ = experiment

    def unexpected_write():
        raise AssertionError("report construction must not take a write transaction")

    monkeypatch.setattr(replays.store, "transaction", unexpected_write)
    assert Replays(replays.store).get(BOARD, replay)["state"] == "ready"


def test_original_test_files_cannot_be_patched(experiment, fake_cli):
    replays, replay, repo, *_ = experiment
    fake_cli.options(escape=True)
    report = replays.run(OWNER, BOARD, replay, repo)
    assert report["state"] == "refused" and report["benchmark_id"] is None
    assert all(
        a["reason"] == "invalid_model_patch" and a["test_exit"] is None for a in report["attempts"]
    )
    assert not (repo.parent / "outside").exists()


def test_changed_config_during_replay_cannot_record_results_for_a_new_version(
    experiment, fake_cli, monkeypatch
):
    from commonplace.orch import replay as module

    replays, replay, repo, *_ = experiment
    original = module._run_one
    moved = False

    def run(*args):
        nonlocal moved
        result = original(*args)
        if not moved:
            moved = True
            accept_text(replays.store, config_text(prompt="a newer live prompt"))
        return result

    monkeypatch.setattr(module, "_run_one", run)
    with pytest.raises(OrchError, match="stale_benchmark"):
        replays.run(OWNER, BOARD, replay, repo)
    assert replays.get(BOARD, replay)["benchmark_id"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("files", ["../outside"]),
        ("files", ["test_answer.py"]),
        ("revision", "develop"),
        ("known_passed", 0),
    ],
)
def test_manifest_refuses_mutable_refs_tests_and_escaping_paths(historical_tasks, field, value):
    _, manifest = historical_tasks
    manifest = copy.deepcopy(manifest)
    manifest["tasks"][0][field] = value
    with pytest.raises(OrchError, match="invalid_replay"):
        specification(manifest)


def test_nonowner_cannot_start_or_promote(experiment, fake_cli):
    replays, replay, repo, manifest, _, candidate = experiment
    for action in (
        lambda: replays.create(MEMBER, BOARD, candidate.channel, manifest),
        lambda: replays.run(MEMBER, BOARD, replay, repo),
        lambda: replays.promote(
            MEMBER, BOARD, replay, sha256=candidate.sha256, connections=CONNECTIONS
        ),
    ):
        with pytest.raises(OrchError, match="owner_only"):
            action()
    assert not fake_cli.calls()


def test_existing_benchmark_usage_migrates_without_losing_history(tmp_path):
    store = Store(tmp_path / "workspace")
    accept_text(store, config_text())
    accept_text(store, config_text(prompt="repair"), channel="exp:repair")
    with store.transaction() as conn:
        conn.execute("DROP TABLE orch_benchmark_results")
        conn.execute(
            "CREATE TABLE orch_benchmark_results (benchmark_id INTEGER NOT NULL,side TEXT NOT NULL,task TEXT NOT NULL,passed INTEGER NOT NULL,cost_micro_usd INTEGER NOT NULL,tokens INTEGER NOT NULL,latency_ms INTEGER NOT NULL,PRIMARY KEY(benchmark_id,side,task))"
        )
        conn.execute("INSERT INTO orch_benchmark_results VALUES (999,'live','old',1,4,5,6)")
    bench = Benchmarks(store)
    assert bench.results(999, "live") == (TaskResult("old", True, 4, 5, 6),)
    rows = [TaskResult(str(n), True, None, None, 1) for n in range(5)]
    entry = bench.record(OWNER, BOARD, "exp:repair", live_results=rows, candidate_results=rows)
    assert bench.results(entry.id, "live")[0].tokens is None
    assert Benchmarks(Store(store.directory)).results(999, "live")[0].tokens == 5
