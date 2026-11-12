"""The shared scope of the learning memory: switches, the tap, visibility, deletion, forgetting.

A clean workspace: Avery (owner), Blake (member), a project ``work`` both read and a private
project ``vault`` only Avery reads. A scripted model records the prompts.
"""

import json

import pytest

from commonplace.bots import blocks, context, learned
from commonplace.bots.learn_recall import MAX_SHARED
from commonplace.bots.learn_share import SHARED_CAP
from commonplace.bots.store import Conflict, Invalid, Missing
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_learned_store import (  # noqa: F401
    env_fixture,
    learn,
    query,
    say,
    speak,
    switch_on,
)
from tests.test_bots_learning_recall import (  # noqa: F401
    ask as ask_bot,
    carried,
    rig_fixture,
    sent,
    teach,
    turn_on,
)
from tests.test_bots_turn import call, names
from tests.test_commonplace_boards import service

HOSE = "My garden hose is green and long."
SHED = "Our garden shed key hangs by the door."


def settings(rig, **body):
    status, reply, _ = rig.request(rig.owner, "PATCH", "/api/learning/settings", body)
    assert status == 200, reply


def project(rig, slug="work", on=True):
    status, reply, _ = rig.request(
        rig.owner, "PATCH", f"/api/projects/{slug}/learning", {"shared_enabled": on}
    )
    assert status == 200, reply


def all_on(rig, slug="work"):
    settings(rig, enabled=True, shared_enabled=True)
    project(rig, slug)


def share(rig, item, who="avery", **body):
    auth = rig.owner if who == "avery" else rig.member
    return rig.request(auth, "POST", f"/api/learned/{item}/share", body)


def tap(rig, bot, pending, who="avery", accept=True):
    auth = rig.owner if who == "avery" else rig.member
    return rig.request(
        auth, "POST", f"/api/bots/{bot}/confirm", {"pending": pending, "accept": accept}
    )


def shared_in(rig, who="blake", slug="work"):
    auth = rig.owner if who == "avery" else rig.member
    status, body, _ = rig.get(auth, f"/api/projects/{slug}/learned")
    return status, body.get("items")


def rows(rig, sql="SELECT scope,user_id,board,text FROM bot_learned ORDER BY seq"):
    store = rig.store()
    with store._tx(write=False) as conn:
        return [tuple(r) for r in conn.execute(sql)]


def shared_item(rig, bot, text=HOSE, who="avery", slug="work", **fields):
    """A person item shared into ``slug`` through the real chip and tap; returns (item, copy)."""
    item = teach(rig, bot, text, who, kind="fact", topic="hose", **fields)
    status, chip, _ = share(rig, item, who, project=slug)
    assert status == 200, chip
    status, reply, _ = tap(rig, bot, chip["pending"], who)
    assert status == 200, reply
    user = rig.avery if who == "avery" else rig.blake
    (copy,) = [
        r[0]
        for r in rows(
            rig, f"SELECT id FROM bot_learned WHERE shared_from='{item}' AND user_id='{user}'"
        )
    ]
    return item, copy


def vault(rig):
    service(rig.hub, rig.person(rig.hub.admin)).board_add(
        slug="vault", name="Vault", wing="private"
    )


# Nothing is shared by default


def test_sharing_is_off_by_default_and_nothing_is_offered(rig):
    bot = turn_on(rig)
    item = teach(rig, bot, HOSE, kind="fact", topic="hose")
    ask_bot(rig, bot, "Where is the garden hose?", "request-0100")
    assert "share_learned" not in {t["name"] for t in rig.fake.calls[-1]["tools"]}
    status, reply, _ = share(rig, item, project="work")
    assert status == 409 and reply["code"] == "not_shareable", reply
    assert share(rig, item)[0] == 400  # no project named and none on the item
    assert shared_in(rig, "avery") == (200, [])
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned WHERE scope='shared'") == [(0,)]
    assert rows(rig, "SELECT COUNT(*) FROM bot_pending") == [(0,)]


