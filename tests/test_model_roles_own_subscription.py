"""A model per role on your own subscription (card #661): the workspace default per role and its
precedence, the Grok suggestion, linking read back as kind and yes/no, chat routed to the chosen
provider on the person's own account, and never a silent fallback or a credential in any read."""

import json
import sqlite3
import sys
import threading

import pytest

from commonplace.projects import model_modes
from commonplace.runner import chat_agent
from mempalace.sqlite_crypto import connect, row_factory
from tests._relay_helpers import relay_hub_fixture  # noqa: F401

MODELS = "/api/settings/models"
MODE = "/api/settings/model-mode"
CONNECTIONS = "/api/me/model-connections"
GROK = {"provider": "grok", "model": "grok-4.7"}
OPUS = {"provider": "claude", "model": "claude-opus-5-5"}


def _list(h, choice=OPUS):
    status, body, _ = h.request(h.owner, "POST", MODELS, choice)
    assert status == 200, body
    return body


def _link(h, provider, label, machine="own-1"):
    return h.request(
        h.owner, "POST", CONNECTIONS, {"provider": provider, "label": label, "machine": machine}
    )[:2]


def _me(h, board=None):
    path = "/api/me/model-mode" + (f"?board={board}" if board else "")
    status, body, _ = h.request(h.owner, "GET", path)
    assert status == 200, body
    return body


def _replies(h, who, code=None):
    answers = [r for r in h.chat(who)[1]["replies"] if r["kind"] == "answer"]
    return [r for r in answers if code is None or r["code"] == code]


def _chat_ledger(h):
    conn = connect(str(h.hub.gateway.workspace_dir(h.workspace)) + "/runner.sqlite3")
    conn.row_factory = row_factory
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM run_ledger WHERE source='chat'")]
    finally:
        conn.close()


# ---------------------------------------------------------------- per role: save, read back, precedence


def test_the_owner_saves_a_workspace_model_per_role_and_every_reader_sees_where_it_comes_from(
    relay_hub,
):
    h = relay_hub
    assert OPUS in _list(h)["model_catalog"]
    status, view, _ = h.request(
        h.owner, "PUT", MODELS, {"main": GROK, "worker": GROK, "reviewer": OPUS, "gate": OPUS}
    )
    assert status == 200, view
    assert view["can_configure"] is True
    assert view["models"] == {"main": GROK, "worker": GROK, "reviewer": OPUS, "gate": OPUS}
    assert {
        role: item["source"] for role, item in view["models_effective"].items()
    } == dict.fromkeys(("main", "worker", "reviewer", "gate"), "workspace")
    # A project with no choice of its own runs on the workspace's choice, per role.
    project = h.project(h.owner, "work")[1]["models_effective"]
    assert (project["main"]["provider"], project["main"]["model"], project["main"]["source"]) == (
        "grok",
        "grok-4.7",
        "workspace",
    )
    assert (project["gate"]["provider"], project["gate"]["source"]) == ("claude", "workspace")
    # The read-back (route and tool) names every role with its account and linked yes/no.
    workspace = _me(h)["workspace"]["roles"]
    assert set(workspace) == {"main", "worker", "reviewer", "gate"}
    for item in workspace.values():
        assert {"provider", "model", "source", "mode", "mode_source", "kind", "linked"} <= set(item)
    assert (workspace["main"]["mode"], workspace["main"]["linked"]) == ("legacy", False)
    status, said = h.mcp(h.a1, "model_mode", board="work")
    assert status == 200 and said["ok"], said
    roles = json.loads(said["text"])["project"]["roles"]
    assert (roles["main"]["provider"], roles["main"]["source"]) == ("grok", "workspace")
    member = h.request(h.member, "GET", MODELS)[1]
    assert member["can_configure"] is False and member["models"]["main"] == GROK


def test_a_projects_own_choice_wins_over_the_workspace_default_for_that_role_only(relay_hub):
    h = relay_hub
    assert h.request(h.owner, "PUT", MODELS, {"main": GROK, "worker": GROK})[0] == 200
    assert h.set_main_model("claude", "claude-haiku-5-5")[0] == 200
    effective = h.project(h.owner, "work")[1]["models_effective"]
    assert (effective["main"]["provider"], effective["main"]["source"]) == ("claude", "project")
    assert (effective["worker"]["provider"], effective["worker"]["source"]) == ("grok", "workspace")
    roles = _me(h, "work")["project"]["roles"]
    assert (roles["main"]["source"], roles["worker"]["source"]) == ("project", "workspace")
    # Clearing the project's choice falls back to the workspace's.
    h.models(h.owner, "work", {"main": None}, declare=False)
    assert h.project(h.owner, "work")[1]["models_effective"]["main"]["source"] == "workspace"
    # Clearing the workspace's choice leaves a role on today's default.
    h.request(h.owner, "PUT", MODELS, {"worker": None})
    assert h.request(h.owner, "GET", MODELS)[1]["models"]["worker"] is None


