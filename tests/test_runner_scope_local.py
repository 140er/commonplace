"""The local ``runner check`` grant scope record: offline, secret-free and unverified."""

import hashlib
import json
import os
import subprocess

import pytest

from tests.runner_harness import _private_json, runner, runner_config

WARNING = "Reported, not verified"
INVALID = "runner: config_invalid: scope_invalid\n"
ENV = {
    "COMMONPLACE_RUNNER_TOKEN": "synthetic-machine-token",
    "RUNNER_T3_GRANT": "synthetic-grant-value",
}
CANONICAL = (
    '{"provider_instance_id":"worker","runner":{"projects":[{"label":"demo",'
    '"project_id":"demo","runtime_ceiling":"approval-required"}],"topic_models":null,'
    '"workspace_strategy":"root"},"t3":{"max_runtime_mode":null,"mcp":null,'
    '"mode_inheritance":"unknown","projects":null,"workspace_strategies":null},"version":1}'
)
DIGEST = "ca053c5e57026aef8e63a46874f3db22c15ea5eb48f4a3a357fff39af50badc0"
T3_UNKNOWN = {
    "max_runtime_mode": None,
    "workspace_strategies": None,
    "projects": None,
    "mcp": None,
    "mode_inheritance": "unknown",
}
ONE_PROVIDER = '{"providerInstances": {"worker": {"config": {"homePath": "/private/path"}}}}'


def offline(tmp, **overrides):
    endpoint = type("Endpoint", (), {"port": 1})()
    return runner_config(tmp, endpoint, ENV["COMMONPLACE_RUNNER_TOKEN"], **overrides)


def project(project_id, label="demo", **fields):
    return {
        label: {
            "project_id": project_id,
            "shared_checkout_ack": f"{label}:shared-checkout",
            **fields,
        }
    }


def home_with(tmp, settings):
    home = tmp / "t3-home"
    (home / "userdata").mkdir(parents=True, exist_ok=True)
    (home / "userdata" / "settings.json").write_text(settings, encoding="utf-8")
    return home


def check(path, home=None, env=None):
    extra = {"T3CODE_HOME": str(home)} if home is not None else {}
    return runner(["check", "--config", str(path)], {**ENV, **extra, **(env or {})})


def record_of(result):
    assert WARNING in result.stdout, (result.returncode, result.stdout, result.stderr)
    lines = [line for line in result.stdout.splitlines() if line.startswith("grant scope: ")]
    assert len(lines) == 1
    text = lines[0][len("grant scope: ") :]
    assert f"grant scope sha256: {hashlib.sha256(text.encode()).hexdigest()}" in result.stdout
    return json.loads(text), text


def assert_invalid(result):
    assert result.returncode == 4, (result.returncode, result.stdout, result.stderr)
    assert result.stderr == INVALID
    assert "grant scope" not in result.stdout
    assert WARNING not in result.stdout


def test_scope_valid_control(tmp_path):
    result = check(offline(tmp_path), home_with(tmp_path, ONE_PROVIDER))
    assert result.returncode == 0
    record, text = record_of(result)
    assert text == CANONICAL
    assert hashlib.sha256(CANONICAL.encode()).hexdigest() == DIGEST
    assert f"grant scope sha256: {DIGEST}" in result.stdout
    assert record["t3"] == T3_UNKNOWN
    assert record["runner"]["workspace_strategy"] == "root"
    assert "/private/path" not in result.stdout
    # The earlier disclosures are still printed; passing proves nothing about T3.
    assert "launch shape uncaptured" in result.stdout
    assert "verified" not in text
    assert ENV["RUNNER_T3_GRANT"] not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "settings,expected",
    [
        (None, None),
        ('{"theme": "dark"}', None),
        ('{"providerInstances": {}}', None),
        ('{"providerInstances": {"one": {}}}', "one"),
        ('{"providerInstances": {"one": {}, "two": {}}}', None),
    ],
    ids=["missing_file", "absent_key", "zero", "one", "two"],
)
def test_provider_cardinality(tmp_path, settings, expected):
    home = tmp_path / "empty-home" if settings is None else home_with(tmp_path, settings)
    result = check(offline(tmp_path), home)
    assert result.returncode == 0, result.stderr
    record, _ = record_of(result)
    assert record["provider_instance_id"] == expected
    assert record["t3"] == T3_UNKNOWN


