"""/api/learned, /api/bots/<id>/learning and the owner switches over real HTTP.

A clean workspace: the owner (Avery), a member (Blake), a project and another workspace.
"""

import pytest

from commonplace.bots import learned
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_app_grant import app, bearer

WORDS = "Please keep answers short and in plain language."
SECRET = "my token is ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
OFF = {"enabled": False, "shared_enabled": False}


def person_bot(hub, who, text=WORDS, board=None):
    """A general bot of ``who`` (a user id) with one message; returns (bot id, message id)."""
    bots = hub.store()
    bot = bots.create(who, "Helper")["id"]
    return bot, bots.add_user_message(bot, who, text, "request-0001")["id"]


def learn(hub, who, bot, message, text=WORDS, **fields):
    return hub.store_learned().add_item(
        who,
        bot,
        kind="preference",
        topic="answer style",
        text=text,
        source_message_id=message,
        **fields,
    )


@pytest.fixture(name="hub")
def hub_fixture(bots_hub):
    hub = bots_hub
    hub.store_learned = lambda: learned.Learned(hub.store())
    hub.avery, hub.blake = hub.hub.admin, hub.member_user
    return hub


def switch_on(hub, who, bot, mode="auto"):
    assert hub.request(hub.owner, "PATCH", "/api/learning/settings", {"enabled": True})[0] == 200
    cookie = hub.owner if who == hub.avery else hub.member
    status, body, _ = hub.request(cookie, "PATCH", f"/api/bots/{bot}/learning", {"mode": mode})
    assert status == 200, body


def test_a_new_workspace_has_learning_off_for_everyone(hub):
    for auth in (hub.owner, hub.member):
        status, body, _ = hub.get(auth, "/api/learning/settings")
        assert (status, body["enabled"], body["shared_enabled"]) == (200, False, False)
    assert hub.get(hub.owner, "/api/learning/settings")[1]["can_configure"] is True
    assert hub.get(hub.member, "/api/learning/settings")[1]["can_configure"] is False
    bot = hub.bots(hub.member)["bots"][0]["id"]
    status, body, _ = hub.get(hub.member, f"/api/bots/{bot}/learning")
    assert status == 200
    assert body["learning"] == {
        "mode": "off",
        "paused": False,
        "paused_until": None,
        "floor_message_id": 0,
        "workspace_enabled": False,
        "active": False,
        "counts": {"person": 0, "shared": 0, "proposed": 0, "notes": 0},
    }
    assert hub.get(hub.member, "/api/learned")[1]["items"] == []
    status, body, _ = hub.get(hub.owner, "/api/projects/work/learning")
    assert (status, body) == (
        200,
        {"board": "work", "shared_enabled": False, "can_configure": True},
    )


def test_only_the_owner_changes_the_workspace_and_project_switches(hub):
    for path, body in (
        ("/api/learning/settings", {"enabled": True}),
        ("/api/learning/settings", {"shared_enabled": True}),
        ("/api/projects/work/learning", {"shared_enabled": True}),
    ):
        status, reply, _ = hub.request(hub.member, "PATCH", path, body)
        assert (status, reply["code"]) == (403, "forbidden"), path
    assert hub.get(hub.owner, "/api/learning/settings")[1]["enabled"] is False
    assert hub.get(hub.member, "/api/projects/work/learning")[1]["shared_enabled"] is False

    status, body, _ = hub.request(hub.owner, "PATCH", "/api/learning/settings", {"enabled": True})
    assert (status, body["enabled"], body["shared_enabled"]) == (200, True, False)
    assert hub.get(hub.member, "/api/learning/settings")[1]["enabled"] is True
    status, body, _ = hub.request(
        hub.owner, "PATCH", "/api/projects/work/learning", {"shared_enabled": True}
    )
    assert (status, body["shared_enabled"]) == (200, True)
    assert hub.get(hub.member, "/api/projects/work/learning")[1]["shared_enabled"] is True
    assert hub.get(hub.member, "/api/projects/work/learning")[1]["can_configure"] is False
    assert hub.get(hub.owner, "/api/projects/nowhere/learning")[0] == 404
    assert (
        hub.request(hub.owner, "PATCH", "/api/projects/nowhere/learning", {"shared_enabled": True})[
            0
        ]
        == 404
    )
    for path, body in (
        ("/api/learning/settings", {"enabled": "yes"}),
        ("/api/learning/settings", {}),
        ("/api/learning/settings", {"other": True}),
        ("/api/projects/work/learning", {"enabled": True}),
        ("/api/projects/work/learning", {"shared_enabled": 1}),
    ):
        assert hub.request(hub.owner, "PATCH", path, body)[0] == 400, (path, body)

    rows = [
        dict(r)
        for r in hub.db.audit_entries(hub.workspace, limit=50)
        if r["action"] == "bot:learn_setting"
    ]
    assert sorted(r["decision"] for r in rows) == ["ok", "ok", "refused", "refused", "refused"]
    assert all(r["reason"] == "" and r["tool"] == "learn_setting" for r in rows)