@pytest.mark.parametrize(
    "workspace,shared,project_on,sentence",
    [
        (False, False, False, "Learning is switched off"),
        (True, False, False, "Sharing learned items is switched off for this workspace"),
        (True, True, False, "switched off for that project"),
    ],
)
def test_every_switch_must_be_on_before_a_chip_exists(rig, workspace, shared, project_on, sentence):
    bot = turn_on(rig)
    item = teach(rig, bot, HOSE, kind="fact", topic="hose")
    settings(rig, enabled=workspace, shared_enabled=shared)
    project(rig, "work", project_on)
    status, reply, _ = share(rig, item, project="work")
    assert (status, reply["code"]) == (409, "not_shareable")
    assert sentence in reply["error"]
    assert rows(rig, "SELECT COUNT(*) FROM bot_pending") == [(0,)]


def test_a_chip_alone_shares_nothing_until_the_person_taps(rig):
    bot = turn_on(rig)
    all_on(rig)
    item = teach(rig, bot, HOSE, kind="fact", topic="hose")
    status, chip, _ = share(rig, item, project="work")
    assert status == 200
    assert chip["bot"] == bot
    assert chip["label"] == (
        "Share with everyone who can read work, with your name: “" + HOSE + "”. "
        "It stays visible there until you or the workspace owner delete it."
    )
    assert shared_in(rig) == (200, []) and rows(rig)[0][0] == "person"
    assert len(rows(rig)) == 1
    # Declining the chip shares nothing either, and it cannot be tapped again.
    assert tap(rig, bot, chip["pending"], accept=False)[0] == 200
    assert tap(rig, bot, chip["pending"])[0] == 409
    assert shared_in(rig) == (200, []) and len(rows(rig)) == 1


def test_another_person_cannot_tap_or_even_see_the_chip(rig):
    bot = turn_on(rig)
    blake_bot = turn_on(rig, "blake")
    all_on(rig)
    item = teach(rig, bot, HOSE, kind="fact", topic="hose")
    _, chip, _ = share(rig, item, project="work")
    assert tap(rig, bot, chip["pending"], "blake")[0] == 404
    assert tap(rig, blake_bot, chip["pending"], "blake")[0] == 404
    assert share(rig, item, "blake", project="work")[0] == 404  # not Blake's item
    assert len(rows(rig)) == 1


def test_the_second_tap_conflicts_and_one_copy_exists(rig):
    bot = turn_on(rig)
    all_on(rig)
    item = teach(rig, bot, HOSE, kind="fact", topic="hose")
    _, chip, _ = share(rig, item, project="work")
    assert tap(rig, bot, chip["pending"])[0] == 200
    status, reply, _ = tap(rig, bot, chip["pending"])
    assert (status, reply["code"]) == (409, "conflict")
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned WHERE scope='shared'") == [(1,)]
    # A fresh chip for the same sentence into the same project is refused when tapped.
    _, again, _ = share(rig, item, project="work")
    status, reply, _ = tap(rig, bot, again["pending"])
    assert status == 200
    assert reply["message"]["text"] == "That is already shared with this project."
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned WHERE scope='shared'") == [(1,)]


def test_a_switch_turned_off_before_the_tap_refuses_and_closes_the_chip(rig):
    bot = turn_on(rig)
    all_on(rig)
    item = teach(rig, bot, HOSE, kind="fact", topic="hose")
    _, chip, _ = share(rig, item, project="work")
    project(rig, "work", False)
    status, reply, _ = tap(rig, bot, chip["pending"])
    assert status == 200
    assert reply["message"]["text"] == "Sharing learned items is switched off for that project."
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned WHERE scope='shared'") == [(0,)]
    assert tap(rig, bot, chip["pending"])[0] == 409  # closed, a later switch-on does not reopen it


# A shared item reaches project readers, labelled, and nobody else


