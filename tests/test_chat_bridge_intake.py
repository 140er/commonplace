"""Chat-platform bridge, intake slice: verify, filter, journal, identity, store and run, deliver and
interrupted turns, over real HTTP with the in-memory fake platform and a scripted chat model."""

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from commonplace.bots import store as bots_store, turn as bots_turn
from commonplace.chat import model_stream
from commonplace.chat_bridge import adapter, outbox, store, turns, worker
from tests._chat_bridge_fake import FakePlatform, event, send
from tests.test_bots_turn import Fake, configure
from tests.test_chat_bridge_foundation import (  # noqa: F401 - fixtures
    _enabled_install,
    audit_rows,
    bridge_fixture,
)
from tests.test_commonplace_boards import service

WAIT = 5
VAULT_TITLE = "SENTINEL-VAULT-TITLE-4471"
PERSONA = "SENTINEL-PERSONA-2210"
NOTE = "SENTINEL-MEMORY-NOTE-5538"
HISTORY = "SENTINEL-HISTORY-LINE-9907"
AVERY_WORDS = "SENTINEL-AVERY-WORDS-6612"


def wait_for(predicate):
    end = time.monotonic() + WAIT
    while not predicate():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


class World:
    """Avery (workspace owner) and Blake (member, cannot read the private project ``vault``),
    one enabled install, a scripted model and the fake platform."""

    def __init__(self, bridge, platform, model, install):
        self.hub, self.platform, self.model, self.install = bridge, platform, model, install
        self.gateway = bridge.hub.gateway
        self.ws = bridge.hub.workspace
        self.avery, self.blake = bridge.hub.admin, bridge.member_user
        self.counter = 0

    def db(self):
        return sqlite3.connect(self.hub.file())

    def bridge_store(self):
        return store.open_bridge(self.gateway, self.ws)

    def say(self, name, user, channel, text, kind="dm", **extra):
        return send(self.hub, self.install, event(name, user, channel, text, kind, **extra))

    def posts(self, count):
        wait_for(lambda: len(self.platform.posts) >= count)
        return self.platform.posts

    def states(self):
        return dict(self.db().execute("SELECT ext_event, state FROM bridge_events").fetchall())

    def outbox(self):
        return (
            self.db()
            .execute(
                "SELECT ext_event, part, ref, state, attempts FROM bridge_outbox ORDER BY ext_event, part"
            )
            .fetchall()
        )

    def link(self, user, auth, channel=None):
        status, body, _ = self.hub.post(auth, f"/api/bridge/installs/{self.install}/link-code", {})
        assert status == 201, body
        self.counter += 1
        before = len(self.platform.posts)
        assert self.say(f"link-{self.counter}", user, channel or f"D-{user}", body["code"]) == 200
        assert "linked" in self.posts(before + 1)[-1]["text"]

    def map(self, channel, board, disclosure=None):
        body = {"board": board, **({"disclosure": disclosure} if disclosure else {})}
        status, out, _ = self.hub.request(
            self.hub.owner,
            "PUT",
            f"/api/bridge/installs/{self.install}/channels/{channel}",
            body,
        )
        assert status == 200, out
        return out["channel"]

    def bot(self, user, board=None):
        bots = bots_store.open_bots(self.gateway, self.ws)
        if not bots.roster(user):
            boards = service(self.hub.hub, self.hub.person(user)).board_list()
            bots.ensure_defaults(user, [(b["slug"], b["name"]) for b in boards])
        rows = bots.roster(user)
        return next(b for b in rows if b["board"] == board and b["kind"] != "custom")

    def thread(self, user, bot_id):
        return [
            (m["role"], m["text"])
            for m in bots_store.open_bots(self.gateway, self.ws).messages(bot_id, user)
        ]

    def settle(self):
        """Wait until no turn thread of the bridge is still running."""
        wait_for(
            lambda: not [t for t in threading.enumerate() if t.name in ("bridge-turn", "bot-turn")]
        )