def test_a_person_sets_the_mode_and_pause_of_their_own_bot_only(hub):
    bot = hub.bots(hub.owner)["bots"][0]["id"]
    path = f"/api/bots/{bot}/learning"
    status, body, _ = hub.request(hub.owner, "PATCH", path, {"mode": "ask"})
    assert (status, body["learning"]["mode"], body["learning"]["active"]) == (200, "ask", False)
    hub.request(hub.owner, "PATCH", "/api/learning/settings", {"enabled": True})
    status, body, _ = hub.request(hub.owner, "PATCH", path, {"pause": 7})
    assert (body["learning"]["paused"], body["learning"]["active"]) == (True, False)
    assert body["learning"]["paused_until"]
    body = hub.request(hub.owner, "PATCH", path, {"pause": None})[1]
    assert (body["learning"]["paused"], body["learning"]["active"]) == (False, True)
    for bad in (
        {},
        {"mode": "on"},
        {"mode": 5},
        {"pause": 0},
        {"pause": 31},
        {"pause": "3"},
        {"x": 1},
    ):
        assert hub.request(hub.owner, "PATCH", path, bad)[0] == 400, bad
    assert hub.get(hub.owner, path)[1]["learning"]["mode"] == "ask"
    # Blake cannot see or change Avery's bot; the owner has no special read path
    assert hub.request(hub.member, "PATCH", path, {"mode": "off"})[0] == 404
    assert hub.get(hub.member, path)[0] == 404
    assert hub.get(hub.other, path)[0] == 404


def test_each_person_sees_edits_and_forgets_only_their_own_items(hub):
    avery_bot, avery_message = person_bot(hub, hub.avery)
    blake_bot, blake_message = person_bot(hub, hub.blake, "I prefer metric units for everything")
    switch_on(hub, hub.avery, avery_bot)
    switch_on(hub, hub.blake, blake_bot)
    mine = learn(hub, hub.avery, avery_bot, avery_message)
    theirs = learn(hub, hub.blake, blake_bot, blake_message, text="prefer metric units")

    status, body, _ = hub.get(hub.owner, "/api/learned")
    assert [i["id"] for i in body["items"]] == [mine["id"]]
    assert body["items"][0]["text"] == WORDS
    assert body["counts"]["person"] == 1
    assert [i["id"] for i in hub.get(hub.member, "/api/learned")[1]["items"]] == [theirs["id"]]
    assert [i["id"] for i in hub.get(hub.owner, f"/api/bots/{avery_bot}/learned")[1]["items"]] == [
        mine["id"]
    ]
    assert hub.get(hub.owner, f"/api/bots/{blake_bot}/learned")[0] == 404
    assert hub.get(hub.owner, f"/api/learned?bot={blake_bot}")[0] == 404

    path = f"/api/learned/{theirs['id']}"
    for method, body in (
        ("GET", None),
        ("PATCH", {"pinned": True}),
        ("PATCH", {"text": "Avery wrote this"}),
        ("DELETE", None),
    ):
        assert hub.request(hub.owner, method, path, body)[0] == 404, method
    assert hub.request(hub.owner, "POST", f"/api/bots/{blake_bot}/learned/forget-all", {})[0] == 404
    assert hub.get(hub.member, path)[1]["item"]["text"] == "prefer metric units"

    own = f"/api/learned/{mine['id']}"
    status, body, _ = hub.request(
        hub.owner, "PATCH", own, {"text": "Keep it brief", "pinned": True}
    )
    assert (status, body["item"]["text"], body["item"]["text_by"], body["item"]["pinned"]) == (
        200,
        "Keep it brief",
        "person_edit",
        True,
    )
    assert body["item"]["source_message_id"] == avery_message
    status, body, _ = hub.request(hub.owner, "PATCH", own, {"text": SECRET})
    assert (status, body["code"], body["reason"]) == (400, "refused", "secret")
    assert SECRET not in str(body)
    for bad in ({}, {"text": "x"}, {"pinned": "yes"}, {"state": "active"}):
        assert hub.request(hub.owner, "PATCH", own, bad)[0] == 400, bad
    assert hub.get(hub.owner, own)[1]["item"]["text"] == "Keep it brief"

    assert hub.request(hub.owner, "DELETE", own)[0] == 200
    assert hub.request(hub.owner, "DELETE", own)[0] == 404
    assert hub.get(hub.owner, own)[0] == 404
    assert hub.get(hub.owner, "/api/learned")[1]["items"] == []
    assert hub.get(hub.member, "/api/learned")[1]["counts"]["person"] == 1