def test_only_the_signed_in_owner_changes_the_workspace_models(relay_hub):
    h = relay_hub
    for who in (h.member, h.a1):
        assert h.request(who, "PUT", MODELS, {"main": GROK})[0] == 404
        assert h.request(who, "POST", MODELS, OPUS)[0] == 404
    assert h.request(h.a1, "GET", MODELS)[0] == 404  # agents read the tool, not the settings
    assert h.request(h.owner, "GET", MODELS)[1]["models"]["main"] is None
    assert h.request(h.owner, "PUT", MODELS, {"main": GROK})[0] == 200
    with h.db._lock:
        actions = [
            r["action"]
            for r in h.db._conn.execute(
                "SELECT action FROM audit_log WHERE action='models.workspace'"
            ).fetchall()
        ]
    assert actions == ["models.workspace"]


def test_grok_is_offered_everywhere_but_is_never_a_default(relay_hub):
    h = relay_hub
    view = h.project(h.owner, "work")[1]
    assert view["model_catalog"] == [] and GROK in view["model_suggestions"]
    assert view["models_effective"]["main"]["source"] == "none"  # still nothing chosen
    assert GROK in h.request(h.owner, "GET", MODELS)[1]["model_suggestions"]
    # A model that is neither listed nor suggested is refused, and nothing is written.
    status, body, _ = h.request(h.owner, "PUT", MODELS, {"main": {"provider": "x", "model": "y"}})
    assert (status, body["code"]) == (400, "unknown_model")
    assert h.request(h.owner, "PUT", MODELS, {"nope": GROK})[0] == 400
    assert h.request(h.owner, "GET", MODELS)[1]["models"]["main"] is None
    # Picking the suggestion for a project puts it on that project's list.
    assert h.models(h.owner, "work", {"main": GROK}, declare=False)[0] == 200
    assert h.project(h.owner, "work")[1]["model_catalog"] == [GROK]


# ---------------------------------------------------------------- linking: kind and yes/no, never a token


def test_one_machine_links_a_grok_and_a_claude_subscription_and_reads_back_kind_and_linked(
    relay_hub, caplog
):
    h = relay_hub
    machine = h.machine(label="own-1")
    _list(h)
    h.request(
        h.owner, "PUT", MODELS, {"main": GROK, "worker": GROK, "reviewer": OPUS, "gate": OPUS}
    )
    h.request(h.owner, "PUT", MODE, {"mode": "personal"})
    before = _me(h)["workspace"]["roles"]
    assert before["main"]["linked"] is False and before["main"]["kind"] is None
    assert "grok" in before["main"]["reason"]
    assert _link(h, "grok", "my Grok subscription")[0] == 201
    assert _link(h, "claude", "my Claude subscription")[0] == 201
    assert _link(h, "grok", "again")[0] == 400  # one connection per machine and provider
    roles = _me(h)["workspace"]["roles"]
    assert (roles["main"]["kind"], roles["main"]["linked"], roles["main"]["label"]) == (
        "person",
        True,
        "my Grok subscription",
    )
    assert (roles["gate"]["kind"], roles["gate"]["label"]) == ("person", "my Claude subscription")
    project = _me(h, "work")["project"]["roles"]["main"]
    assert (project["mode"], project["source"], project["linked"]) == (
        "personal",
        "workspace",
        True,
    )
    # Another person reads linked: no, and never the owner's label.
    theirs = h.request(h.member, "GET", "/api/me/model-mode?board=work")[1]["project"]["roles"]
    assert theirs["main"]["linked"] is False and "my Grok" not in json.dumps(theirs)
    # No read carries the machine's credential or any token-like field.
    reads = [
        h.request(h.owner, "GET", MODELS)[2],
        h.request(h.owner, "GET", CONNECTIONS)[2],
        h.request(h.owner, "GET", "/api/me/model-mode?board=work")[2],
        json.dumps(h.project(h.owner, "work")[1]["models_effective"]).encode(),
        h.mcp(h.a1, "model_mode", board="work")[1]["text"].encode(),
    ]
    secret = machine["machine_token"].encode()
    for raw in reads:
        assert secret not in raw
        assert not {"token", "key", "secret", "api_key"} & set(_keys(json.loads(raw)))
    assert machine["machine_token"] not in caplog.text


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_a_machine_with_another_persons_connection_cannot_be_linked(relay_hub):
    h = relay_hub
    h.machine(label="own-1")
    assert _link(h, "grok", "mine")[0] == 201
    with h.db._lock, h.db._tx() as conn:  # the machine is someone else's own account now
        conn.execute("UPDATE model_connections SET user_id='someone-else'")
    assert _link(h, "claude", "mine too")[0] == 404


