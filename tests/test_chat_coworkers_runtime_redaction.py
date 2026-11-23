"""Item13: transient provider input copies; retained content stays exact."""

import json
from types import SimpleNamespace

import pytest

from commonplace.boards.service import BoardService
from commonplace.chat import model, settings, worker as front_worker
from commonplace.chat.coworkers import runtime, store as coworker_store
from commonplace.chat.types import MAX_INPUT_CHARS
from commonplace.extensions import ToolContext
from tests._chat_helpers import chat_environment, chat_hub_fixture  # noqa: F401

CORPUS = [
    pytest.param(
        "Before orchard\npassword=fixture-only-value\n  Keep Ω.\t",
        "Before orchard\npassword=[redacted:assignment]\n  Keep Ω.\t",
        ("fixture-only-value",),
        id="K01-assignment",
    ),
    pytest.param(
        "Before orchard\n-----BEGIN PRIVATE KEY-----\nfixture-only material\n"
        "-----END PRIVATE KEY-----\n  Keep Ω.\t",
        "Before orchard\n[redacted:private_key]\n  Keep Ω.\t",
        ("fixture-only material",),
        id="K02-multiline-key",
    ),
    pytest.param(
        "Before orchard\nAuthorization: Bearer fixture-token-value\n  Keep Ω.\t",
        "Before orchard\nAuthorization: [redacted:bearer]\n  Keep Ω.\t",
        ("fixture-token-value",),
        id="K03-header",
    ),
    pytest.param(
        'Before orchard\n{"password":"fixture-only-value"}\n  Keep Ω.\t',
        'Before orchard\n{"password":"[redacted:json_secret]"}\n  Keep Ω.\t',
        ("fixture-only-value",),
        id="K04-json-string",
    ),
    pytest.param(
        'Before orchard\n  Keep Ω.\t "literal" \\ path\nPerson\'s question or request: ordinary',
        'Before orchard\n  Keep Ω.\t "literal" \\ path\nPerson\'s question or request: ordinary',
        (),
        id="K05-verbatim-control",
    ),
]
MODES = [
    pytest.param("local-dm", id="I13-P01-local-dm"),
    pytest.param("local-group", id="I13-P02-local-group"),
    pytest.param("local-board", id="I13-P03-local-board"),
    pytest.param("hosted-openai", id="I13-P04-hosted-openai"),
    pytest.param("hosted-anthropic", id="I13-P05-hosted-anthropic"),
]
KEY = "sk-" + "s" * 24
NAME = "Nia password=fixture-only-name"
CLEAN_NAME = "Nia password=[redacted:assignment]"


@pytest.fixture
def wire(chat_hub, monkeypatch):
    state = SimpleNamespace(calls=[], on_open=None)

    class CaptureCall:
        def __init__(self, config, endpoint, timeout):
            self.config = config
            self.endpoint = endpoint
            self.closed = False
            self.reply = b""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def open(self, payload, on_sent=None):
            state.calls.append(
                {
                    "payload": payload,
                    "body": json.loads(payload),
                    "headers": self.endpoint.headers.copy(),
                    "provider": self.config.provider,
                }
            )
            if on_sent is not None:
                on_sent()
            if state.on_open is not None:
                state.on_open()
            answer = "Synthetic answer."
            self.reply = json.dumps(
                {"content": [{"type": "text", "text": answer}]}
                if self.endpoint.anthropic
                else {"choices": [{"message": {"content": answer}}]}
            ).encode()
            return self

        def isclosed(self):
            return self.closed

        def read(self, size):
            chunk, self.reply = self.reply[:size], self.reply[size:]
            self.closed = not self.reply
            return chunk

    def forbidden(*args, **kwargs):
        pytest.fail("The item13 transport capture must never connect to a provider")

    monkeypatch.setattr(model, "Call", CaptureCall)
    monkeypatch.setattr(model, "_connect_socket", forbidden)
    return state