def test_forget_all_over_http_sets_the_floor_and_keeps_the_thread(hub):
    bot, message = person_bot(hub, hub.avery)
    switch_on(hub, hub.avery, bot)
    learn(hub, hub.avery, bot, message)
    hub.store().memory_add(bot, hub.avery, "likes short answers")
    path = f"/api/bots/{bot}/learned"
    assert hub.get(hub.owner, path)[1]["counts"] == {
        "person": 1,
        "shared": 0,
        "proposed": 0,
        "notes": 1,
    }
    for body in ({"x": 1}, None):
        assert hub.request(hub.owner, "POST", path + "/forget-all", body)[0] == 400
    assert hub.request(hub.owner, "POST", path + "/other", {})[0] == 404
    status, body, _ = hub.request(hub.owner, "POST", path + "/forget-all", {})
    assert (status, body) == (200, {"forgotten": 1, "notes": 1, "floor": message})
    assert hub.get(hub.owner, path)[1] == {
        "items": [],
        "counts": {"person": 0, "shared": 0, "proposed": 0, "notes": 0},
    }
    assert (
        hub.get(hub.owner, f"/api/bots/{bot}/learning")[1]["learning"]["floor_message_id"]
        == message
    )
    assert hub.get(hub.owner, f"/api/bots/{bot}/messages")[1]["messages"][0]["text"] == WORDS


def test_notes_are_in_the_list_and_forgetting_one_blanks_it(hub):
    bot = hub.bots(hub.owner)["bots"][0]["id"]
    note = hub.post(hub.owner, f"/api/bots/{bot}/memory", {"text": "likes short answers"})[1][
        "memory"
    ]
    items = hub.get(hub.owner, "/api/learned?kind=note")[1]["items"]
    assert [(i["id"], i["kind"], i["text"]) for i in items] == [
        (f"n{note['id']}", "note", "likes short answers")
    ]
    assert (
        hub.request(hub.owner, "PATCH", f"/api/learned/n{note['id']}", {"pinned": True})[0] == 400
    )
    assert hub.request(hub.member, "DELETE", f"/api/learned/n{note['id']}")[0] == 404
    assert hub.request(hub.owner, "DELETE", f"/api/learned/n{note['id']}")[0] == 200
    assert hub.request(hub.owner, "DELETE", f"/api/learned/n{note['id']}")[0] == 404
    assert hub.get(hub.owner, "/api/learned")[1]["items"] == []
    with hub.store()._tx(write=False) as conn:
        assert (
            conn.execute("SELECT text FROM bot_memory WHERE id=?", (note["id"],)).fetchone()[0]
            == ""
        )


def test_listing_bounds(hub):
    for query in (
        "limit=0",
        "limit=101",
        "limit=x",
        "offset=-1",
        "scope=group",
        "state=old",
        "kind=idea",
    ):
        assert hub.get(hub.owner, f"/api/learned?{query}")[0] == 400, query
    assert hub.get(hub.owner, "/api/learned?limit=100&offset=0&scope=person&state=active")[0] == 200