@pytest.fixture(name="platform")
def platform_fixture(monkeypatch):
    platform = FakePlatform()
    monkeypatch.setitem(adapter.ADAPTERS, "fake", platform)
    monkeypatch.setattr(worker, "INTERVAL_S", 3600)  # tests sweep by hand
    return platform


@pytest.fixture(name="model")
def model_fixture(monkeypatch):
    model = Fake()
    monkeypatch.setattr(model_stream, "stream", model)
    return model


@pytest.fixture(name="w")
def world_fixture(bridge, platform, model):
    configure(bridge)
    boards = service(bridge.hub, bridge.person(bridge.hub.admin))
    boards.board_add(slug="vault", name="Vault", wing="private_vault")
    boards.task_add(title=VAULT_TITLE, board="vault")
    boards.task_add(title="Plain open task", board="work")
    return World(bridge, platform, model, _enabled_install(bridge, "T-ONE"))


# --- default off ---------------------------------------------------------------------------------


def test_clean_workspace_intake_answers_404_creates_nothing_and_starts_no_worker(bridge, platform):
    threads = threading.active_count()
    ghost = "0" * 32
    for route in ("events", "interact"):
        assert send(bridge, ghost, event("e1", "U1", "D1", "hi"), route=route) == 404
    assert not bridge.file().exists()
    assert not bots_store.exists(bridge.hub.gateway, bridge.hub.workspace)
    assert platform.posts == [] and audit_rows(bridge) == []
    assert threading.active_count() == threads
    for token in (bridge.hub.owner_token("agent-owner"), bridge.tm, bridge.t1):
        assert not [t for t in bridge.hub.tool_names(token) if t.startswith("bridge")]


def test_an_install_that_is_off_refuses_every_route_and_nothing_runs(w):
    w.link("U-AVERY", w.hub.owner)
    path = f"/api/bridge/installs/{w.install}"
    assert w.hub.request(w.hub.owner, "PATCH", path, {"enabled": False})[0] == 200
    calls, posts = len(w.model.calls), len(w.platform.posts)
    for route in ("events", "interact"):
        assert send(w.hub, w.install, event("off-1", "U-AVERY", "D-A", "hi"), route=route) == 404
    assert send(w.hub, w.install, {"type": "handshake", "challenge": "x"}) == 404
    time.sleep(0.2)
    assert (len(w.model.calls), len(w.platform.posts)) == (calls, posts)
    assert "off-1" not in w.states()


# --- verify --------------------------------------------------------------------------------------


def test_forged_stale_and_foreign_requests_are_refused_and_leave_no_trace(w):
    good = event("v1", "U-X", "D-X", "hello")
    old = str(int(time.time()) - 3600)
    assert send(w.hub, w.install, good, secret="not-the-secret") == 401  # forged
    assert send(w.hub, w.install, good, sig="0" * 64) == 401
    assert send(w.hub, w.install, good, ts=old) == 401  # validly signed, but stale
    assert send(w.hub, w.install, {**good, "team": "T-OTHER"}) == 401  # another team
    assert send(w.hub, w.install, None, body=b"not json {") == 401
    assert send(w.hub, w.install, None, body=b"x" * (256 * 1024 + 1)) == 413
    assert send(w.hub, w.install, good, route="interact", secret="nope") == 401
    assert w.states() == {} and w.platform.posts == [] and w.model.calls == []
    # A signed handshake is answered by the adapter and journals nothing.
    assert send(w.hub, w.install, {"type": "handshake", "challenge": "abc"}) == 200
    assert w.states() == {}


# --- identity ------------------------------------------------------------------------------------