def test_a_shared_item_reaches_a_teammates_prompt_labelled_and_the_private_one_never(rig):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    all_on(rig)
    item, copy = shared_item(rig, avery_bot)
    teach(rig, avery_bot, SHED, kind="fact", topic="shed")  # stays private: never tapped
    assert shared_in(rig, "blake") == (
        200,
        [
            {
                "id": copy,
                "kind": "fact",
                "text": HOSE,
                "said": rig.store_learned().get(rig.avery, item)["source_at"][:10],
                "shared_at": rig.store_learned().get(rig.avery, copy)["created_at"],
                "mine": False,
                "by": "owner@example.com",
            }
        ],
    )
    ask_bot(
        rig, blake_bot, "Where is the garden hose and the garden shed?", "request-0101", "blake"
    )
    system, prompt = sent(rig)
    (shown,) = carried(prompt)
    assert shown == {
        "id": copy,
        "scope": "shared",
        "kind": "fact",
        "said": shown["said"],
        "text": HOSE,
        "by": "a teammate",
    }
    assert SHED not in prompt and context.LEARNED_SHARED in prompt
    assert context.LEARNED_RULE in system
    # The same sentence is told once to Avery: her own item, not her copy as well.
    ask_bot(rig, avery_bot, "Where is the hose?", "request-0102")
    (mine,) = carried(sent(rig)[1])
    assert (mine["id"], mine["scope"]) == (item, "person") and context.LEARNED_SHARED not in sent(
        rig
    )[1]
    # Blake's reply carries a block for the shared item, and the use is counted on the copy.
    stored = rig.store().messages(blake_bot, rig.blake)[-1]
    assert stored["blocks"][-1] == {"type": "learned", "ids": [copy]}
    assert rig.store_learned().get(rig.avery, copy)["use_count"] == 1
    reader = blocks.Reader(rig.hub.gateway, rig.person(rig.member_user))
    (block,) = blocks.hydrate(reader, [{"type": "learned", "ids": [copy]}])
    assert [i["text"] for i in block["items"]] == [HOSE]


def test_a_person_item_stays_private_and_blake_cannot_read_it(rig):
    avery_bot = turn_on(rig)
    all_on(rig)
    item, _ = shared_item(rig, avery_bot)
    assert rig.get(rig.member, f"/api/learned/{item}")[0] == 404
    assert rig.get(rig.member, "/api/learned")[1]["items"] == []
    assert rig.request(rig.member, "DELETE", f"/api/learned/{item}")[0] == 404
    assert rig.request(rig.member, "PATCH", f"/api/learned/{item}", {"pinned": True})[0] == 404
    assert rig.get(rig.owner, f"/api/learned/{item}")[1]["item"]["scope"] == "person"


def test_shared_items_are_not_used_by_a_person_whose_bot_has_learning_off(rig):
    avery_bot = turn_on(rig)
    blake_bot = rig.by_board(rig.member)["general"]["id"]  # learning stays off for Blake
    all_on(rig)
    shared_item(rig, avery_bot)
    ask_bot(rig, blake_bot, "Where is the garden hose?", "request-0103", "blake")
    assert carried(sent(rig)[1]) == []


def test_a_project_bot_reads_only_its_own_projects_shared_items(rig):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    rig.add_board("garden", "Garden")
    all_on(rig)
    project(rig, "garden")
    shared_item(rig, avery_bot, HOSE, slug="work")
    shared_item(rig, avery_bot, SHED, slug="garden")
    garden_bot = rig.by_board(rig.member)["garden"]["id"]
    assert (
        rig.request(rig.member, "PATCH", f"/api/bots/{garden_bot}/learning", {"mode": "auto"})[0]
        == 200
    )
    ask_bot(
        rig, garden_bot, "Where is the garden hose and the garden shed?", "request-0104", "blake"
    )
    assert [i["text"] for i in carried(sent(rig)[1])] == [SHED]
    ask_bot(
        rig, blake_bot, "Where is the garden hose and the garden shed?", "request-0105", "blake"
    )
    assert sorted(i["text"] for i in carried(sent(rig)[1])) == sorted([HOSE, SHED])


def test_at_most_three_shared_items_are_used_and_the_rest_are_counted_as_left_out(rig):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    all_on(rig)
    for n in range(MAX_SHARED + 2):
        shared_item(rig, avery_bot, f"The garden gate number {n} is painted blue.")
    ask_bot(rig, blake_bot, "Which garden gate is painted blue?", "request-0106", "blake")
    prompt = sent(rig)[1]
    assert len(carried(prompt)) == MAX_SHARED and "(2 more left out)" in prompt


# Switches and access are re-resolved on every read


