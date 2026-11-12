"""The learning extractor: switches, triggers, server-side validation, dedupe, caps and the run journal.

A clean workspace (owner Avery, member Blake, a project) with a scripted model. Time is injected
(``now``) wherever the five-minute spacing, the 60-second lease or the 14-day expiry matters.
"""

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from commonplace.bots import extract, learned, limits, modelcfg
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_turn import Fake, Stream, configure, names

WORDS = "Please always keep answers short and in plain language."
T0 = datetime.now(timezone.utc).replace(microsecond=0)
FIVE, TEN = timedelta(minutes=5), timedelta(minutes=10)
SECRET = "my token is ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2


def reply(*items):
    return [("text", json.dumps({"items": list(items)}))]


def item(message_id, quote, kind="preference", topic="answer style", **extra):
    return {"message_id": message_id, "quote": quote, "kind": kind, "topic": topic, **extra}


class Rig:
    """Avery's general bot, the store and the scripted model."""

    def __init__(self, hub, fake):
        self.hub, self.fake = hub, fake
        self.user = hub.hub.admin
        self.workspace = hub.hub.workspace
        self.bot = hub.by_board(hub.owner)["general"]["id"]
        self.bots = hub.store()
        self.learned = learned.Learned(self.bots)
        self.n = 0
        self.hooked, self.hook_done = [], threading.Event()

    def on(self, mode="auto", workspace=True):
        if workspace:
            status, body, _ = self.hub.request(
                self.hub.owner, "PATCH", "/api/learning/settings", {"enabled": True}
            )
            assert status == 200, body
        status, body, _ = self.hub.request(
            self.hub.owner, "PATCH", f"/api/bots/{self.bot}/learning", {"mode": mode}
        )
        assert status == 200, body
        return self

    def say(self, *texts) -> list[int]:
        ids = []
        for text in texts:
            self.n += 1
            ids.append(
                self.bots.add_user_message(self.bot, self.user, text, f"request-{self.n:04d}")["id"]
            )
        return ids

    def run(self, now=T0, script=None):
        if script is not None:
            self.fake.script.append(script)
        thread = extract.start(
            self.hub.hub.gateway, self.workspace, self.user, self.bots.get(self.bot, self.user), now
        )
        assert extract.idle()
        return thread

    def items(self, **filters):
        return self.learned.list(self.user, **filters)

    def runs(self):
        return self.learned.runs(self.bot)

    def asked(self):
        return [c for c in self.fake.calls if c["system"] == extract.SYSTEM]

    def sql(self, query, *args):
        conn = sqlite3.connect(self.bots.directory() / "bots.sqlite3")
        try:
            rows = conn.execute(query, args).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()


@pytest.fixture(name="rig")
def rig_fixture(bots_hub, monkeypatch):
    fake = Fake()
    from commonplace.chat import model_stream

    monkeypatch.setattr(model_stream, "stream", fake)
    configure(bots_hub)
    rig = Rig(bots_hub, fake)
    real = extract.after_turn

    def spy(run):  # what the turn engine's hook did, and when it was done deciding
        started = real(run)
        rig.hooked.append(started)
        rig.hook_done.set()
        return started

    monkeypatch.setattr(extract, "after_turn", spy)
    return rig


# Off by default: nothing is learned, nothing is asked of a model


def test_a_fresh_workspace_learns_nothing_and_asks_no_model(rig):
    (message,) = rig.say("From now on list prices in euros please.")
    assert rig.run(script=reply(item(message, "list prices in euros"))) is None
    assert rig.fake.calls == [] and rig.runs() == [] and rig.items() == []
    assert rig.sql("SELECT COUNT(*) FROM bot_learn_log") == [(0,)]


def test_a_workspace_switch_alone_does_not_turn_a_bot_on(rig):
    rig.on(mode="off")
    rig.say("From now on list prices in euros please.")
    assert rig.run() is None and rig.fake.calls == [] and rig.runs() == []


def test_a_bot_mode_without_the_workspace_switch_does_nothing(rig):
    rig.on(workspace=False)
    rig.say("From now on list prices in euros please.")
    assert rig.run() is None and rig.fake.calls == [] and rig.runs() == []