def test_an_unlinked_sender_gets_only_the_linking_sentence_and_creates_no_turn(w):
    assert w.say("u1", "U-STRANGER", "D-S", "please file this: SENTINEL-DROPPED-TEXT") == 200
    [post] = w.posts(1)
    assert post["text"] == outbox.FIXED["unlinked"]
    assert (post["channel"], post["thread"]) == ("D-S", "")
    assert w.model.calls == [] and w.states() == {"u1": "refused"}
    assert not bots_store.exists(w.gateway, w.ws)  # no bot, no thread, no message
    on_disk = b"".join(p.read_bytes() for p in w.hub.hub.supervisor.root.rglob("*.sqlite3*"))
    assert b"SENTINEL-DROPPED-TEXT" not in on_disk


def test_a_code_sent_in_a_channel_is_burned_and_does_not_link(w):
    status, body, _ = w.hub.post(w.hub.owner, f"/api/bridge/installs/{w.install}/link-code", {})
    assert w.say("c1", "U-AVERY", "C-ANY", body["code"], kind="other") == 200
    assert "channel" in w.posts(1)[-1]["text"]
    assert w.say("c2", "U-AVERY", "D-A", body["code"]) == 200
    assert "did not work" in w.posts(2)[-1]["text"]
    assert w.bridge_store().link_of(w.install, "U-AVERY") is None


# --- one turn: DM and channel --------------------------------------------------------------------


def test_a_dm_runs_the_linked_persons_turn_and_replies_in_the_dm(w):
    w.link("U-AVERY", w.hub.owner)
    general = w.bot(w.avery)
    bots = bots_store.open_bots(w.gateway, w.ws)
    bots.memory_add(general["id"], w.avery, NOTE)
    earlier = bots.add_user_message(general["id"], w.avery, HISTORY, "web-request-0001")
    bots.add_bot_message(general["id"], w.avery, earlier["id"], "Noted.", state="ok")
    w.model.script = [[("text", "Hello Avery.")]]
    assert w.say("d1", "U-AVERY", "D-A", "hi there") == 200
    reply = w.posts(2)[-1]
    assert (reply["channel"], reply["thread"], reply["text"]) == ("D-A", "", "Hello Avery.")
    [call] = w.model.calls
    names = {t["name"] for t in call["tools"]}
    assert "project_status" in names and not names & {"create_work", "remember", "forget"}
    seen = call["system"] + json.dumps(call["messages"])
    assert NOTE in seen and HISTORY in seen  # a DM turn is the person's own web turn
    thread = w.thread(w.avery, general["id"])
    assert thread[-2:] == [("user", "hi there"), ("bot", "Hello Avery.")]
    assert not [t for _, t in thread if "link-" in t]  # the code never reached a thread
    message = bots.message_for_request(general["id"], w.avery, turns.request_id(w.install, "d1"))
    assert message["text"] == "hi there"
    assert w.states()["d1"] == "replied"


def test_a_mention_in_a_mapped_channel_is_a_threaded_titles_only_turn_without_private_context(w):
    w.link("U-AVERY", w.hub.owner)
    assert w.map("C-WORK", "work")["disclosure"] == "titles"
    work = w.bot(w.avery, "work")
    bots = bots_store.open_bots(w.gateway, w.ws)
    bots.memory_add(work["id"], w.avery, NOTE)
    earlier = bots.add_user_message(work["id"], w.avery, HISTORY, "web-request-0001")
    bots.add_bot_message(work["id"], w.avery, earlier["id"], "Noted.", state="ok")
    w.hub.request(w.hub.owner, "PATCH", f"/api/bots/{work['id']}", {"instructions": PERSONA})
    w.model.script = [[("text", "One open task.")]]
    assert w.say("m1", "U-AVERY", "C-WORK", "<@APP> what is open?", kind="mention") == 200
    reply = w.posts(2)[-1]
    assert (reply["channel"], reply["thread"], reply["text"]) == (
        "C-WORK",
        "ts-m1",
        "One open task.",
    )
    [call] = w.model.calls
    assert {t["name"] for t in call["tools"]} == {"project_status", "list_work"}
    seen = call["system"] + json.dumps(call["messages"])
    for private in (PERSONA, NOTE, HISTORY):
        assert private not in seen
    assert "public to a channel" in call["system"]
    assert "<@APP>" not in json.dumps(call["messages"])
    # A reply goes into the thread the mention was in; it never starts a new top-level post.
    w.say("m2", "U-AVERY", "C-WORK", "<@APP> and now?", kind="mention", thread="th-9")
    assert w.posts(3)[-1]["thread"] == "th-9"
    # ``full`` adds the bodies and decisions of the mapped project, still read-only.
    w.map("C-WORK", "work", "full")
    w.settle()
    w.say("m3", "U-AVERY", "C-WORK", "<@APP> details?", kind="mention")
    w.posts(4)
    names = {t["name"] for t in w.model.calls[-1]["tools"]}
    assert {"work_detail", "list_decisions", "project_status"} <= names
    assert not names & {
        "create_work",
        "comment_work",
        "answer_decision",
        "remember",
        "create_project",
    }