@pytest.mark.parametrize("which", ["project", "shared", "workspace"])
def test_switching_off_hides_shared_items_at_once_and_switching_on_restores_them(rig, which):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    all_on(rig)
    _, copy = shared_item(rig, avery_bot)

    def blake_sees():
        ask_bot(
            rig,
            blake_bot,
            "Where is the garden hose?",
            f"request-{len(rig.fake.calls):04d}x",
            "blake",
        )
        return [i["id"] for i in carried(sent(rig)[1])]

    assert blake_sees() == [copy]
    off = {
        "project": lambda: project(rig, "work", False),
        "shared": lambda: settings(rig, shared_enabled=False),
        "workspace": lambda: settings(rig, enabled=False),
    }[which]
    on = {
        "project": lambda: project(rig, "work", True),
        "shared": lambda: settings(rig, shared_enabled=True),
        "workspace": lambda: settings(rig, enabled=True),
    }[which]
    off()
    assert blake_sees() == [] and shared_in(rig) == (200, [])
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned WHERE scope='shared'") == [(1,)]  # hidden
    on()
    assert blake_sees() == [copy] and [i["id"] for i in shared_in(rig)[1]] == [copy]


def test_a_member_who_cannot_read_the_project_never_sees_its_shared_items(rig):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    vault(rig)
    all_on(rig, "vault")
    _, copy = shared_item(rig, avery_bot, slug="vault")
    assert shared_in(rig, "avery", "vault")[1][0]["id"] == copy
    assert shared_in(rig, "blake", "vault")[0] == 404  # an unreadable project looks missing
    ask_bot(rig, blake_bot, "Where is the garden hose?", "request-0107", "blake")
    assert carried(sent(rig)[1]) == []
    reader = blocks.Reader(rig.hub.gateway, rig.person(rig.member_user))
    assert blocks.hydrate(reader, [{"type": "learned", "ids": [copy]}]) == [
        {"type": "learned", "gone": True}
    ]
    # Blake cannot share into it either, whatever he names.
    mine = teach(rig, blake_bot, SHED, "blake", kind="fact", topic="shed")
    assert share(rig, mine, "blake", project="vault")[0] == 404


def test_a_member_who_loses_the_project_stops_seeing_shared_items_but_keeps_their_own(rig):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    all_on(rig)
    mine = teach(rig, blake_bot, SHED, "blake", kind="fact", topic="shed")
    _, copy = shared_item(rig, avery_bot)
    ask_bot(rig, blake_bot, "Where is the hose?", "request-0108", "blake")
    assert [i["id"] for i in carried(sent(rig)[1])] == [copy]
    rig.archive_board("work")  # nobody reads it any more
    assert shared_in(rig, "blake")[0] == 404
    ask_bot(rig, blake_bot, "Where is the hose and the shed?", "request-0109", "blake")
    assert [i["text"] for i in carried(sent(rig)[1])] == [SHED]  # his own item remains his
    assert rig.get(rig.member, f"/api/learned/{mine}")[0] == 200


# Deleting


def test_the_sharer_and_the_owner_can_delete_a_copy_and_no_one_else(rig):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    all_on(rig)
    item, copy = shared_item(rig, avery_bot)
    # Blake did not share it: he cannot delete it, and learns nothing from the refusal.
    assert rig.request(rig.member, "DELETE", f"/api/learned/{copy}")[0] == 404
    assert [i["id"] for i in shared_in(rig)[1]] == [copy]
    # The sharer deletes her copy: the person item stays and its sentence is not blocked.
    assert rig.request(rig.owner, "DELETE", f"/api/learned/{copy}")[0] == 200
    assert shared_in(rig) == (200, [])
    assert rig.get(rig.owner, f"/api/learned/{item}")[0] == 200
    assert rows(rig, "SELECT COUNT(*) FROM bot_learn_blocks") == [(0,)]
    assert rig.request(rig.owner, "DELETE", f"/api/learned/{copy}")[0] == 404  # idempotent
    # The owner deletes a member's copy.
    _, theirs = shared_item(rig, blake_bot, SHED, "blake")
    assert [i["id"] for i in shared_in(rig, "avery")[1]] == [theirs]
    assert rig.request(rig.owner, "DELETE", f"/api/learned/{theirs}")[0] == 200
    assert shared_in(rig, "avery") == (200, [])
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned WHERE scope='shared'") == [(0,)]