def test_a_paused_person_is_not_learned_from(rig):
    rig.on()
    rig.learned.configure(rig.bot, rig.user, pause=3)
    rig.say("From now on list prices in euros please.")
    assert rig.run() is None and rig.fake.calls == [] and rig.runs() == []
    assert rig.run(now=T0 + timedelta(days=4)) is not None  # the pause is over
    assert len(rig.asked()) == 1


def test_messages_before_the_floor_are_not_learned_from(rig):
    rig.on()
    rig.say("From now on list prices in euros please.")
    rig.learned.forget_all(rig.bot, rig.user)
    assert rig.run() is None and rig.fake.calls == [] and rig.runs() == []


def test_no_usable_model_means_nothing_is_sent_and_the_window_stays_open(bots_hub, monkeypatch):
    fake = Fake()
    from commonplace.chat import model_stream

    monkeypatch.setattr(model_stream, "stream", fake)
    rig = Rig(bots_hub, fake).on()  # no chat model is configured in this workspace
    rig.say("From now on list prices in euros please.")
    assert rig.run() is not None
    assert fake.calls == [] and rig.runs() == [] and rig.items() == []


def test_a_used_up_budget_skips_the_run_without_a_model_call(rig):
    rig.on()
    configure(rig.hub, user_daily_limit=1)
    config = modelcfg.resolve(rig.hub.hub.gateway.db, rig.workspace)[0]
    assert limits.take(rig.hub.hub.gateway, config, rig.workspace, rig.user)[0] == ""
    (message,) = rig.say("From now on list prices in euros please.")
    rig.run(script=reply(item(message, "list prices in euros")))
    assert rig.fake.calls == [] and rig.runs() == [] and rig.items() == []


# Triggers


def test_two_plain_messages_do_not_start_a_run_but_three_do(rig):
    rig.on()
    rig.say("What is on the board today?", "Show me the open work.")
    assert rig.run() is None and rig.fake.calls == []
    rig.say("And what is next?")
    assert rig.run(script=reply()) is not None
    assert len(rig.asked()) == 1


@pytest.mark.parametrize(
    "text", ["I always want prices in euros.", "Vanaf nu graag alles in het Nederlands."]
)
def test_one_message_with_a_cue_starts_a_run_in_either_language(rig, text):
    rig.on()
    rig.say(text)
    assert rig.run(script=reply()) is not None and len(rig.asked()) == 1


def test_two_runs_of_one_bot_are_five_minutes_apart(rig):
    rig.on()
    rig.say("I prefer short answers in plain language.")
    assert rig.run(script=reply()) is not None
    rig.say("From now on list prices in euros please.")
    assert rig.run(now=T0 + timedelta(minutes=4)) is None
    assert rig.run(now=T0 + FIVE + timedelta(seconds=1), script=reply()) is not None
    assert len(rig.asked()) == 2


def test_a_second_run_of_the_person_waits_while_one_is_in_flight(rig):
    rig.on()
    (message,) = rig.say("I prefer short answers in plain language.")
    assert rig.learned.claim(rig.bot, rig.user, message, T0)
    rig.say("From now on list prices in euros please.")
    assert rig.run(now=T0 + timedelta(seconds=30)) is None
    assert rig.fake.calls == []


# What the model is shown


def test_the_extractor_reads_only_the_persons_own_main_thread_messages(rig):
    rig.on()
    first, second = rig.say(WORDS, "My team of four sits in two time zones.")
    rig.bots.add_bot_message(rig.bot, rig.user, first, "BOT-REPLY-MARKER", state="ok")
    rig.bots.add_event_message(rig.bot, rig.user, "EVENT-MARKER")
    other = rig.sql(
        "INSERT INTO bot_messages (bot_id,role,text,created_at,thread_id,author_id) "
        "VALUES (?,'user','OTHER-PERSON-MARKER','2026-01-01T00:00:00Z','t1','someone-else')",
        rig.bot,
    )
    assert other == []
    rig.say("Never use jargon with me.")
    rig.run(script=reply())
    (call,) = rig.asked()
    sent = json.loads(call["messages"][0]["content"])
    assert [m["text"] for m in sent["messages"]] == [
        WORDS,
        "My team of four sits in two time zones.",
        "Never use jargon with me.",
    ]
    assert not any(
        marker in json.dumps(call["messages"]) + call["system"]
        for marker in ("BOT-REPLY", "EVENT-MARKER", "OTHER-PERSON")
    )
    assert call["tools"] == []