# ---------------------------------------------------------------- routing and failure states in chat


def test_chat_for_a_grok_main_runs_grok_4_7_on_the_persons_own_subscription(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    h.models(h.owner, "work", {"main": GROK}, declare=False)
    h.request(h.owner, "PUT", MODE, {"mode": "personal"})
    _link(h, "grok", "my Grok subscription")
    assert h.poll(machine, clis=("claude",))[0] == 204
    h.post(h.owner, "Before the Grok CLI is on")
    assert h.poll(machine, clis=("claude",))[0] == 204  # never handed to another CLI
    [refused] = _replies(h, h.owner, "no_runner")
    assert "selected provider" in refused["text"]
    assert h.poll(machine, clis=("grok", "claude"))[0] == 204
    h.post(h.owner, "Question for Grok")
    status, lease = h.poll(machine, clis=("grok", "claude"))
    assert status == 200, lease
    assert (lease["cli"], lease["model"]) == ("grok", "grok-4.7")
    assert h.answer(machine, lease["job"], lease["lease_token"], "Hi", "grok", "grok-4.7")[0] == 200
    [answer] = _replies(h, h.owner, "ok")
    assert answer["model"] == "runner:grok/grok-4.7"
    [row] = [r for r in _chat_ledger(h) if r["model"] == "grok-4.7"]
    assert (row["model_provider"], row["account_kind"], row["account_label"]) == (
        "grok",
        "person",
        "my Grok subscription",
    )


def test_an_unlinked_grok_main_refuses_in_words_and_never_falls_back(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    h.models(h.owner, "work", {"main": GROK}, declare=False)
    h.request(h.owner, "PUT", MODE, {"mode": "personal"})
    _link(h, "claude", "my Claude subscription")  # an account for another provider only
    assert h.poll(machine, clis=("grok", "claude"))[0] == 204
    h.post(h.owner, "Question")
    assert h.poll(machine, clis=("grok", "claude"))[0] == 204
    assert not any(j["state"] == "queued" for j in h.jobs())
    [reply] = _replies(h, h.owner, "connection_missing")
    assert "your own grok account" in reply["text"]
    [item] = h.request(h.owner, "GET", "/api/needs-me")[1]["model_connections"]
    assert (item["board"], item["provider"]) == ("work", "grok")
    main = _me(h, "work")["project"]["roles"]["main"]
    assert (main["kind"], main["linked"]) == (None, False) and "grok" in main["reason"]


def test_an_expired_sign_in_tells_the_person_asks_once_and_clears_when_it_answers(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    h.models(h.owner, "work", {"main": GROK}, declare=False)
    h.request(h.owner, "PUT", MODE, {"mode": "personal"})
    _link(h, "grok", "my Grok subscription")
    h.poll(machine, clis=("grok",))
    for text in ("One", "Two"):
        h.post(h.owner, text)
        status, lease = h.poll(machine, clis=("grok",))
        assert status == 200, lease
        assert h.fail(machine, lease["job"], lease["lease_token"], "cli_signed_out")[0] == 200
    replies = _replies(h, h.owner, "cli_signed_out")
    assert len(replies) == 2 and "grok login" in replies[0]["text"]
    assert "Nothing was sent to another account" in replies[0]["text"]
    [item] = h.request(h.owner, "GET", "/api/needs-me")[1]["model_signins"]
    assert (item["kind"], item["provider"], item["machine"]) == ("model_signin", "grok", "own-1")
    assert "model_signins" not in h.request(h.member, "GET", "/api/needs-me")[1]
    h.post(h.owner, "Three")
    status, lease = h.poll(machine, clis=("grok",))
    assert (
        h.answer(machine, lease["job"], lease["lease_token"], "Back", "grok", "grok-4.7")[0] == 200
    )
    assert "model_signins" not in h.request(h.owner, "GET", "/api/needs-me")[1]


def test_a_signed_out_cli_is_told_apart_and_its_words_stay_on_the_machine(tmp_path):
    def cli(name, words, code=1):
        path = tmp_path / name
        path.write_text(
            f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n"
            f"sys.stderr.write({words!r})\nsys.exit({code})\n"
        )
        path.chmod(0o755)
        return {"argv": [str(path)], "default_model": "grok-4.7", "tools": True}

    chat = {
        "clis": {
            "out": cli("out", "Error: not logged in. Run `grok login` first."),
            "broken": cli("broken", "Error: something else broke"),
        }
    }
    assert chat_agent._ask_cli(chat, "out", None, "system", "prompt") == ("fail", "cli_signed_out")
    assert chat_agent._ask_cli(chat, "broken", None, "system", "prompt") == ("fail", "cli_failed")


# ---------------------------------------------------------------- the schema change


OLD_INDEX = (
    "CREATE UNIQUE INDEX model_connections_machine ON model_connections(machine_id) "
    "WHERE revoked_at IS NULL"
)


class _Db:
    def __init__(self, path):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()


def test_the_index_upgrade_keeps_every_connection_and_allows_one_per_provider(tmp_path):
    db = _Db(tmp_path / "control.sqlite3")
    old = model_modes.SCHEMA.split("DROP INDEX")[0]  # the tables as slice 1 left them
    db._conn.executescript(old + OLD_INDEX + ";")
    db._conn.execute(
        "INSERT INTO model_connections VALUES "
        "('mc_0000000000000001','ws','u1','claude','mine','m1','2026-11-20T00:00:00Z',NULL)"
    )
    model_modes.ensure_schema(db)
    model_modes.ensure_schema(db)  # idempotent on every open
    names = {r[0] for r in db._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "model_connections_machine" not in names
    assert "model_connections_machine_provider" in names
    assert db._conn.execute("SELECT COUNT(*) FROM model_connections").fetchone()[0] == 1
    db._conn.execute(
        "INSERT INTO model_connections VALUES "
        "('mc_0000000000000002','ws','u1','grok','mine','m1','2026-11-20T00:00:00Z',NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "INSERT INTO model_connections VALUES "
            "('mc_0000000000000003','ws','u1','grok','x','m1','2026-11-20T00:00:00Z',NULL)"
        )


# ---------------------------------------------------------------- the Grok CLI on the runner

GROK_ARGV = [
    "-p",
    "--no-memory",
    "--no-subagents",
    "--disable-web-search",
    "--max-turns",
    "1",
    "--tools",
    "",
    "-m",
    "{model}",
    "--output-format",
    "json",
]


def _grok_chat(tmp_path, args, tools=False):
    binary = tmp_path / "grok"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return chat_agent.validate_chat(
        {
            "default_cli": "grok",
            "clis": {
                "grok": {
                    "argv": [str(binary), *args],
                    "default_model": "grok-4.7",
                    "tools": tools,
                }
            },
        }
    )


def test_the_grok_cli_runs_tools_off_only_with_every_safety_flag(tmp_path):
    assert _grok_chat(tmp_path, GROK_ARGV)["clis"]["grok"]["default_model"] == "grok-4.7"
    assert _grok_chat(tmp_path, [*GROK_ARGV[:4], "--max-turns=1", *GROK_ARGV[6:]])


@pytest.mark.parametrize(
    "flag",
    [
        "--always-approve",
        "--yolo",
        "--dangerously-skip-permissions",
        "--allow",
        "--allowedTools",
        "--allowed-tools",
        "--mcp-config",
        "--permission-mode",
    ],
)
def test_each_denied_grok_flag_is_refused(tmp_path, flag):
    with pytest.raises(ValueError, match="tools-off"):
        _grok_chat(tmp_path, [*GROK_ARGV, flag])
    with pytest.raises(ValueError, match="tools-off"):
        _grok_chat(tmp_path, [*GROK_ARGV, f"{flag}=x"])


@pytest.mark.parametrize(
    "dropped", ["-p", "--no-memory", "--no-subagents", "--disable-web-search", "--max-turns"]
)
def test_a_grok_argv_without_a_required_flag_is_refused(tmp_path, dropped):
    args = list(GROK_ARGV)
    at = args.index(dropped)
    del args[at : at + (2 if dropped == "--max-turns" else 1)]
    with pytest.raises(ValueError, match="tools-off"):
        _grok_chat(tmp_path, args)


@pytest.mark.parametrize(
    "args",
    [
        [*GROK_ARGV[:5], "2", *GROK_ARGV[6:]],  # more than one turn
        [*GROK_ARGV[:7], "read", *GROK_ARGV[8:]],  # a tool switched on
        [*GROK_ARGV, "--tools", "read"],  # switched on again later
    ],
)
def test_a_grok_argv_with_more_turns_or_tools_is_refused(tmp_path, args):
    with pytest.raises(ValueError, match="tools-off"):
        _grok_chat(tmp_path, args)


# Recorded shapes of Grok's headless JSON (synthetic numbers, no real call).
GROK_RESULT = {
    "type": "result",
    "result": "  Hello from Grok.  ",
    "session_id": "synthetic-session",
    "usage": {
        "inputTokens": 20_272,
        "outputTokens": 272,
        "cachedReadTokens": 11_264,
        "reasoningTokens": 180,
        "costUsdTicks": 230_272_000,
        "modelUsage": {"grok-4.7": {"inputTokens": 20_272, "outputTokens": 272}},
    },
}
GROK_TEXT_ONLY_TOTALS = {
    "text": "Per model only.",
    "usage": {
        "modelUsage": {
            "grok-4.7": {"inputTokens": 100, "outputTokens": 10, "costUsdTicks": 20_000},
            "grok-4.7-mini": {"inputTokens": 50, "outputTokens": 5, "costUsdTicks": 10_000},
        }
    },
}


def test_grok_headless_json_gives_the_answer_tokens_and_cost_and_nothing_else():
    text, usage = chat_agent._unwrap(json.dumps(GROK_RESULT))
    assert text == "Hello from Grok."
    assert usage == {
        "input_tokens": 20_272,
        "output_tokens": 272,
        "cost_micros": 23_027,
        "currency": "USD",
    }
    text, usage = chat_agent._unwrap(json.dumps(GROK_TEXT_ONLY_TOTALS))
    assert text == "Per model only."
    assert usage == {"input_tokens": 150, "output_tokens": 15, "cost_micros": 3, "currency": "USD"}
    # A negative or non-integer count is never reported; the answer still is.
    broken = {"type": "result", "result": "x", "usage": {"inputTokens": -1, "costUsdTicks": 1.5}}
    assert chat_agent._unwrap(json.dumps(broken)) == ("x", {})
    # Claude's result keeps its own parser.
    claude = {"type": "result", "result": "c", "usage": {"input_tokens": 3, "output_tokens": 4}}
    assert chat_agent._unwrap(json.dumps(claude)) == ("c", {"input_tokens": 3, "output_tokens": 4})


@pytest.mark.parametrize(
    "said,code",
    [
        ("Error: request failed with status 401 Unauthorized", "cli_signed_out"),
        ("You are not logged in. Run `grok login --device-auth`.", "cli_signed_out"),
        ("Error: 403 Forbidden: grok-4.7 is not available on your plan", "cli_not_entitled"),
        ("Error: 426 Upgrade Required: please update the CLI", "cli_outdated"),
        ("Error: something else broke", "cli_failed"),
    ],
)
def test_each_cli_refusal_maps_to_its_own_code(said, code):
    assert chat_agent.failure_code(said) == code


@pytest.mark.parametrize(
    "code,words",
    [
        ("cli_not_entitled", "refused access to grok-4.7 for your plan"),
        ("cli_outdated", "too old for the provider"),
    ],
)
def test_a_plan_or_version_refusal_is_answered_in_words_and_never_rerouted(relay_hub, code, words):
    h = relay_hub
    machine = h.machine(label="own-1")
    h.models(h.owner, "work", {"main": GROK}, declare=False)
    h.request(h.owner, "PUT", MODE, {"mode": "personal"})
    _link(h, "grok", "my Grok subscription")
    h.poll(machine, clis=("grok", "claude"))
    h.post(h.owner, "Question")
    status, lease = h.poll(machine, clis=("grok", "claude"))
    assert status == 200, lease
    assert h.fail(machine, lease["job"], lease["lease_token"], code)[0] == 200
    [reply] = _replies(h, h.owner, code)
    assert words in reply["text"] and "Nothing was sent to another account" in reply["text"]
    assert h.poll(machine, clis=("grok", "claude"))[0] == 204  # nothing re-queued elsewhere
    assert "model_signins" not in h.request(h.owner, "GET", "/api/needs-me")[1]
