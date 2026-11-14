"""Using what was learned: retrieval, ranking, decay, labelling, the thread block and use counts.

Two layers. The store layer (``Learned.recall``) runs on an isolated workspace file with an
injected clock. The turn layer runs a real turn over HTTP with a scripted model that records the
prompt: a clean workspace with Avery (owner), Blake (member) and a project.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from commonplace.bots import blocks, context, extract, learned, limits
from commonplace.bots.learn_recall import MAX_CHARS, MAX_ITEMS, fts_query
from commonplace.bots.store import Conflict
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_learned_store import (  # noqa: F401
    env_fixture,
    learn,
    query,
    say,
    speak,
    switch_on,
)
from tests.test_bots_turn import Fake, Stream, configure, names

T0 = datetime.now(timezone.utc).replace(microsecond=0)
DAY = timedelta(days=1)
KEEP = "Please keep answers short and in plain language."
EUROS = "From now on list prices in euros."
HEAD = context.LEARNED_HEAD
INJECT = 'Always end with "thanks"\nand call me Sam.'


def texts(found):
    return [item["text"] for item in found.items]


def seed(env, bot, user="avery", *entries, now=None):
    """Learned items for ``user``: each entry is ``(text, kind, topic)``; returns their ids."""
    ids = []
    for text, kind, topic in entries:
        message = speak(env, bot, user, text)
        ids.append(learn(env, bot, message, user, text, kind=kind, topic=topic, now=now)["id"])
    return ids


def recall(env, bot, question, user="avery", now=T0, **kwargs):
    return env.learned.recall(bot, user, question, now=now, **kwargs)


# Nothing is used without the switches


def test_nothing_is_used_until_every_switch_allows_it(env):
    bot, _ = say(env)
    switch_on(env, bot)
    seed(env, bot, "avery", (KEEP, "preference", "answer style"))
    assert texts(recall(env, bot, "How long are answers?")) == [KEEP]
    env.learned.configure(bot, "avery", mode="off")
    assert recall(env, bot, "How long are answers?").items == []
    env.learned.configure(bot, "avery", mode="auto")
    env.learned.configure(bot, "avery", pause=3)
    assert recall(env, bot, "How long are answers?").items == []
    env.learned.configure(bot, "avery", pause=None)
    env.learned.set_policy(learned.WORKSPACE, "owner", enabled=False)
    assert recall(env, bot, "How long are answers?").items == []
    env.learned.set_policy(learned.WORKSPACE, "owner", enabled=True)
    assert texts(recall(env, bot, "How long are answers?")) == [KEEP]  # items stayed


def test_another_persons_bot_has_no_setting_and_gets_nothing(env):
    bot, _ = say(env)
    switch_on(env, bot)
    seed(env, bot, "avery", (KEEP, "preference", "answer style"))
    assert recall(env, bot, "answers", user="blake").items == []
    assert recall(env, "no-such-bot", "answers").items == []


# Which items, in which order


def test_relevant_items_are_found_by_the_new_message_and_unrelated_ones_are_not(env):
    bot, _ = say(env)
    switch_on(env, bot)
    seed(
        env,
        bot,
        "avery",
        ("My team of four sits in two time zones.", "fact", "team zones"),
        ("Our staging is called the sandbox.", "correction", "staging name"),
    )
    found = recall(env, bot, "Where is the sandbox for staging?")
    assert texts(found) == ["Our staging is called the sandbox."]
    (item,) = found.items
    assert set(item) == {"id", "scope", "kind", "said", "text"}
    assert (item["scope"], item["kind"]) == ("person", "correction")
    # The date the message was stored (not the clock at import, which can be a day earlier).
    assert item["said"] == env.learned.get("avery", item["id"])["source_at"][:10]
    assert texts(recall(env, bot, "Plan a team meeting across zones"))[0].startswith("My team")
    assert recall(env, bot, "Order lunch").items == []  # nothing in common, no standing item


def test_stop_words_do_not_count_as_a_match(env):
    bot, _ = say(env)
    switch_on(env, bot)
    seed(env, bot, "avery", ("My team of four sits in two time zones.", "fact", "team zones"))
    assert recall(env, bot, "What are the plans for the day?").items == []
    assert fts_query("What are the plans?") == '"plans"'
    assert fts_query("") is None and fts_query(None) is None
    # Only quoted plain words ever reach the index: operators are just words.
    assert fts_query('"; DROP -- OR NEAR(') == '"drop" OR "near"'


def test_standing_preferences_come_along_without_a_word_in_common(env):
    bot, _ = say(env)
    switch_on(env, bot)
    seed(
        env,
        bot,
        "avery",
        ("Please keep answers short and in plain language.", "preference", "style one"),
        ("From now on list prices in euros.", "instruction", "currency"),
        ("Never use jargon with me.", "preference", "jargon"),
        ("Always give the date as day month year.", "instruction", "dates"),
        ("My team of four sits in two time zones.", "fact", "team zones"),
    )
    found = recall(env, bot, "Order lunch")
    assert len(found.items) == 3  # three standing items; the fact has no word in common
    assert all(i["kind"] in ("preference", "instruction") for i in found.items)
    assert found.left_out == 0  # the fourth has no word in common and is not relevant


def test_pinned_items_are_always_in_and_first(env):
    bot, _ = say(env)
    switch_on(env, bot)
    (fact,) = seed(env, bot, "avery", ("My team of four sits in two time zones.", "fact", "zones"))
    seed(env, bot, "avery", (KEEP, "preference", "answer style"))
    env.learned.update("avery", fact, pinned=True)
    found = recall(env, bot, "Order lunch")
    assert texts(found)[0].startswith("My team") and len(found.items) == 2


def test_decay_halves_weight_every_90_days_and_hides_unpinned_items_after_180(env):
    bot, _ = say(env)
    switch_on(env, bot)
    old, fresh = "Our staging is called the sandbox.", "Our staging area is the playground."
    seed(env, bot, "avery", (old, "fact", "staging old"), now=T0 - 100 * DAY)
    seed(env, bot, "avery", (fresh, "fact", "staging new"), now=T0 - 1 * DAY)
    assert (
        texts(recall(env, bot, "staging", now=T0))[0] == fresh
    )  # same word, older item ranks lower
    assert len(recall(env, bot, "staging", now=T0).items) == 2
    far = T0 + 100 * DAY  # the older item is now 200 days old, the newer one 101
    assert texts(recall(env, bot, "staging", now=far)) == [fresh]
    assert recall(env, bot, "staging", now=T0 + 400 * DAY).items == []
    assert len(query(env, "SELECT id FROM bot_learned")) == 2  # nothing was deleted


def test_pinned_items_do_not_decay_and_use_keeps_an_item_alive(env):
    bot, _ = say(env)
    switch_on(env, bot)
    (pinned, used) = seed(
        env,
        bot,
        "avery",
        ("Our staging is called the sandbox.", "fact", "staging name"),
        ("Our billing runs on the first of the month.", "fact", "billing day"),
        now=T0 - 300 * DAY,
    )
    env.learned.update("avery", pinned, pinned=True)
    found = recall(env, bot, "staging and billing")
    assert [i["id"] for i in found.items] == [pinned]
    env.bots.add_bot_message(  # a reply that used the second item refreshes it
        bot, "avery", speak(env, bot, "avery", "Question one"), "ok", learned_used=[used]
    )
    assert {i["id"] for i in recall(env, bot, "staging and billing", now=T0 + 3 * DAY).items} == {
        pinned,
        used,
    }


def test_proposed_replaced_and_foreign_items_are_never_used(env):
    bot, _ = say(env)
    switch_on(env, bot)
    seed(env, bot, "avery", ("Our staging is called the sandbox.", "fact", "staging name"))
    other, _ = say(env, "blake", "Blake's own words on staging.", name="Blake bot")
    switch_on(env, other, "blake")
    seed(env, other, "blake", ("Our staging lives in the cellar.", "fact", "staging cellar"))
    message = speak(env, bot, "avery", "Our staging is a proposal only.")
    learn(
        env, bot, message, "avery", "Our staging is a proposal only.", topic="p", state="proposed"
    )
    assert texts(recall(env, bot, "staging")) == ["Our staging is called the sandbox."]
    message = speak(env, bot, "avery", "Our staging is now called the dune.")
    learn(
        env,
        bot,
        message,
        "avery",
        "Our staging is now called the dune.",
        topic="staging name",
        dedupe=True,
    )  # replaces the first item (same topic)
    assert texts(recall(env, bot, "staging")) == ["Our staging is now called the dune."]
    assert texts(recall(env, other, "staging", user="blake")) == [
        "Our staging lives in the cellar."
    ]


def test_items_of_a_project_the_person_cannot_read_are_not_used(env):
    bot, _ = say(env)
    switch_on(env, bot)
    seed(env, bot, "avery", ("Garden budget reviews happen on Fridays.", "fact", "garden budget"))
    message = speak(env, bot, "avery", "Garden irrigation uses rain water.")
    learn(
        env,
        bot,
        message,
        "avery",
        "Garden irrigation uses rain water.",
        topic="irrigation",
        board="garden",
    )
    assert len(recall(env, bot, "garden").items) == 1  # the tagged item needs a readable project
    assert len(recall(env, bot, "garden", can_read=lambda slug: slug == "garden").items) == 2
    assert len(recall(env, bot, "garden", can_read=lambda slug: False).items) == 1


def test_the_budget_holds_whole_items_and_says_how_many_were_left_out(env):
    bot, _ = say(env)
    switch_on(env, bot)
    seed(
        env,
        bot,
        "avery",
        *[
            (f"Garden bed number {n} is watered every morning by hand.", "fact", f"bed {n}")
            for n in range(12)
        ],
    )
    found = recall(env, bot, "garden bed")
    assert len(found.items) == MAX_ITEMS and found.left_out == 12 - MAX_ITEMS
    assert sum(len(json.dumps(i)) for i in found.items) <= MAX_CHARS
    long = ("Garden notes: " + "water the beds early and keep records of rain. " * 7)[:299]
    seed(env, bot, "avery", (long, "fact", "long note"))
    sizes = [len(json.dumps(i, ensure_ascii=False)) for i in recall(env, bot, "garden").items]
    assert sum(sizes) <= MAX_CHARS


# Forget


def test_a_forgotten_item_is_gone_from_the_next_recall_and_from_the_search_index(env):
    bot, _ = say(env)
    switch_on(env, bot)
    (item,) = seed(env, bot, "avery", ("Our staging is called the sandbox.", "fact", "staging"))
    assert texts(recall(env, bot, "staging"))
    env.learned.forget("avery", item)
    assert recall(env, bot, "staging").items == []
    assert (
        query(env, "SELECT rowid FROM bot_learned_fts WHERE bot_learned_fts MATCH 'sandbox'") == []
    )
    assert query(env, "SELECT COUNT(*) FROM bot_learned") == [(0,)]


def test_a_reply_counts_use_in_the_same_write_and_a_conflict_counts_nothing(env):
    bot, _ = say(env)
    switch_on(env, bot)
    (item,) = seed(env, bot, "avery", (KEEP, "preference", "answer style"))
    message = speak(env, bot, "avery", "Question one")
    env.bots.add_bot_message(bot, "avery", message, "Done.", learned_used=[item])
    with pytest.raises(Conflict):
        env.bots.add_bot_message(bot, "avery", message, "Again.", learned_used=[item])
    stored = env.learned.get("avery", item)
    assert stored["use_count"] == 1 and stored["last_used_at"] is not None
    foreign, _ = say(env, "blake", name="Blake bot")
    env.bots.add_bot_message(  # another person's reply never touches Avery's item
        foreign, "blake", speak(env, foreign, "blake", "Hi"), "Hello.", learned_used=[item]
    )
    assert env.learned.get("avery", item)["use_count"] == 1


# The prompt of a real turn


@pytest.fixture(name="rig")
def rig_fixture(bots_hub, monkeypatch):
    fake = Fake()
    from commonplace.chat import model_stream

    monkeypatch.setattr(model_stream, "stream", fake)
    monkeypatch.setattr(extract, "after_turn", lambda run: None)  # extraction is not under test
    hub = bots_hub
    configure(hub)
    hub.fake, hub.n = fake, 0
    hub.avery, hub.blake = hub.hub.admin, hub.member_user
    hub.store_learned = lambda: learned.Learned(hub.store())
    return hub


def turn_on(rig, who="avery"):
    auth = rig.owner if who == "avery" else rig.member
    assert rig.request(rig.owner, "PATCH", "/api/learning/settings", {"enabled": True})[0] == 200
    bot = rig.by_board(auth)["general"]["id"]
    assert rig.request(auth, "PATCH", f"/api/bots/{bot}/learning", {"mode": "auto"})[0] == 200
    return bot


def teach(rig, bot, text, who="avery", **fields):
    user = rig.avery if who == "avery" else rig.blake
    rig.n += 1
    message = rig.store().add_user_message(bot, user, text, f"seed-{rig.n:04d}")["id"]
    return rig.store_learned().add_item(
        user,
        bot,
        kind=fields.pop("kind", "preference"),
        topic=fields.pop("topic", "answer style"),
        text=text,
        source_message_id=message,
        **fields,
    )["id"]


def ask(rig, bot, text, request_id, who="avery"):
    auth = rig.owner if who == "avery" else rig.member
    return Stream(rig, auth, bot, text, request_id).until_end()


def sent(rig):
    call = rig.fake.calls[-1]
    return call["system"], "\n\n".join(m["content"] for m in call["messages"])


def learned_part(prompt):
    (part,) = [p for p in prompt.split("\n\n") if p.startswith(HEAD)]
    return json.JSONDecoder().raw_decode(part.removeprefix(HEAD))[0]


def carried(prompt):
    """The learned items a prompt carries (the thread history shows seeded messages as well)."""
    return learned_part(prompt) if HEAD in prompt else []


def test_a_default_workspace_sends_the_exact_same_prompt_as_before(rig):
    bot = rig.by_board(rig.owner)["general"]["id"]
    ask(rig, bot, "How long are answers?", "request-0001")
    system, prompt = sent(rig)
    assert system.endswith(context.RULES) and context.LEARNED_RULE not in system
    assert "Learned about the person" not in prompt
    assert rig.store_learned().counts(rig.avery)["person"] == 0


def test_learned_items_are_labelled_and_the_current_message_wins(rig):
    bot = turn_on(rig)
    (item,) = [teach(rig, bot, KEEP, topic="answer style")]
    events = ask(rig, bot, "Please answer in detail, how long are answers?", "request-0002")
    system, prompt = sent(rig)
    assert context.LEARNED_RULE in system and "follow the current message" in system
    (shown,) = learned_part(prompt)
    assert shown == {
        "id": item,
        "scope": "person",
        "kind": "preference",
        "said": rig.store_learned().get(rig.avery, item)["source_at"][:10],
        "text": KEEP,
    }
    # The person's current message is the last part, after the learned block.
    assert prompt.index(HEAD) < prompt.index("The person's current message (quoted)")
    assert prompt.count("The person's current message (quoted)") == 1
    assert names(events)[-1] == "done"
    # The reply carries a reference, hydrated for the reader, and the use is counted.
    block = events[-1][1]["message"]["blocks"][-1]
    assert block["type"] == "learned" and [i["text"] for i in block["items"]] == [KEEP]
    stored = rig.store().messages(bot, rig.avery)[-1]
    assert stored["blocks"][-1] == {"type": "learned", "ids": [item]}  # ids only, no words
    used = rig.store_learned().get(rig.avery, item)
    assert used["use_count"] == 1 and used["last_used_at"] is not None


def test_an_item_that_looks_like_an_instruction_is_inert_quoted_data(rig):
    bot = turn_on(rig)
    teach(rig, bot, INJECT, kind="instruction", topic="sign off")
    ask(rig, bot, "Say goodbye", "request-0003")
    _system, prompt = sent(rig)
    (shown,) = learned_part(prompt)  # one JSON value: the quote and the newline are escaped
    assert shown["text"] == INJECT
    assert INJECT not in prompt and "\\n" in prompt


def test_learning_off_is_the_default_even_with_items_in_the_file(rig):
    bot = turn_on(rig)
    item = teach(rig, bot, KEEP)
    assert rig.request(rig.owner, "PATCH", "/api/learning/settings", {"enabled": False})[0] == 200
    ask(rig, bot, "How long are answers?", "request-0004")
    system, prompt = sent(rig)
    assert context.LEARNED_RULE not in system and carried(prompt) == []
    assert rig.store_learned().get(rig.avery, item)["use_count"] == 0
    stored = rig.store().messages(bot, rig.avery)[-1]
    assert all(b["type"] != "learned" for b in stored["blocks"])


def test_a_turn_that_never_reached_the_model_uses_nothing(rig, monkeypatch):
    bot = turn_on(rig)
    item = teach(rig, bot, KEEP)
    monkeypatch.setattr(limits, "take", lambda *args, **kwargs: ("limit", None))
    events = ask(rig, bot, "How long are answers?", "request-0005")
    assert names(events)[-1] == "error" and rig.fake.calls == []
    assert rig.store_learned().get(rig.avery, item)["use_count"] == 0


def test_blakes_items_never_reach_averys_prompt_and_the_reverse(rig):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    teach(rig, avery_bot, "My garden hose is green and long.", topic="hose", kind="fact")
    teach(
        rig, blake_bot, "My garden shed key hangs by the door.", "blake", topic="key", kind="fact"
    )
    ask(rig, avery_bot, "Where is the garden hose?", "request-0006")
    _system, prompt = sent(rig)
    assert [i["text"] for i in carried(prompt)] == ["My garden hose is green and long."]
    ask(rig, blake_bot, "Where is the garden shed?", "request-0007", "blake")
    _system, prompt = sent(rig)
    assert [i["text"] for i in carried(prompt)] == ["My garden shed key hangs by the door."]


def test_a_forgotten_item_is_gone_from_the_next_turn_and_the_thread_block(rig):
    bot = turn_on(rig)
    item = teach(rig, bot, KEEP)
    ask(rig, bot, "How long are answers?", "request-0008")
    assert rig.request(rig.owner, "DELETE", f"/api/learned/{item}")[0] in (200, 204)
    ask(rig, bot, "How long are answers now?", "request-0009")
    _system, prompt = sent(rig)
    assert carried(prompt) == [] and "Learned about the person" not in prompt
    status, body, _ = rig.get(rig.owner, f"/api/bots/{bot}/messages")
    assert status == 200
    first = [m for m in body["messages"] if m["role"] == "bot"][0]
    assert first["blocks"][-1] == {"type": "learned", "gone": True}  # a forgotten item is omitted
    assert [b for m in body["messages"] for b in m["blocks"] if b["type"] == "learned"] == [
        {"type": "learned", "gone": True}
    ]


def test_an_item_of_a_project_the_person_lost_is_not_used(rig):
    bot = turn_on(rig)
    rig.add_board("garden", "Garden")
    teach(
        rig,
        bot,
        "Garden beds are watered every morning.",
        kind="fact",
        topic="beds",
        board="garden",
    )
    ask(rig, bot, "When are the garden beds watered?", "request-0010")
    assert len(learned_part(sent(rig)[1])) == 1
    rig.archive_board("garden")
    ask(rig, bot, "When are the garden beds watered?", "request-0011")
    assert carried(sent(rig)[1]) == []


def test_the_input_budget_keeps_items_before_the_snapshot_and_earlier_messages():
    items = [
        {"id": str(n), "scope": "person", "kind": "fact", "said": "2026-01-01", "text": "w" * 200}
        for n in range(4)
    ]
    earlier = [context.quote({"from": "person", "text": "e" * 300, "at": "x"})] * 20
    ctx = context.Context(
        "system", [], {"project": "Garden"}, earlier, "current", learned_items=items, left_out=2
    )
    roomy = ctx.messages([], limit=limits.BOT_MAX_INPUT_CHARS)
    assert ctx.learned_used == [i["id"] for i in items]
    assert "(2 more left out)" in roomy[0]["content"]
    # Room for the items but little else: the history is cut first, the items stay.
    tight = len(json.dumps(items)) + 900
    messages = ctx.messages([], limit=tight)
    assert context.size("system", messages, []) <= tight
    assert ctx.learned_used == [i["id"] for i in items]
    assert messages[0]["content"].count("e" * 300) < 20
    # Less room: whole items drop from the end and the person is told how many are left out.
    few = len(json.dumps(items[:2])) + 500
    messages = ctx.messages([], limit=few)
    assert context.size("system", messages, []) <= few
    assert 0 < len(ctx.learned_used) < 4
    kept = learned_part(messages[0]["content"])
    assert [i["id"] for i in kept] == ctx.learned_used and "more left out" in messages[0]["content"]
    assert ctx.messages([], limit=10) is None and ctx.learned_used == []


def test_the_block_hydrator_omits_what_the_reader_no_longer_has(rig):
    bot = turn_on(rig)
    item = teach(rig, bot, KEEP)
    principal = rig.person(rig.hub.admin)
    reader = blocks.Reader(rig.hub.gateway, principal)
    assert (
        blocks.hydrate(reader, [{"type": "learned", "ids": [item, "nope", 7]}])[0]["items"][0]["id"]
        == item
    )
    assert blocks.hydrate(reader, [{"type": "learned", "ids": "x"}]) == [
        {"type": "learned", "gone": True}
    ]
    other = blocks.Reader(rig.hub.gateway, rig.person(rig.member_user))
    assert blocks.hydrate(other, [{"type": "learned", "ids": [item]}]) == [
        {"type": "learned", "gone": True}
    ]