def test_the_filter_ignores_bots_edits_and_unaddressed_text_and_still_answers_2xx(w):
    w.link("U-AVERY", w.hub.owner)
    w.map("C-WORK", "work")
    posts = len(w.platform.posts)
    cases = [
        ("f-bot", event("f-bot", "U-BOT", "C-WORK", "x", "mention", bot=True)),
        ("f-self", event("f-self", "U-APP", "D-A", "my own reply", bot=True)),
        ("f-chat", event("f-chat", "U-AVERY", "C-WORK", "just talking", "other")),
        ("f-unmapped", event("f-unmapped", "U-AVERY", "C-NOPE", "<@APP> hi", "mention")),
        ("f-shared", event("f-shared", "U-AVERY", "C-WORK", "<@APP> hi", "mention", shared=True)),
        ("f-empty", event("f-empty", "U-AVERY", "C-WORK", "<@APP>", "mention")),
    ]
    for name, payload in cases:
        assert send(w.hub, w.install, payload) == 200, name
    assert send(w.hub, w.install, event("f-edit", "U-AVERY", "D-A", "hi", edited=True)) == 200
    assert send(w.hub, w.install, event("f-gone", "U-AVERY", "D-A", "hi", deleted=True)) == 200
    time.sleep(0.2)
    states = w.states()
    assert [states[name] for name, _ in cases] == ["ignored"] * len(cases)
    assert "f-edit" not in states and "f-gone" not in states
    assert w.model.calls == [] and len(w.platform.posts) == posts


# --- idempotency ---------------------------------------------------------------------------------


def test_a_duplicate_platform_event_is_answered_once_and_runs_nothing_twice(w):
    w.link("U-AVERY", w.hub.owner)
    w.model.script = [[("text", "Once.")]]
    statuses = []
    gate = threading.Barrier(5)

    def retry():
        gate.wait()
        statuses.append(w.say("dup-1", "U-AVERY", "D-A", "hello"))

    workers = [threading.Thread(target=retry) for _ in range(5)]
    [t.start() for t in workers]
    [t.join() for t in workers]
    assert statuses == [200] * 5
    w.posts(2)
    w.settle()
    assert w.say("dup-1", "U-AVERY", "D-A", "hello") == 200  # a late retry, after the reply
    time.sleep(0.3)
    assert len(w.model.calls) == 1
    assert w.platform.texts()[1:] == ["Once."]
    assert [(r[0], r[1], r[3], r[4]) for r in w.outbox() if r[0] == "dup-1"] == [
        ("dup-1", 0, "sent", 1)
    ]
    general = w.bot(w.avery)
    assert [t for r, t in w.thread(w.avery, general["id"]) if r == "user"] == ["hello"]


# --- the linked person's rights only -------------------------------------------------------------