def test_only_a_signed_in_person_reaches_these_routes(hub):
    bot, message = person_bot(hub, hub.avery)
    switch_on(hub, hub.avery, bot)
    item = learn(hub, hub.avery, bot, message)
    token, _ = app(hub)  # the person's own app holding bots:chat acts as them everywhere else
    agent = hub.t1
    calls = (
        ("GET", "/api/learned", None),
        ("GET", f"/api/learned/{item['id']}", None),
        ("PATCH", f"/api/learned/{item['id']}", {"pinned": True}),
        ("DELETE", f"/api/learned/{item['id']}", None),
        ("GET", "/api/learning/settings", None),
        ("PATCH", "/api/learning/settings", {"enabled": False}),
        ("GET", f"/api/bots/{bot}/learning", None),
        ("PATCH", f"/api/bots/{bot}/learning", {"mode": "off"}),
        ("GET", f"/api/bots/{bot}/learned", None),
        ("POST", f"/api/bots/{bot}/learned/forget-all", {}),
        ("GET", "/api/projects/work/learning", None),
        ("PATCH", "/api/projects/work/learning", {"shared_enabled": True}),
    )
    assert hub.get(bearer(token), "/api/bots")[0] == 200  # the app does reach the bots
    for method, path, body in calls:
        assert hub.request(bearer(token), method, path, body)[0] in (403, 404), (
            "app",
            method,
            path,
        )
        assert hub.request(agent, method, path, body)[0] in (403, 404), ("agent", method, path)
        assert hub.request(None, method, path, body)[0] in (401, 403), ("anonymous", method, path)
    assert hub.get(hub.owner, "/api/learning/settings")[1]["enabled"] is True
    assert hub.get(hub.owner, f"/api/learned/{item['id']}")[1]["item"]["pinned"] is False
    assert hub.get(hub.owner, f"/api/bots/{bot}/learning")[1]["learning"]["mode"] == "auto"


def test_a_cross_origin_write_is_refused(hub):
    bot, message = person_bot(hub, hub.avery)
    switch_on(hub, hub.avery, bot)
    item = learn(hub, hub.avery, bot, message)
    foreign = {**hub.owner, "Origin": "http://attacker.example"}
    for method, path, body in (
        ("PATCH", "/api/learning/settings", {"enabled": False}),
        ("PATCH", "/api/projects/work/learning", {"shared_enabled": True}),
        ("PATCH", f"/api/bots/{bot}/learning", {"mode": "off"}),
        ("PATCH", f"/api/learned/{item['id']}", {"pinned": True}),
        ("DELETE", f"/api/learned/{item['id']}", None),
        ("POST", f"/api/bots/{bot}/learned/forget-all", {}),
    ):
        assert hub.request(foreign, method, path, body)[0] == 403, (method, path)
    assert hub.get(hub.owner, f"/api/learned/{item['id']}")[1]["item"]["pinned"] is False
    assert hub.get(hub.owner, "/api/learning/settings")[1]["enabled"] is True


def test_nothing_is_stored_or_logged_with_the_persons_words(hub):
    bot, message = person_bot(hub, hub.avery, "My favourite quartz lantern notes, always")
    switch_on(hub, hub.avery, bot)
    item = learn(hub, hub.avery, bot, message, text="quartz lantern notes")
    hub.request(hub.owner, "PATCH", f"/api/learned/{item['id']}", {"text": "quartz lantern edit"})
    hub.request(hub.owner, "DELETE", f"/api/learned/{item['id']}")
    audit = repr([tuple(r) for r in hub.db.audit_entries(hub.workspace, limit=200)])
    assert "quartz" not in audit and "lantern" not in audit
    with hub.store()._tx(write=False) as conn:
        for table in (
            "bot_learn_log",
            "bot_learn_blocks",
            "bot_learn_runs",
            "bot_learn_policy",
            "bot_learn_state",
        ):
            assert "quartz" not in repr([tuple(r) for r in conn.execute(f"SELECT * FROM {table}")])