def configure(h, monkeypatch, mode):
    # Only synthetic test configuration; production key resolution stays real.
    monkeypatch.setenv("COMMONPLACE_CHAT_KEY_TEST", KEY)
    monkeypatch.setenv("COMMONPLACE_CHAT_KEY_WORKSPACES", h.workspace)
    monkeypatch.setenv("COMMONPLACE_CHAT_KEY_HOSTS", "provider.example.test")
    data: dict[str, object] = {
        "provider": "openai_compatible",
        "model": "fixture",
        "base_url": "http://127.0.0.1:9",
    }
    body: dict[str, object] = {"front": data}
    if mode.startswith("hosted-"):
        body.update(scope="work", mode="hosted")
        data["key_ref"] = "env:COMMONPLACE_CHAT_KEY_TEST"
        if mode == "hosted-anthropic":
            data["provider"] = "anthropic"
            data.pop("base_url")
        else:
            data["base_url"] = "https://provider.example.test"
    settings.set_front(h.db, h.person(h.admin), body)


def service(h):
    return BoardService(ToolContext(principal=h.person(h.admin), gateway=h.gateway))


def conversation(h, mode, *, name="Nia", instructions="Use a calm tone."):
    store = coworker_store.open_store(h.gateway, h.workspace)
    bot = store.create_bot(h.admin, name, instructions=instructions)
    group = mode == "local-group"
    conv = store.create_conversation(
        h.admin,
        "group" if group else "dm",
        member_ids=[h.member_user] if group else [],
        bot_ids=[bot["id"]],
        board="work" if mode not in ("local-dm", "local-group") else None,
    )
    return store, bot, conv


def payload_parts(call):
    body = call["body"]
    messages = body["messages"]
    if call["provider"] == "anthropic":
        assert len(messages) == 1 and messages[0]["role"] == "user"
        return body["system"], messages[0]["content"]
    assert len(messages) == 2
    assert [message["role"] for message in messages] == ["system", "user"]
    return messages[0]["content"], messages[1]["content"]


def job(store, h, cid, receipt):
    return next(
        item for item in store.list_jobs(h.admin, cid) if item["id"] == receipt["job"]["id"]
    )