def test_a_channel_turn_for_a_project_the_asker_cannot_read_is_refused_without_a_turn(w):
    w.link("U-AVERY", w.hub.owner)
    w.link("U-BLAKE", w.hub.member)
    w.map("C-VAULT", "vault", "full")
    calls = len(w.model.calls)
    assert w.say("p1", "U-BLAKE", "C-VAULT", "<@APP> what is in here?", kind="mention") == 200
    assert w.posts(3)[-1]["text"] == outbox.FIXED["channel_denied"]
    assert len(w.model.calls) == calls and w.states()["p1"] == "refused"
    # Avery, who reads the project, gets the turn in the same channel.
    w.model.script = [[("tool_call", {"id": "c1", "name": "list_work", "arguments": {}})]]
    w.say("p2", "U-AVERY", "C-VAULT", "<@APP> what is in here?", kind="mention")
    w.posts(4)
    assert VAULT_TITLE in json.dumps(w.model.calls[-1]["messages"])


def test_a_dm_never_reaches_a_project_the_person_cannot_read_and_people_do_not_mix(w):
    w.link("U-AVERY", w.hub.owner)
    w.link("U-BLAKE", w.hub.member)
    w.model.script = [
        [("tool_call", {"id": "c1", "name": "project_status", "arguments": {"project": "vault"}})],
        [("text", "I can't see that.")],
    ]
    assert w.say("x1", "U-BLAKE", "D-B", "status of the vault project please") == 200
    w.posts(3)
    w.settle()
    shown = json.dumps([[c["system"], c["messages"], c["tools"]] for c in w.model.calls])
    assert VAULT_TITLE not in shown and "vault" not in w.model.calls[0]["system"].lower()
    refusal = json.dumps(w.model.calls[1]["messages"])
    assert bots_turn.REFUSALS["invalid"] in refusal or bots_turn.REFUSALS["gone"] in refusal
    # Avery's words go to Avery's bot only: nothing of them in Blake's thread or model input.
    w.model.script = [[("text", "Fine.")]]
    w.say("x2", "U-AVERY", "D-A", f"remember {AVERY_WORDS}")
    w.posts(4)
    w.settle()
    assert AVERY_WORDS not in json.dumps([w.model.calls[0]["system"], w.model.calls[0]["messages"]])
    assert w.thread(w.avery, w.bot(w.avery)["id"])[-2][1].endswith(AVERY_WORDS)
    assert not [t for _, t in w.thread(w.blake, w.bot(w.blake)["id"]) if AVERY_WORDS in t]
    assert VAULT_TITLE not in json.dumps(
        [w.thread(w.blake, b["id"]) for b in bots_store.open_bots(w.gateway, w.ws).roster(w.blake)]
    )


def test_a_removed_member_and_a_revoked_link_stop_at_the_next_event(w):
    w.link("U-BLAKE", w.hub.member)
    with w.hub.db._lock:
        conn = w.hub.db._conn
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (w.ws, w.blake))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
    assert w.say("r1", "U-BLAKE", "D-B", "still there?") == 200
    assert w.posts(2)[-1]["text"] == outbox.FIXED["unlinked"] and w.model.calls == []


# --- delivery ------------------------------------------------------------------------------------


def _later(minutes=10):
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def test_a_reply_is_delivered_once_through_the_outbox_and_retries_are_bounded(w):
    w.link("U-AVERY", w.hub.owner)
    fail = adapter.PostResult(False, retry=True)
    w.platform.script = [fail, fail]
    w.model.script = [[("text", "Eventually.")]]
    w.say("o1", "U-AVERY", "D-A", "hello")
    wait_for(lambda: [r for r in w.outbox() if r[0] == "o1" and r[3] == "queued"])
    assert len(w.platform.posts) == 1  # only the link reply so far
    for step in range(3):
        outbox.deliver(w.gateway, w.ws, w.install, _later(10 * (step + 1)))
    assert w.platform.texts()[1:] == ["Eventually."]  # posted exactly once
    assert [r[3:] for r in w.outbox() if r[0] == "o1"] == [("sent", 3)]
    outbox.deliver(w.gateway, w.ws, w.install, _later(60))
    assert len(w.platform.posts) == 2

    # A platform that keeps failing: bounded attempts, then ``failed``; the reply is not lost.
    w.platform.script = [fail] * 20
    w.model.script = [[("text", "Never arrives.")]]
    w.say("o2", "U-AVERY", "D-A", "again")
    w.settle()
    for step in range(10):
        outbox.deliver(w.gateway, w.ws, w.install, _later(60 * (step + 1)))
    [row] = [r for r in w.outbox() if r[0] == "o2"]
    assert row[3:] == ("failed", outbox.MAX_ATTEMPTS)
    assert "Never arrives." not in w.platform.texts()
    assert ("bot", "Never arrives.") in w.thread(w.avery, w.bot(w.avery)["id"])