def test_an_owner_cannot_delete_a_copy_of_a_project_they_cannot_read(rig):
    avery_bot = turn_on(rig)
    vault(rig)
    all_on(rig, "vault")
    _, copy = shared_item(rig, avery_bot, slug="vault")
    rig.archive_board("vault")
    assert rig.request(rig.member, "DELETE", f"/api/learned/{copy}")[0] == 404
    store = rig.store_learned()
    with pytest.raises(Missing):
        store.delete_shared(copy, user_id="someone-else", owner_can_read=lambda board: False)
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned WHERE scope='shared'") == [(1,)]


def test_forgetting_a_person_item_deletes_its_shared_copies_for_good(rig):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    all_on(rig)
    item, copy = shared_item(rig, avery_bot)
    assert rig.request(rig.owner, "DELETE", f"/api/learned/{item}")[0] == 200
    assert shared_in(rig, "blake") == (200, [])
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned") == [(0,)]
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned_fts") == [(0,)]
    ask_bot(rig, blake_bot, "Where is the garden hose?", "request-0110", "blake")
    assert carried(sent(rig)[1]) == []
    assert rig.request(rig.owner, "DELETE", f"/api/learned/{copy}")[0] == 404
    # The sentence is blocked for its sharer: the same words are not learned again.
    assert rows(rig, "SELECT COUNT(*) FROM bot_learn_blocks") == [(1,)]


def test_forget_everything_removes_the_shared_contributions_too(rig):
    avery_bot = turn_on(rig)
    all_on(rig)
    shared_item(rig, avery_bot)
    shared_item(rig, avery_bot, SHED)
    status, reply, _ = rig.request(
        rig.owner, "POST", f"/api/bots/{avery_bot}/learned/forget-all", {}
    )
    assert (status, reply["forgotten"]) == (200, 4)
    assert shared_in(rig) == (200, [])
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned") == [(0,)]


# The model can only ask


def test_the_model_can_ask_to_share_but_only_the_tap_shares(rig):
    avery_bot = turn_on(rig)
    all_on(rig)
    item = teach(rig, avery_bot, HOSE, kind="fact", topic="hose")
    rig.fake.script = [
        [call("share_learned", id=item, project="work")],
        [("text", "Waiting for you.")],
    ]
    events = ask_bot(
        rig, avery_bot, "Please share what I said about the hose with work", "request-0111"
    )
    assert "share_learned" in {t["name"] for t in rig.fake.calls[0]["tools"]}
    assert names(events)[-1] == "done"
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned WHERE scope='shared'") == [(0,)]
    (pending,) = rows(rig, "SELECT id,label,state,tool FROM bot_pending")
    assert pending[2:] == ("open", "share_learned")
    assert pending[1].startswith("Share with everyone who can read work, with your name: ")
    assert HOSE in pending[1]
    assert tap(rig, avery_bot, pending[0])[0] == 200
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned WHERE scope='shared'") == [(1,)]


def test_the_model_cannot_share_what_is_not_the_persons_or_not_active_or_unreadable(rig):
    avery_bot, blake_bot = turn_on(rig), turn_on(rig, "blake")
    vault(rig)
    all_on(rig)
    project(rig, "vault")
    foreign = teach(rig, blake_bot, SHED, "blake", kind="fact", topic="shed")
    mine = teach(rig, avery_bot, HOSE, kind="fact", topic="hose")
    for n, args in enumerate(
        [
            {"id": foreign, "project": "work"},  # another person's item
            {"id": "n1", "project": "work"},  # a note
            {"id": mine, "project": "nowhere"},  # no such project
            {"id": mine},  # no project and none on the item
        ]
    ):
        rig.fake.script = [[call("share_learned", **args)], [("text", "Sorry.")]]
        ask_bot(rig, avery_bot, "Share it", f"request-02{n:02d}")
        assert rows(rig, "SELECT COUNT(*) FROM bot_pending") == [(0,)], args
    # A person that cannot read the project gets no chip either.
    rig.fake.script = [[call("share_learned", id=foreign, project="vault")], [("text", "Sorry.")]]
    ask_bot(rig, blake_bot, "Share it", "request-0299", "blake")
    assert rows(rig, "SELECT COUNT(*) FROM bot_pending") == [(0,)]


# The store itself