@pytest.mark.parametrize(
    "settings",
    [
        '{"providerInstances": []}',
        '{"providerInstances": null}',
        '{"providerInstances": {"one": []}}',
        '{"providerInstances": {"one": {}, "bad id": {}}}',
        '{"providerInstances": {"one": {}, "two": 1}}',
        '{"providerInstances": {"sk-secret": {}}}',
    ],
    ids=["list", "null", "value", "unselected_key", "unselected_value", "marker"],
)
def test_provider_structure_invalid(tmp_path, settings):
    assert_invalid(check(offline(tmp_path), home_with(tmp_path, settings)))


@pytest.mark.parametrize(
    "settings",
    [
        '{"a": 1, "a": 2}',
        '{"providerInstances": {"one": {"x": 1, "x": 2}}}',
        '{"other": {"deep": [{"y": 1, "y": 2}]}}',
        '{"a": NaN}',
        '{"a": -Infinity}',
        "{not json",
        "[]",
        "",
    ],
    ids=["top", "provider", "ignored", "nan", "infinity", "malformed", "array", "empty"],
)
def test_settings_recursive_duplicates(tmp_path, settings):
    assert_invalid(check(offline(tmp_path), home_with(tmp_path, settings)))


def test_settings_unreadable(tmp_path):
    home = tmp_path / "t3-home"
    (home / "userdata" / "settings.json").mkdir(parents=True)
    assert_invalid(check(offline(tmp_path), home))


@pytest.mark.parametrize(
    "project_id",
    ["project_1", "123e4567-e89b-12d3-a456-426614174000", "a" * 128, "task-sk-1"],
    ids=["underscore", "uuid", "max", "interior_marker"],
)
def test_id_vectors_accepted(tmp_path, project_id):
    result = check(offline(tmp_path, projects=project(project_id)))
    assert result.returncode == 0, result.stderr
    assert record_of(result)[0]["runner"]["projects"][0]["project_id"] == project_id


@pytest.mark.parametrize(
    "project_id",
    ["has space", "a/b", "a" * 129, "sk-demo", "GHP_x", "cmmn_x"],
    ids=["space", "slash", "long", "sk", "ghp", "cmmn"],
)
def test_id_vectors_rejected_but_run_unchanged(tmp_path, project_id):
    path = offline(tmp_path, projects=project(project_id))
    assert_invalid(check(path))
    # ``run`` keeps its own validation: it reaches the hub, which is unreachable here.
    result = runner(["run", "--config", str(path), "--once"], ENV)
    assert result.returncode == 5, (result.stdout, result.stderr)


def test_label_markers_and_interior(tmp_path):
    good = offline(
        tmp_path / "good",
        projects=project("demo", label="project-xoxo"),
        binding_acks=["project-xoxo:worker"],
    )
    result = check(good)
    assert result.returncode == 0, result.stderr
    assert record_of(result)[0]["runner"]["projects"][0]["label"] == "project-xoxo"
    bad = offline(
        tmp_path / "bad",
        projects=project("demo", label="sk-demo"),
        binding_acks=["sk-demo:worker"],
    )
    assert_invalid(check(bad))


def test_topics_keep_spaces(tmp_path):
    path = offline(tmp_path, topics=["code review"], binding_acks=["demo:code review"])
    result = check(path)
    assert result.returncode == 0, result.stderr
    record_of(result)


@pytest.mark.parametrize(
    "env",
    [
        {"COMMONPLACE_RUNNER_TOKEN": "", "RUNNER_T3_GRANT": ""},
        {"RUNNER_T3_GRANT": ""},
        {"COMMONPLACE_RUNNER_TOKEN": ""},
        {"RUNNER_T3_GRANT": "line\nbreak"},
        {"COMMONPLACE_RUNNER_TOKEN": "line\rbreak"},
    ],
    ids=["both", "token", "grant", "newline", "carriage"],
)
def test_credential_fail_closed(tmp_path, env):
    assert_invalid(check(offline(tmp_path), env=env))