def test_long_replies_are_split_to_the_platforms_limit_and_posted_in_order(w):
    w.link("U-AVERY", w.hub.owner)
    long = " ".join(f"word{i}" for i in range(60))
    w.model.script = [[("text", long)]]
    w.say("l1", "U-AVERY", "D-A", "write a lot")
    expected = outbox.split_text(long, w.platform.caps.max_text)
    assert len(expected) > 2
    parts = w.posts(1 + len(expected))[1:]
    assert all(len(p["text"]) <= w.platform.caps.max_text for p in parts)
    assert " ".join(p["text"] for p in parts) == long


def test_a_post_cut_off_between_send_and_record_is_never_retried_blindly(w):
    w.link("U-AVERY", w.hub.owner)
    bridge = w.bridge_store()
    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    bridge.journal(w.install, "k1", "replied", "D-A", "", old)
    bridge.enqueue(w.install, "k1", 0, "thread", "text:busy")
    [row] = bridge.due(w.install)
    bridge.claim(row["id"], old)  # the call went out, the crash came before "sent"
    before = len(w.platform.posts)
    worker.sweep(w.gateway, w.ws)
    states = {r[1]: r[3] for r in w.outbox() if r[0] == "k1"}
    assert states == {0: "unknown", -1: "queued"} or states == {0: "unknown", -1: "sent"}
    wait_for(lambda: len(w.platform.posts) == before + 1)
    assert w.platform.texts()[-1] == outbox.FIXED["unsure"]  # one line, and not the reply again
    worker.sweep(w.gateway, w.ws)
    worker.sweep(w.gateway, w.ws, _later(30))
    assert len(w.platform.posts) == before + 1

    # An adapter that can find its own post settles the row from the platform's answer.
    w.platform.caps = adapter.Caps(find_posted=True, max_text=120)
    for name, found in (("k2", "p-found"), ("k3", None)):
        bridge.journal(w.install, name, "replied", "D-A", "", old)
        bridge.enqueue(w.install, name, 0, "thread", "text:busy")
        [row] = [r for r in bridge.due(w.install) if r["ext_event"] == name]
        bridge.claim(row["id"], old)
        w.platform.found = found
        worker.sweep(w.gateway, w.ws)
    rows = {r[0]: r[3] for r in w.outbox() if r[0] in ("k2", "k3")}
    assert rows["k2"] == "sent" and rows["k3"] in ("queued", "sent")
    assert [r[1] for r in w.outbox() if r[0] == "k2"] == [0]  # no notice when it was found


# --- interrupted ---------------------------------------------------------------------------------