def store_item(env, text=HOSE, user="avery"):
    bot, message = say(env, user, text)
    switch_on(env, bot, user)
    item = learn(env, bot, message, user, text, kind="fact", topic="hose")
    env.learned.set_policy(learned.WORKSPACE, "owner", shared_enabled=True)
    env.learned.set_policy("board:work", "owner", shared_enabled=True)
    return bot, item["id"]


def test_the_store_refuses_a_share_for_every_wrong_state(env):
    bot, item = store_item(env)
    ok = lambda slug: True  # noqa: E731
    with pytest.raises(Invalid):
        env.learned.share("avery", item, "", ok)
    with pytest.raises(Missing):
        env.learned.share("blake", item, "work", ok)  # someone else's item
    with pytest.raises(Missing):
        env.learned.share("avery", item, "work", lambda slug: False)  # unreadable project
    with pytest.raises(Missing):
        env.learned.share("avery", item, "work")  # no way to read means no project
    env.learned.set_policy("board:work", "owner", shared_enabled=False)
    with pytest.raises(learned.Disabled) as caught:
        env.learned.share("avery", item, "work", ok)
    assert caught.value.reason == "project_off"
    env.learned.set_policy("board:work", "owner", shared_enabled=True)
    copy = env.learned.share("avery", item, "work", ok)
    assert (copy["scope"], copy["shared_from"], copy["text"], copy["board"]) == (
        "shared",
        item,
        HOSE,
        "work",
    )
    with pytest.raises(Invalid):
        env.learned.share("avery", copy["id"], "work", ok)  # a copy is not shared again
    with pytest.raises(Invalid):
        env.learned.update("avery", copy["id"], text="Changed words here.")  # never edited
    with pytest.raises(Conflict):
        env.learned.share("avery", item, "work", ok)
    assert query(env, "SELECT action FROM bot_learn_log WHERE action='share'") == [("share",)]
    assert env.learned.counts("avery", bot)["shared"] == 1


def test_a_note_the_bot_added_is_not_copied_and_the_cap_holds(env):
    bot, message = say(env, "avery", HOSE)
    switch_on(env, bot)
    env.learned.set_policy(learned.WORKSPACE, "owner", shared_enabled=True)
    env.learned.set_policy("board:work", "owner", shared_enabled=True)
    ok = lambda slug: True  # noqa: E731
    item = learn(
        env, bot, message, text=HOSE, kind="fact", topic="hose", note="The bot's own remark."
    )
    copy = env.learned.share("avery", item["id"], "work", ok)
    assert copy["note"] is None and copy["note_by"] is None
    for n in range(SHARED_CAP - 1):
        # Other people's copies fill the project; each is a separate person's shared item.
        query(env, "SELECT 1")
        extra, _ = say(env, f"user{n}")
        env.learned.configure(extra, f"user{n}", mode="auto")
        m = speak(env, extra, f"user{n}", f"The garden gate number {n} is blue.")
        learned_item = learn(
            env,
            extra,
            m,
            f"user{n}",
            f"The garden gate number {n} is blue.",
            kind="fact",
            topic=f"gate {n}",
        )
        env.learned.share(f"user{n}", learned_item["id"], "work", ok)
    over, _ = say(env, "user99")
    env.learned.configure(over, "user99", mode="auto")
    m = speak(env, over, "user99", "The garden gate over is blue.")
    last = learn(
        env, over, m, "user99", "The garden gate over is blue.", kind="fact", topic="gate over"
    )
    with pytest.raises(learned.Capped) as caught:
        env.learned.share("user99", last["id"], "work", ok)
    assert caught.value.kind == "shared"


def test_the_shared_list_needs_both_switches_and_a_readable_project(env):
    bot, item = store_item(env)
    ok = lambda slug: True  # noqa: E731
    env.learned.share("avery", item, "work", ok)
    assert [i["text"] for i in env.learned.shared_list("work", "blake", ok)] == [HOSE]
    assert env.learned.shared_list("work", "blake", lambda slug: False) == []
    assert env.learned.shared_list("work", "blake") == []
    with pytest.raises(Invalid):
        env.learned.shared_list("work", "blake", ok, limit=0)
    env.learned.set_policy(learned.WORKSPACE, "owner", shared_enabled=False)
    assert env.learned.shared_list("work", "blake", ok) == []
    assert json.dumps(env.learned.shared_list("other", "blake", ok)) == "[]"