@pytest.mark.parametrize("name", sorted(ENV))
def test_known_secret_collision(tmp_path, name):
    control = check(offline(tmp_path / "control"))
    assert control.returncode == 0
    record_of(control)
    secret = ENV[name]
    path = offline(tmp_path / "collide", projects=project(f"x-{secret}-y"))
    result = check(path)
    assert_invalid(result)
    assert secret not in result.stdout + result.stderr
    home = home_with(tmp_path, json.dumps({"providerInstances": {f"p{secret}": {}}}))
    result = check(offline(tmp_path / "provider"), home)
    assert_invalid(result)
    assert secret not in result.stdout + result.stderr


def test_known_secret_escaped_form(tmp_path):
    secret = 'quote"secret'
    result = check(
        offline(tmp_path, projects=project("demo")),
        env={"RUNNER_T3_GRANT": secret},
    )
    assert result.returncode == 0, result.stderr
    assert_invalid(
        check(offline(tmp_path, projects=project(secret)), env={"RUNNER_T3_GRANT": secret})
    )


@pytest.mark.parametrize("names", ["default_names", "custom_names"])
def test_all_checks_hermetic(tmp_path, monkeypatch, names):
    custom = names == "custom_names"
    token_env, grant_env = (
        ("HERMETIC_TOKEN", "HERMETIC_GRANT")
        if custom
        else ("COMMONPLACE_RUNNER_TOKEN", "RUNNER_T3_GRANT")
    )
    overrides = {"token_env": token_env, "t3_grant_env": grant_env} if custom else {}
    # The parent environment holds credentials and a host settings file.
    sentinel = home_with(tmp_path / "host", '{"providerInstances": {"host-sentinel": {}}}')
    monkeypatch.setenv(token_env, "host-token")
    monkeypatch.setenv(grant_env, "host-grant")
    monkeypatch.setenv("T3CODE_HOME", str(sentinel))
    path = offline(tmp_path, **overrides)
    assert_invalid(runner(["check", "--config", str(path)], {}))
    explicit = {token_env: "explicit-token", grant_env: "explicit-grant"}
    result = runner(["check", "--config", str(path)], explicit)
    assert result.returncode == 0, result.stderr
    assert record_of(result)[0]["provider_instance_id"] is None
    assert "host-sentinel" not in result.stdout

    chat = {
        "hub_url": "http://127.0.0.1:1",
        "token_env": "COMMONPLACE_RUNNER_TOKEN",
        "chat": json.loads(chat_block()),
    }
    only = tmp_path / "chat.json"
    _private_json(only, chat)
    bad = home_with(tmp_path / "bad", "{not json")
    result = runner(["check", "--config", str(only)], {"T3CODE_HOME": str(bad)})
    assert result.returncode == 0, result.stderr
    assert "grant scope" not in result.stdout


def chat_block():
    argv = [
        "/usr/bin/env",
        "-p",
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--model",
        "{model}",
    ]
    return json.dumps(
        {
            "default_cli": "example",
            "context": "minimal",
            "relay_always": False,
            "clis": {"example": {"argv": argv, "default_model": "m1", "tools": True}},
        }
    )


def test_harness_check_env_is_hermetic_run_env_is_not(monkeypatch):
    seen = []

    def capture(argv, env, **_):
        seen.append(env)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", capture)
    monkeypatch.setenv("HOST_ONLY_SENTINEL", "x")
    runner(["check", "--config", "c"], {"EXPLICIT": "1"})
    runner(["run", "--config", "c"], {"EXPLICIT": "1"})
    assert "HOST_ONLY_SENTINEL" not in seen[0] and seen[0]["EXPLICIT"] == "1"
    assert seen[0]["T3CODE_HOME"] == seen[0]["HOME"] != os.environ.get("HOME")
    assert seen[1]["HOST_ONLY_SENTINEL"] == "x" and seen[1]["EXPLICIT"] == "1"