def retained_message(store, h, cid, receipt):
    return next(
        item for item in store.list_messages(h.admin, cid) if item["id"] == receipt["message"]["id"]
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("raw,expected,values", CORPUS)
def test_item13_provider_payload_and_retained_originals(
    chat_hub, monkeypatch, wire, mode, raw, expected, values
):
    h = chat_hub
    configure(h, monkeypatch, mode)
    boards = service(h)
    title, body = "Fixture password=fixture-only-task", "Task body\n  Keep Ω.\t"
    card = boards.task_add(board="work", title=title, body=body)
    audit_reason = "Fixture password=fixture-only-audit"
    audit_seq = h.db.audit(
        action="item13.fixture",
        decision="ok",
        workspace_id=h.workspace,
        user_id=h.admin,
        reason=audit_reason,
    )
    audit_before = dict(
        next(row for row in h.db.audit_entries(h.workspace) if row["seq"] == audit_seq)
    )
    board_message = boards.space_post("work", raw)
    with h.db._lock:
        queued = [
            tuple(row)
            for row in h.db._conn.execute(
                "SELECT workspace_id,board,message_id,user_id,state,code,called "
                "FROM chat_front_jobs WHERE workspace_id=?",
                (h.workspace,),
            )
        ]
    assert queued == [(h.workspace, "work", board_message["id"], h.admin, "pending", "", 0)]
    assert front_worker.FrontWorker(h.gateway, start=False).run_once()
    assert len(wire.calls) == 1
    _, front_content = payload_parts(wire.calls[0])
    assert front_content.endswith("Person's question or request: " + expected)
    for value in values:
        assert value not in wire.calls[0]["payload"].decode()
    wire.calls.clear()

    store, bot, conv = conversation(h, mode, name=NAME if values else "Nia", instructions=raw)
    cid = conv["id"]
    hidden = store.create_conversation(h.admin, "dm", bot_ids=[bot["id"]])
    store.add_memory(h.admin, hidden["id"], "Hidden orchard fixture")
    context_user = h.member_user if mode == "local-group" else h.admin
    store.add_memory(context_user, cid, raw)
    removed = store.add_memory(h.admin, cid, "Removed orchard fixture")
    store.remove_memory(h.admin, cid, removed["id"])
    prior = store.post_message(context_user, cid, raw, "item13-prior")
    store.cancel_job(context_user, prior["job"]["id"])
    current = store.post_message(h.admin, cid, raw, "item13-current")
    future = store.post_message(h.admin, cid, "Future orchard fixture", "item13-future")
    store.cancel_job(h.admin, future["job"]["id"])
    config_before = settings.front(h.db, h.workspace, conv["board"])
    key_before = settings.resolve_key(config_before)

    assert h.gateway.coworker_worker.run_once(h.workspace)
    assert len(wire.calls) == 1
    assert job(store, h, cid, current)["state"] == "done"
    system, content = payload_parts(wire.calls[0])
    data = json.loads(content)
    assert system == runtime.SYSTEM + expected[:2000]
    assert data["bot"] == {"id": bot["id"], "name": CLEAN_NAME if values else "Nia"}
    assert data["message"] == {
        "id": current["message"]["id"],
        "actor": f"user:{h.admin}",
        "text": expected,
    }
    assert [row["id"] for row in data["history"]] == [prior["message"]["id"]]
    assert [row["text"] for row in data["history"]] == [expected]
    assert [row["text"] for row in data["memories"]] == [expected]
    assert len(system) + len(content) <= MAX_INPUT_CHARS
    for value in (*values, "fixture-only-name" if values else "absent-fixture"):
        assert value not in wire.calls[0]["payload"].decode()
    for absent in ("Hidden orchard fixture", "Removed orchard fixture", "Future orchard fixture"):
        assert absent not in content

    assert retained_message(store, h, cid, prior)["text"] == raw
    assert retained_message(store, h, cid, current)["text"] == raw
    assert retained_message(store, h, cid, future)["text"] == "Future orchard fixture"
    assert [row["text"] for row in store.list_memory(h.admin, cid)] == [raw]
    persisted_bot = next(row for row in store.list_bots() if row["id"] == bot["id"])
    assert (persisted_bot["name"], persisted_bot["instructions"]) == (bot["name"], raw)
    assert (
        next(
            row for row in boards.space_read("work")["messages"] if row["id"] == board_message["id"]
        )["text"]
        == raw
    )
    retained_card = next(row for row in boards.task_list("work") if row["number"] == card["number"])
    assert (retained_card["title"], retained_card["body"]) == (title, body)
    assert (
        dict(next(row for row in h.db.audit_entries(h.workspace) if row["seq"] == audit_seq))
        == audit_before
    )
    assert settings.front(h.db, h.workspace, conv["board"]) == config_before
    assert settings.resolve_key(config_before) == key_before
    if config_before.key_ref is None:
        assert "Authorization" not in wire.calls[0]["headers"]
    elif config_before.provider == "anthropic":
        assert key_before == KEY and wire.calls[0]["headers"]["x-api-key"] == KEY
    else:
        assert key_before == KEY and wire.calls[0]["headers"]["Authorization"] == "Bearer " + KEY


@pytest.mark.parametrize(
    "control,code",
    [
        pytest.param("none", "needs_configuration", id="I13-C01-none"),
        pytest.param("disabled", "needs_configuration", id="I13-C02-disabled"),
        pytest.param("boardless-hosted", "needs_configuration", id="I13-C03-boardless-hosted"),
        pytest.param("inherited-hosted", "needs_configuration", id="I13-C04-inherited-hosted"),
        pytest.param("key-missing", "key_missing", id="I13-C05-key-missing"),
        pytest.param("key-denied", "key_not_allowed", id="I13-C06-key-denied"),
    ],
)
def test_item13_refusal_never_prepares_a_payload(chat_hub, monkeypatch, wire, control, code):
    h = chat_hub
    configure(h, monkeypatch, "local-dm")
    mode = (
        "local-board"
        if control in ("inherited-hosted", "key-missing", "key-denied")
        else "local-dm"
    )
    store, _, conv = conversation(h, mode)
    raw = "Original password=fixture-only-refusal\n  Keep Ω.\t"
    store.add_memory(h.admin, conv["id"], raw)
    receipt = store.post_message(h.admin, conv["id"], raw, "item13-refusal")
    front: dict[str, object] = {
        "provider": "openai_compatible",
        "model": "fixture",
        "base_url": "http://127.0.0.1:9",
    }
    if control == "none":
        front = {"provider": "none"}
    elif control == "disabled":
        front["enabled"] = False
    elif control in ("boardless-hosted", "inherited-hosted"):
        front["base_url"] = "https://provider.example.test"
    else:
        front["base_url"] = "https://provider.example.test"
        front["key_ref"] = "env:COMMONPLACE_CHAT_KEY_TEST"
    update: dict[str, object] = {"front": front}
    if control in ("key-missing", "key-denied"):
        update.update(scope="work", mode="hosted")
    settings.set_front(h.db, h.person(h.admin), update)
    if control == "key-missing":
        monkeypatch.delenv("COMMONPLACE_CHAT_KEY_TEST")
    elif control == "key-denied":
        monkeypatch.delenv("COMMONPLACE_CHAT_KEY_WORKSPACES")
    assert h.gateway.coworker_worker.run_once(h.workspace)
    assert wire.calls == []
    result = job(store, h, conv["id"], receipt)
    assert (result["state"], result["code"]) == ("needs_configuration", code)
    assert retained_message(store, h, conv["id"], receipt)["text"] == raw
    assert [row["text"] for row in store.list_memory(h.admin, conv["id"])] == [raw]


def test_item13_persona_redacts_before_cap(chat_hub, monkeypatch, wire):
    h = chat_hub
    configure(h, monkeypatch, "local-dm")
    prefix = "x" * 1989 + " "
    raw = prefix + "ghp_" + "s" * 36
    expected = prefix + "[redacted:github_token]"
    store, bot, conv = conversation(h, "local-dm", instructions=raw)
    receipt = store.post_message(h.admin, conv["id"], "Orchard question", "item13-persona")
    assert h.gateway.coworker_worker.run_once(h.workspace)
    assert len(wire.calls) == 1
    system, _ = payload_parts(wire.calls[0])
    assert system == runtime.SYSTEM + expected[:2000]
    assert "ghp_" not in system
    assert job(store, h, conv["id"], receipt)["state"] == "done"
    assert next(row for row in store.list_bots() if row["id"] == bot["id"])["instructions"] == raw


def test_item13_expanded_current_still_honors_budget(chat_hub, monkeypatch, wire):
    h = chat_hub
    configure(h, monkeypatch, "local-dm")
    store, _, conv = conversation(h, "local-dm")
    raw = ("password=xxxx " * 550).strip()
    receipt = store.post_message(h.admin, conv["id"], raw, "item13-budget-current")
    assert h.gateway.coworker_worker.run_once(h.workspace)
    assert wire.calls == []
    result = job(store, h, conv["id"], receipt)
    assert (result["state"], result["code"]) == ("failed", "input_too_long")
    assert retained_message(store, h, conv["id"], receipt)["text"] == raw


@pytest.mark.parametrize("source", ["memory", "history"], ids=["I13-B02-memory", "I13-B03-history"])
def test_item13_expanded_optional_context_is_dropped(chat_hub, monkeypatch, wire, source):
    h = chat_hub
    configure(h, monkeypatch, "local-dm")
    store, _, conv = conversation(h, "local-dm")
    raw = ("password=xxxx " * 200).strip()
    if source == "memory":
        store.add_memory(h.admin, conv["id"], raw)
    else:
        prior = store.post_message(h.admin, conv["id"], raw, "item13-budget-prior")
        store.cancel_job(h.admin, prior["job"]["id"])
    current = "Orchard " + "z" * 7000
    receipt = store.post_message(h.admin, conv["id"], current, "item13-budget-optional")
    assert h.gateway.coworker_worker.run_once(h.workspace)
    assert len(wire.calls) == 1
    system, content = payload_parts(wire.calls[0])
    data = json.loads(content)
    assert data["memories" if source == "memory" else "history"] == []
    assert data["message"]["text"] == current
    assert len(system) + len(content) <= MAX_INPUT_CHARS
    assert job(store, h, conv["id"], receipt)["state"] == "done"
    if source == "memory":
        assert [row["text"] for row in store.list_memory(h.admin, conv["id"])] == [raw]
    else:
        assert retained_message(store, h, conv["id"], prior)["text"] == raw


def test_item13_memory_change_still_withholds_publication(chat_hub, monkeypatch, wire):
    h = chat_hub
    configure(h, monkeypatch, "local-dm")
    store, _, conv = conversation(h, "local-dm")
    raw = "Orchard password=fixture-only-memory"
    memory = store.add_memory(h.admin, conv["id"], raw)
    receipt = store.post_message(h.admin, conv["id"], "Recall orchard", "item13-revalidation")
    wire.on_open = lambda: store.remove_memory(h.admin, conv["id"], memory["id"])
    assert h.gateway.coworker_worker.run_once(h.workspace)
    assert len(wire.calls) == 1
    _, content = payload_parts(wire.calls[0])
    assert json.loads(content)["memories"][0]["text"] == "Orchard password=[redacted:assignment]"
    result = job(store, h, conv["id"], receipt)
    assert (result["state"], result["code"]) == ("failed", "context_changed")
    assert not any(
        row["actor"].startswith("bot:") for row in store.list_messages(h.admin, conv["id"])
    )
    assert retained_message(store, h, conv["id"], receipt)["text"] == "Recall orchard"


def test_item13_task_branch_retains_explicit_originals(chat_hub, monkeypatch, wire):
    h = chat_hub
    configure(h, monkeypatch, "local-board")
    store, _, conv = conversation(h, "local-board")
    # Isolate the durable task branch from the separate, uncommissioned runner acceptance.
    monkeypatch.setattr(runtime.orchestration, "enabled", lambda db, wid: False)
    raw_memory = "Hidden password=fixture-only-memory"
    store.add_memory(h.admin, conv["id"], raw_memory)
    title = "Orchard password=fixture-only-task"
    body = "Exact body password=fixture-only-body\n  Keep Ω.\t"
    raw = "/task " + title + "\n" + body
    receipt = store.post_message(h.admin, conv["id"], raw, "item13-task")
    assert h.gateway.coworker_worker.run_once(h.workspace)
    assert wire.calls == []
    result = job(store, h, conv["id"], receipt)
    assert (result["state"], result["code"]) == ("done", "task_created")
    card = next(
        row for row in service(h).task_list("work") if row["number"] == result["task"]["number"]
    )
    assert (card["title"], card["body"]) == (title, body)
    assert retained_message(store, h, conv["id"], receipt)["text"] == raw
    assert [row["text"] for row in store.list_memory(h.admin, conv["id"])] == [raw_memory]


def test_item13_bot_history_uses_the_same_outbound_copy(chat_hub, monkeypatch, wire):
    h = chat_hub
    configure(h, monkeypatch, "local-dm")
    store, _, conv = conversation(h, "local-dm")
    raw = "Bot original password=fixture-only-bot\n  Keep Ω.\t"
    prior = store.post_message(h.admin, conv["id"], "Prior question", "item13-bot-prior")
    claimed = store.claim_job()
    assert claimed["id"] == prior["job"]["id"]
    store.add_bot_reply(claimed["id"], raw)
    receipt = store.post_message(h.admin, conv["id"], "Recall orchard", "item13-bot-current")
    assert h.gateway.coworker_worker.run_once(h.workspace)
    assert len(wire.calls) == 1
    _, content = payload_parts(wire.calls[0])
    history = json.loads(content)["history"]
    bot_rows = [row for row in history if row["actor"].startswith("bot:")]
    assert len(bot_rows) == 1
    assert bot_rows[0]["text"] == "Bot original password=[redacted:assignment]\n  Keep Ω.\t"
    persisted = store.list_messages(h.admin, conv["id"])
    assert next(row for row in persisted if row["id"] == bot_rows[0]["id"])["text"] == raw
    assert job(store, h, conv["id"], receipt)["state"] == "done"