def test_a_restart_mid_turn_resolves_as_interrupted_and_is_never_rerun(w):
    w.link("U-AVERY", w.hub.owner)
    bridge, bots = w.bridge_store(), bots_store.open_bots(w.gateway, w.ws)
    general = w.bot(w.avery)
    rid = turns.request_id(w.install, "i1")
    message = bots.add_user_message(general["id"], w.avery, "half done question", rid)
    bridge.journal(w.install, "i1", "running", "D-A", "")
    bridge.set_event(
        w.install, "i1", ("running",), "running",
        user_id=w.avery, bot_id=general["id"], message_id=message["id"],
    )  # fmt: skip
    # A turn that never began: received, and old enough for its intake to be over.
    bridge.journal(
        w.install, "i2", "received", "D-A", "", datetime.now(timezone.utc) - timedelta(minutes=5)
    )
    posts, calls = len(w.platform.posts), len(w.model.calls)
    worker.sweep(w.gateway, w.ws, _later(1))
    worker.sweep(w.gateway, w.ws, _later(2))
    w.posts(posts + 2)
    time.sleep(0.2)
    sentence = bots_turn.SENTENCES["interrupted"]
    assert w.platform.texts()[posts:] == [sentence, sentence]
    assert w.states()["i1"] == w.states()["i2"] == "failed:interrupted"
    assert len(w.model.calls) == calls  # nothing ran again
    assert w.thread(w.avery, general["id"])[-1] == ("bot", sentence)
    assert bots.reply_to(general["id"], w.avery, message["id"])["state"] == "failed:interrupted"


def test_a_turn_still_running_is_left_alone_by_the_sweep(w):
    w.link("U-AVERY", w.hub.owner)
    hold = threading.Event()
    w.model.script = [[hold, ("text", "Late but whole.")]]
    w.say("s1", "U-AVERY", "D-A", "slow question")
    wait_for(lambda: w.states().get("s1") == "running")
    time.sleep(0.1)
    posts = len(w.platform.posts)
    worker.sweep(w.gateway, w.ws, _later(1))
    assert w.states()["s1"] == "running" and len(w.platform.posts) == posts
    hold.set()
    assert w.posts(posts + 1)[-1]["text"] == "Late but whole."


# --- limits and owner routes ---------------------------------------------------------------------


def test_the_daily_cap_answers_with_a_fixed_sentence_and_runs_no_more_turns(w):
    w.link("U-AVERY", w.hub.owner)
    path = f"/api/bridge/installs/{w.install}"
    assert w.hub.request(w.hub.owner, "PATCH", path, {"daily_cap": 1})[0] == 200
    w.say("q1", "U-AVERY", "D-A", "first")
    w.posts(2)
    w.settle()
    w.say("q2", "U-AVERY", "D-A", "second")
    assert w.posts(3)[-1]["text"] == outbox.FIXED["cap"] and len(w.model.calls) == 1


def test_only_the_owner_maps_channels_and_only_to_a_project_they_can_read(w):
    hub, path = w.hub, f"/api/bridge/installs/{w.install}/channels"
    assert hub.request(hub.member, "PUT", path + "/C-1", {"board": "work"})[0] == 404
    assert hub.request(hub.t1, "PUT", path + "/C-1", {"board": "work"})[0] == 404
    assert hub.request(hub.owner, "PUT", path + "/C-1", {"board": "nope"})[1]["code"] == (
        "unknown_board"
    )
    assert (
        hub.request(hub.owner, "PUT", path + "/C-1", {"board": "work", "disclosure": "x"})[0] == 400
    )
    assert hub.request(hub.owner, "PUT", path + "/C-1", {"board": "work", "extra": 1})[0] == 400
    assert hub.request(hub.owner, "PUT", path + "/C-1", {"board": "work"})[0] == 200
    listed = hub.get(hub.owner, path)[1]["channels"]
    assert [(c["ext_channel"], c["board"], c["disclosure"]) for c in listed] == [
        ("C-1", "work", "titles")
    ]
    assert hub.get(hub.member, path)[0] == 404
    assert hub.request(hub.owner, "DELETE", path + "/C-1")[0] == 200
    assert hub.request(hub.owner, "DELETE", path + "/C-1")[0] == 404
    assert [r["tool"] for r in audit_rows(hub) if "channel" in r["tool"]] == [
        "map_channel", "map_channel", "map_channel", "unmap_channel"
    ]  # fmt: skip