def test_the_input_is_redacted_and_bounded_and_carries_known_topics(rig):
    rig.on()
    first = rig.say(WORDS)[0]
    rig.run(script=reply(item(first, WORDS)))
    texts = [f"Always keep note {i} short. " + "x" * 900 for i in range(8)]
    rig.say(*texts, "Also, my token is ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2)
    rig.run(now=T0 + TEN, script=reply())
    sent = json.loads(rig.asked()[-1]["messages"][0]["content"])
    assert len(sent["messages"]) <= 6
    assert sum(len(m["text"]) for m in sent["messages"]) <= 4000
    assert sent["known_topics"] == ["answer style"]
    assert "ghp_" not in json.dumps(sent)


# Validation: the server, not the model, decides what is stored


def test_auto_learns_the_exact_quote_with_its_source(rig):
    rig.on()
    (message,) = rig.say("Hello! " + WORDS + " Thanks.")
    rig.say("Also my team of four sits in two time zones.")
    rig.run(script=reply(item(message, WORDS, note="likes brevity")))
    (stored,) = rig.items(kind="preference")
    assert stored["text"] == WORDS and stored["text_by"] == "person"
    assert stored["state"] == "active" and stored["scope"] == "person"
    assert stored["source_message_id"] == message and stored["bot_id"] == rig.bot
    assert (stored["note"], stored["note_by"]) == ("likes brevity", "bot")
    (run,) = rig.runs()
    assert (run["state"], run["n_proposed"], run["n_kept"], run["n_dropped"]) == ("done", 1, 1, 0)
    assert rig.sql("SELECT action FROM bot_learn_log WHERE item_id=?", stored["id"]) == [("learn",)]


def test_a_paraphrased_or_invented_quote_is_dropped(rig):
    rig.on()
    (message,) = rig.say(WORDS, "Show me the work.", "And the questions.")[:1]
    rig.run(
        script=reply(
            item(message, "Keep replies brief."),  # a paraphrase
            item(message, WORDS + " Really."),  # extended
            item(message, "I never want to talk to you ever again."),  # invented
        )
    )
    assert rig.items() == []
    (run,) = rig.runs()
    assert (run["state"], run["n_kept"], run["n_dropped"]) == ("done", 0, 3)


def test_a_quote_must_come_from_a_message_in_the_input(rig):
    rig.on()
    old = rig.say("My team of four sits in two time zones.")[0]
    rig.run(script=reply())
    new = rig.say("Never use jargon with me.")[0]
    bot_reply = rig.bots.add_bot_message(
        rig.bot, rig.user, new, "Your team of four sits in two time zones, I know.", state="ok"
    )["id"]
    rig.run(
        now=T0 + TEN,
        script=reply(
            item(old, "My team of four sits in two time zones."),  # processed in an earlier window
            item(bot_reply, "Your team of four sits in two time zones"),  # a bot message
            item(new + 99, "Never use jargon with me."),  # not a message at all
            item(str(new), "Never use jargon with me."),  # id of the wrong type
        ),
    )
    assert rig.items() == []
    assert rig.runs()[-1]["n_dropped"] == 4


def test_a_secret_or_a_marker_or_a_sensitive_quote_is_never_stored(rig):
    rig.on()
    secret = SECRET + " and I prefer short answers"
    texts = [
        secret,
        "Always use my IBAN NL91 ABNA 0417 1643 00 for payments.",
        "I always take my medication with breakfast every day.",
        "From now on my password is the name of my dog.",
        "Remember [redacted:token] is my key for the bot.",
    ]
    first = rig.say(*texts[:3])
    rig.run(
        script=reply(
            item(first[0], secret, topic="token"),
            item(first[1], texts[1], topic="bank"),
            item(first[2], texts[2], topic="health"),
        )
    )
    assert rig.items() == []
    second = rig.say(*texts[3:], "I always want short answers.")
    rig.run(
        now=T0 + TEN,
        script=reply(
            item(second[0], texts[3], topic="login"),
            item(second[1], texts[4], topic="k"),
            item(second[2], "I always want short answers.", topic="tone"),  # the control
        ),
    )
    assert [i["text"] for i in rig.items()] == ["I always want short answers."]
    assert "ghp_" not in str(rig.sql("SELECT * FROM bot_learn_log")) + str(rig.runs())


def test_a_scanner_error_refuses_the_item_and_the_run_goes_on(rig, monkeypatch):
    from commonplace import privacy

    rig.on()
    first, second = rig.say(WORDS, "My team of four sits in two time zones.")
    real = privacy.scan

    def flaky(text):
        if "team of four" in text:
            raise RuntimeError("scanner down")
        return real(text)

    monkeypatch.setattr(privacy, "scan", flaky)
    rig.say("I always want short answers.")
    rig.run(
        script=reply(
            item(second, "My team of four sits in two time zones.", kind="fact", topic="team"),
            item(first, WORDS),
        )
    )
    assert [i["text"] for i in rig.items()] == [WORDS]
    assert rig.runs()[0]["n_dropped"] == 1


def test_a_bad_kind_topic_or_board_tag_is_dropped(rig):
    rig.on()
    (message,) = rig.say(WORDS)
    rig.say("Show me the work.", "And the questions.")
    rig.run(
        script=reply(
            item(message, WORDS, kind="opinion"),
            item(message, WORDS, topic="   "),
            item(message, WORDS, board="no-such-project"),
        )
    )
    assert rig.items() == [] and rig.runs()[0]["n_dropped"] == 3


def test_a_readable_project_tag_is_kept_and_an_unreadable_one_is_not(rig):
    rig.on()
    (message,) = rig.say("From now on the work project ships on Fridays.")
    rig.say("Show me the work.", "And the questions.")
    rig.run(
        script=reply(
            item(message, "the work project ships on Fridays", kind="fact", board="work"),
        )
    )
    (stored,) = rig.items()
    assert stored["board"] == "work"


# Schema breaches fail the whole run


@pytest.mark.parametrize(
    "answer",
    [
        "not json at all",
        '{"items": "none"}',
        '{"items": [], "extra": 1}',
        '{"items": [{"quote": "only a quote"}]}',
        '{"items": [{"message_id": 1, "quote": "x", "kind": "fact", "topic": "t", "extra": 1}]}',
        "[]",
    ],
)
def test_an_answer_that_breaks_the_schema_drops_the_whole_run(rig, answer):
    rig.on()
    rig.say(WORDS, "Show me the work.", "And the questions.")
    rig.run(script=[("text", answer)])
    assert rig.items() == []
    (run,) = rig.runs()
    assert run["state"] == "failed:schema"


def test_a_provider_error_is_a_failed_run_with_a_code_and_never_text(rig, monkeypatch):
    from commonplace.chat import model_stream
    from commonplace.chat.types import ModelError

    def broken(*args, **kwargs):
        raise ModelError("http_error")

    monkeypatch.setattr(model_stream, "stream", broken)
    rig.on()
    rig.say(WORDS, "Show me the work.", "And the questions.")
    rig.run()
    (run,) = rig.runs()
    assert run["state"] == "failed:http_error" and rig.items() == []


# Caps


def test_at_most_three_items_per_run(rig):
    rig.on()
    text = "I prefer tea. I like short answers. My team has four people. Always use euros."
    (message,) = rig.say(text)
    quotes = [
        "I prefer tea.",
        "I like short answers.",
        "My team has four people.",
        "Always use euros.",
    ]
    rig.run(script=reply(*[item(message, q, topic=f"topic {n}") for n, q in enumerate(quotes)]))
    assert len(rig.items()) == 3
    assert [rig.runs()[0][k] for k in ("n_proposed", "n_kept", "n_dropped")] == [4, 3, 1]


def test_twenty_new_items_per_person_per_day_and_two_hundred_live(rig):
    rig.on()
    (message,) = rig.say("I prefer tea. Always use euros.")
    rig.sql(
        "INSERT INTO bot_learn_log (user_id,bot_id,item_id,action,at) "
        + " UNION ALL ".join(["SELECT ?,?,'x','learn',?"] * 20),
        *[v for _ in range(20) for v in (rig.user, rig.bot, T0.strftime("%Y-%m-%dT%H:%M:%SZ"))],
    )
    rig.run(script=reply(item(message, "I prefer tea.")))
    assert rig.items() == [] and rig.runs()[0]["n_dropped"] == 1
    rig.sql("DELETE FROM bot_learn_log")
    for n in range(200):
        rig.sql(
            "INSERT INTO bot_learned (id,user_id,bot_id,scope,kind,topic,text,source_message_id,"
            "source_at,quote_hash,state,last_seen_at,created_at) VALUES (?,?,?,'person','fact',?,"
            "'filler words here',?,?,?,'active',?,?)",
            f"id{n}", rig.user, rig.bot, f"filler {n}", message, "2026-01-01T00:00:00Z",
            f"hash{n}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )  # fmt: skip
    rig.say("Always use euros.")
    rig.run(now=T0 + TEN, script=reply(item(message, "I prefer tea.")))
    assert rig.sql("SELECT COUNT(*) FROM bot_learned") == [(200,)]
    assert rig.runs()[-1]["n_dropped"] == 1


# Dedupe


def test_a_repeated_sentence_bumps_seen_count_instead_of_adding_a_row(rig):
    rig.on()
    (first,) = rig.say(WORDS)
    rig.run(script=reply(item(first, WORDS)))
    (twice,) = rig.say("And once more. " + WORDS)
    rig.run(now=T0 + TEN, script=reply(item(twice, WORDS)))
    (only,) = rig.items()
    assert only["seen_count"] == 2 and only["source_message_id"] == first


def test_the_same_topic_with_new_words_replaces_the_old_item(rig):
    rig.on()
    (first,) = rig.say("I prefer answers in two sentences.")
    rig.run(script=reply(item(first, "I prefer answers in two sentences.")))
    (second,) = rig.say("I prefer answers in one sentence now.")
    rig.run(now=T0 + TEN, script=reply(item(second, "I prefer answers in one sentence now.")))
    items = {i["text"]: i for i in rig.items()}
    old, new = (
        items["I prefer answers in two sentences."],
        items["I prefer answers in one sentence now."],
    )
    assert old["replaced_by"] == new["id"] and new["replaced_by"] is None
    assert rig.learned.live_topics(rig.user) == ["answer style"]


def test_a_forgotten_sentence_is_not_learned_again(rig):
    rig.on()
    (first,) = rig.say(WORDS)
    rig.run(script=reply(item(first, WORDS)))
    (stored,) = rig.items()
    rig.learned.forget(rig.user, stored["id"])
    (again,) = rig.say("Once more please: " + WORDS)
    rig.run(now=T0 + TEN, script=reply(item(again, WORDS)))
    assert rig.items() == [] and rig.runs()[-1]["n_dropped"] == 1


# Ask mode


def test_ask_mode_stores_a_proposal_that_replaces_nothing_and_expires(rig):
    rig.on(mode="ask")
    (first,) = rig.say("I prefer answers in two sentences.")
    rig.run(script=reply(item(first, "I prefer answers in two sentences.")))
    (proposal,) = rig.items()
    assert proposal["state"] == "proposed"
    assert rig.learned.live_topics(rig.user) == []  # a proposal is not used yet
    rig.learned.configure(rig.bot, rig.user, mode="auto")
    (second,) = rig.say("I prefer answers in one sentence now.")
    rig.run(now=T0 + TEN, script=reply(item(second, "I prefer answers in one sentence now.")))
    states = {i["text"]: i["state"] for i in rig.items()}
    assert states == {"I prefer answers in one sentence now.": "active"}  # the proposal was dropped
    rig.learned.configure(rig.bot, rig.user, mode="ask")
    (third,) = rig.say("Never use jargon with me.")
    rig.run(now=T0 + 2 * TEN, script=reply(item(third, "Never use jargon with me.", topic="tone")))
    assert [i["state"] for i in rig.items(state="proposed")] == ["proposed"]
    rig.say("Always keep it short.")
    rig.run(now=T0 + timedelta(days=15), script=reply())
    assert rig.items(state="proposed") == []  # deleted after 14 days unanswered
    assert rig.sql("SELECT COUNT(*) FROM bot_learned WHERE state='proposed'") == [(0,)]


# The run journal


def test_an_interrupted_run_is_never_rerun(rig):
    rig.on()
    old = rig.say(WORDS)[0]
    assert rig.learned.claim(rig.bot, rig.user, old, T0)  # the process died here
    (fresh,) = rig.say("Never use jargon with me.")
    rig.run(now=T0 + TEN, script=reply(item(old, WORDS), item(fresh, "Never use jargon with me.")))
    first, second = rig.runs()
    assert (first["state"], second["state"]) == ("failed:interrupted", "done")
    sent = json.loads(rig.asked()[0]["messages"][0]["content"])
    assert [m["text"] for m in sent["messages"]] == ["Never use jargon with me."]
    assert [i["text"] for i in rig.items()] == ["Never use jargon with me."]


def test_the_same_window_cannot_be_claimed_twice(rig):
    rig.on()
    (message,) = rig.say(WORDS)
    assert rig.learned.claim(rig.bot, rig.user, message, T0) is True
    assert rig.learned.claim(rig.bot, rig.user, message, T0 + TEN) is False
    assert len(rig.runs()) == 1


def test_a_run_that_was_taken_over_stores_nothing(rig):
    rig.on()
    (message,) = rig.say(WORDS, "Show me the work.", "And the questions.")[:1]
    gate = threading.Event()
    rig.fake.script.append([gate, *reply(item(message, WORDS))])
    plan = rig.learned.plan(rig.bot, rig.user, T0)
    bot = rig.bots.get(rig.bot, rig.user)
    worker = threading.Thread(
        target=extract._run, args=(rig.hub.hub.gateway, rig.workspace, rig.user, bot, plan, T0)
    )
    worker.start()
    for _ in range(500):
        if rig.runs():
            break
        time.sleep(0.01)
    rig.sql("UPDATE bot_learn_runs SET state='failed:interrupted'")
    gate.set()
    worker.join(5)
    assert rig.items() == []


# The turn hook


def talk(rig, text, request_id):
    return Stream(rig.hub, rig.hub.owner, rig.bot, text, request_id)


def test_a_finished_turn_learns_after_done_without_holding_the_reply(rig):
    rig.on()
    rig.fake.script = [[("text", "Will do.")]]
    stream = talk(rig, "From now on list prices in euros please.", "request-9001")
    ack = stream.next()[1]["message"]["id"]
    hold = threading.Event()
    rig.fake.script.append([hold, *reply(item(ack, "list prices in euros", topic="currency"))])
    events = stream.until_end()
    assert set(names(events)[:-1]) == {"delta"} and names(events)[-1] == "done"
    assert events[-1][1]["timing"]["first_token_ms"] is not None
    assert rig.hook_done.wait(3)
    (worker,) = rig.hooked
    assert worker.is_alive() and rig.items() == []  # the reply is complete; the extractor waits
    hold.set()
    worker.join(5)
    (stored,) = rig.items()
    assert (stored["text"], stored["source_message_id"]) == ("list prices in euros", ack)
    assert len(rig.asked()) == 1


def test_a_turn_with_learning_off_makes_exactly_the_chat_call(rig):
    rig.fake.script = [[("text", "Fine.")]]
    talk(rig, "From now on list prices in euros please.", "request-9002").until_end()
    assert rig.hook_done.wait(3) and rig.hooked == [None]  # asked, answered no, no thread
    assert len(rig.fake.calls) == 1 and rig.asked() == [] and rig.runs() == []
    assert rig.sql("SELECT COUNT(*) FROM bot_learn_log") == [(0,)]


def test_a_replayed_request_never_reaches_the_extractor(rig, monkeypatch):
    rig.on()
    seen = []
    monkeypatch.setattr(extract, "after_turn", lambda run: seen.append(run.request_id))
    rig.fake.script = [[("text", "Fine.")]]
    for _ in range(2):  # the second request is a replay of the first
        talk(rig, "From now on list prices in euros please.", "request-9003").until_end()
    assert seen == ["request-9003"] and len(rig.fake.calls) == 1


def test_a_failed_turn_does_not_reach_the_extractor(rig, monkeypatch):
    rig.on()
    monkeypatch.setattr(limits, "BOT_TURN_TIMEOUT_S", 0.3)
    rig.fake.script = [[("text", "Partial."), "hang"]]
    events = talk(rig, "From now on list prices in euros please.", "request-9004").until_end()
    assert names(events)[-1] == "error"
    assert not rig.hook_done.wait(0.3)  # the engine never called the hook
    assert rig.asked() == [] and rig.runs() == []
