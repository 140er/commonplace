"""Chat-platform bridge, confirmation slice: on a chat direct message nothing writes with the
person's identity without their own tap, over real HTTP with the in-memory fake platform and a
scripted chat model."""

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from commonplace.bots import store as bots_store
from commonplace.chat import model_stream  # noqa: F401 - the `model` fixture patches it
from commonplace.chat_bridge import outbox
from tests._chat_bridge_fake import send, tap
from tests.test_bots_turn import call
from tests.test_chat_bridge_foundation import (  # noqa: F401 - fixtures
    _enabled_install,
    audit_rows,
    bridge_fixture,
)
from tests.test_chat_bridge_intake import (  # noqa: F401 - fixtures
    World,
    model_fixture,
    platform_fixture,
    wait_for,
    world_fixture,
)

WORDS = "Fix the leaking tap in the kitchen"
SECRET = "ghp_" + "A1b2C3d4" * 5  # a credential-shaped string
WRITERS = {
    "create_work",
    "comment_work",
    "tell_orchestrator",
    "prioritize_work",
    "remember",
    "forget",
}


class Chat(World):
    """The slice-3 world plus helpers for chips and taps."""

    def link_of(self, ext_user):
        return self.bridge_store().link_of(self.install, ext_user)

    def confirm_ok(self, ext_user, auth, value=True):
        status, body, _ = self.hub.request(
            auth,
            "PATCH",
            f"/api/bridge/links/{self.link_of(ext_user)['id']}",
            {"confirm_ok": value},
        )
        assert status == 200, body
        return body["link"]

    def ready(self, ext_user="U-AVERY", auth=None, tapping=True):
        self.link(ext_user, auth or self.hub.owner)
        if tapping:
            self.confirm_ok(ext_user, auth or self.hub.owner)

    def tasks(self, board="work"):
        return [
            t["title"]
            for t in self.service(self.avery).task_list(board=board)
            if isinstance(t, dict) and "title" in t
        ]

    def service(self, user):
        from tests.test_commonplace_boards import service

        return service(self.hub.hub, self.hub.person(user))

    def quiet(self):
        """Wait until no turn runs, no event is open and every outbox row is posted or closed."""
        self.settle()
        wait_for(
            lambda: (
                not [r for r in self.outbox() if r[3] in ("queued", "sending")]
                and not [s for s in self.states().values() if s in ("received", "running")]
            )
        )

    def ask(self, name, user, channel, text, script):
        """One DM turn with the model scripted, then quiet."""
        self.model.script = script
        assert self.say(name, user, channel, text) == 200
        wait_for(lambda: name in self.states())
        self.quiet()

    def chips(self):
        return [p for p in self.platform.posts if p["buttons"]]

    def press(self, name, user, channel, payload, install=None, team="T-ONE"):
        body = {**tap(name, user, channel, payload), "team": team}
        return send(self.hub, install or self.install, body, route="interact")

    def tap(self, *args, **kwargs):
        status = self.press(*args, **kwargs)
        if status == 200:
            self.quiet()
        return status

    def last(self):
        return self.platform.posts[-1]["text"]

    def pending(self):
        conn = sqlite3.connect(
            Path(self.hub.hub.supervisor.workspace_dir(self.ws)) / "bots.sqlite3"
        )
        try:
            return conn.execute("SELECT id, tool, state, label FROM bot_pending").fetchall()
        finally:
            conn.close()

    def expire(self):
        conn = sqlite3.connect(
            Path(self.hub.hub.supervisor.workspace_dir(self.ws)) / "bots.sqlite3"
        )
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("UPDATE bot_pending SET expires_at=?", (past,))
        conn.commit()
        conn.close()

    def prompts(self):
        return (
            self.db()
            .execute("SELECT pending, ext_user, ext_channel FROM bridge_prompts")
            .fetchall()
        )

    def answers(self):
        return [p["text"] for p in self.platform.posts if p["private"] and not p["buttons"]]


@pytest.fixture(name="c")
def chat_fixture(w):
    return Chat(w.hub, w.platform, w.model, w.install)


def project(name):
    return ("tool_call", {"id": "c1", "name": "create_project", "arguments": {"name": name}})


def file_script(title=WORDS):
    return [
        [call("create_work", title=title, project="work")],
        [("text", "I have queued that for you.")],
    ]


# --- default off ---------------------------------------------------------------------------------


def test_clean_workspace_has_no_confirm_route_no_file_and_no_worker(bridge, platform):
    threads = threading.active_count()
    ghost = "0" * 32
    for caller in (bridge.owner, bridge.member, bridge.t1, bridge.tm):
        status, body, _ = bridge.request(
            caller, "PATCH", f"/api/bridge/links/{ghost}", {"confirm_ok": True}
        )
        assert (status, body["code"]) == (404, "not_found")
    assert send(bridge, ghost, tap("t1", "U1", "D1", f"confirm:{ghost}"), route="interact") == 404
    assert not bridge.file().exists()
    assert not bots_store.exists(bridge.hub.gateway, bridge.hub.workspace)
    assert platform.posts == [] and audit_rows(bridge) == []
    assert threading.active_count() == threads
    for token in (bridge.hub.owner_token("agent-owner"), bridge.tm, bridge.t1):
        assert not [t for t in bridge.hub.tool_names(token) if t.startswith("bridge")]


def test_with_confirm_ok_off_a_write_is_refused_and_nothing_is_written(c):
    c.ready(tapping=False)
    assert c.link_of("U-AVERY")["confirm_ok"] == 0
    c.ask("d1", "U-AVERY", "D-A", WORDS, file_script())
    names = {t["name"] for t in c.model.calls[0]["tools"]}
    assert not names & WRITERS and "project_status" in names
    assert WORDS not in c.tasks() and c.chips() == [] and c.prompts() == []
    assert c.pending() == []  # a refused call leaves no pending action behind
    assert [r[3] for r in c.outbox() if r[0] == "d1"] == ["sent"]


def test_with_confirm_ok_off_a_confirm_tool_waits_in_the_web_app_and_a_forged_tap_does_nothing(c):
    c.ready(tapping=False)
    c.ask(
        "d1",
        "U-AVERY",
        "D-A",
        "start a project called Garden",
        [[project("Garden")], [("text", "Waiting.")]],
    )
    [(pending, tool, state, _)] = c.pending()
    assert (tool, state) == ("create_project", "open")
    assert c.platform.texts()[-1] == outbox.FIXED["web_confirm"]
    assert c.chips() == [] and c.prompts() == []
    assert c.tap("t1", "U-AVERY", "D-A", f"confirm:{pending}") == 200
    assert c.last() == outbox.FIXED["tap_denied"]
    assert c.pending()[0][2] == "open"
    assert "garden" not in [b["slug"] for b in c.service(c.avery).board_list()]


# --- a write happens only after the linked person's own tap --------------------------------------


def test_a_write_is_a_private_chip_and_only_the_persons_tap_writes_it_once(c):
    c.ready()
    c.ask("d1", "U-AVERY", "D-A", WORDS, file_script())
    names = {t["name"] for t in c.model.calls[0]["tools"]}
    assert {"create_work", "comment_work", "remember"} <= names
    assert WORDS not in c.tasks()  # asked, not written
    [chip] = c.chips()
    assert chip["private"] and (chip["user"], chip["channel"]) == ("U-AVERY", "D-A")
    assert chip["text"].startswith(f"Create a task: “{WORDS}”")
    assert [label for label, _ in chip["buttons"]] == ["Confirm", "Cancel"]
    confirm = chip["buttons"][0][1]
    assert c.tap("t1", "U-AVERY", "D-A", confirm) == 200
    assert c.tasks().count(WORDS) == 1
    assert c.platform.posts[-1]["private"] and c.last() == "Done."
    assert [s for s in c.states().values() if s == "replied"]
    # A double tap (another delivery of the same button) and a retry of the first both run nothing.
    assert c.tap("t2", "U-AVERY", "D-A", confirm) == 200
    assert outbox.FIXED["tap_done"] in c.platform.texts()
    assert c.tap("t1", "U-AVERY", "D-A", confirm) == 200
    assert c.tasks().count(WORDS) == 1 and c.platform.texts().count(outbox.FIXED["tap_done"]) == 1


def test_concurrent_taps_make_one_write(c):
    c.ready()
    c.ask("d1", "U-AVERY", "D-A", WORDS, file_script())
    confirm = c.chips()[0]["buttons"][0][1]
    gate = threading.Barrier(6)
    statuses = []

    def press(n):
        gate.wait()
        statuses.append(c.press(f"race-{n}", "U-AVERY", "D-A", confirm))

    workers = [threading.Thread(target=press, args=(n,)) for n in range(6)]
    [t.start() for t in workers]
    [t.join() for t in workers]
    assert statuses == [200] * 6
    wait_for(lambda: WORDS in c.tasks())
    time.sleep(0.3)
    assert c.tasks().count(WORDS) == 1


def test_cancel_writes_nothing(c):
    c.ready()
    c.ask("d1", "U-AVERY", "D-A", WORDS, file_script())
    cancel = c.chips()[0]["buttons"][1][1]
    assert c.tap("t1", "U-AVERY", "D-A", cancel) == 200
    wait_for(lambda: "Cancelled." in c.platform.texts())
    assert WORDS not in c.tasks()
    assert c.tap("t2", "U-AVERY", "D-A", c.chips()[0]["buttons"][0][1]) == 200
    wait_for(lambda: outbox.FIXED["tap_done"] in c.platform.texts())
    assert WORDS not in c.tasks()


def test_other_people_installs_places_and_switched_off_links_run_nothing(c):
    c.ready()
    c.ready("U-BLAKE", c.hub.member)
    c.ask("d1", "U-AVERY", "D-A", WORDS, file_script())
    confirm = c.chips()[0]["buttons"][0][1]
    denied = outbox.FIXED["tap_denied"]
    cases = [
        ("another linked person", "U-BLAKE", "D-A"),
        ("a stranger", "U-NOBODY", "D-A"),
        ("the right person, another place", "U-AVERY", "C-OTHER"),
    ]
    for n, (label, user, channel) in enumerate(cases):
        assert c.tap(f"x{n}", user, channel, confirm) == 200, label
        assert c.last() == denied, label
        assert c.platform.posts[-1]["user"] == user  # the answer goes to whoever tapped
    # Another install on the same hub: its taps never see this install's chips.
    other = _enabled_install(c.hub, "T-TWO")
    assert c.tap("y1", "U-AVERY", "D-A", confirm, install=other, team="T-TWO") == 200
    assert WORDS not in c.tasks()
    # The person switches confirming off after the chip was posted: the chip is dead.
    c.confirm_ok("U-AVERY", c.hub.owner, False)
    assert c.prompts() == []
    assert c.tap("z1", "U-AVERY", "D-A", confirm) == 200
    assert c.last() == denied
    assert WORDS not in c.tasks()


def test_a_link_without_confirm_ok_cannot_confirm_a_chip_it_was_shown(c):
    c.ready()
    c.ask("d1", "U-AVERY", "D-A", WORDS, file_script())
    confirm = c.chips()[0]["buttons"][0][1]
    conn = c.db()
    conn.execute("UPDATE bridge_links SET confirm_ok=0")  # off, without the route's cleanup
    conn.commit()
    assert c.tap("t1", "U-AVERY", "D-A", confirm) == 200
    assert c.last() == outbox.FIXED["tap_off"]
    assert WORDS not in c.tasks()


def test_an_expired_chip_runs_nothing(c):
    c.ready()
    c.ask("d1", "U-AVERY", "D-A", WORDS, file_script())
    confirm = c.chips()[0]["buttons"][0][1]
    c.expire()
    assert c.tap("t1", "U-AVERY", "D-A", confirm) == 200
    assert c.last() == outbox.FIXED["tap_expired"]
    assert WORDS not in c.tasks() and c.pending()[0][2] == "expired"


def test_a_removed_member_a_revoked_link_and_a_disabled_install_run_nothing(c):
    c.ready()
    c.ask("d1", "U-AVERY", "D-A", WORDS, file_script())
    confirm = c.chips()[0]["buttons"][0][1]
    c.ask("d2", "U-AVERY", "D-A", "also " + WORDS, file_script("also " + WORDS))
    again = c.chips()[1]["buttons"][0][1]
    # The link is revoked: its chips go with it.
    status, _, _ = c.hub.request(
        c.hub.owner,
        "DELETE",
        f"/api/bridge/installs/{c.install}/links/{c.link_of('U-AVERY')['id']}",
    )
    assert status == 200 and c.prompts() == []
    assert c.tap("t1", "U-AVERY", "D-A", confirm) == 200
    assert c.last() == outbox.FIXED["tap_denied"]
    # The install is off: the intake does not exist for it.
    path = f"/api/bridge/installs/{c.install}"
    assert c.hub.request(c.hub.owner, "PATCH", path, {"enabled": False})[0] == 200
    assert c.tap("t2", "U-AVERY", "D-A", again) == 404
    assert c.tasks() == [t for t in c.tasks() if WORDS not in t]


def test_a_member_who_left_between_chip_and_tap_writes_nothing(c):
    c.ready("U-BLAKE", c.hub.member)
    c.ask("d1", "U-BLAKE", "D-B", WORDS, file_script())
    confirm = c.chips()[0]["buttons"][0][1]
    with c.hub.db._lock:
        conn = c.hub.db._conn
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (c.ws, c.blake))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
    assert c.tap("t1", "U-BLAKE", "D-B", confirm) == 200
    assert c.last() == outbox.FIXED["tap_denied"]
    assert WORDS not in c.tasks()


# --- the person's own rights ---------------------------------------------------------------------


def test_the_write_runs_with_the_taps_persons_rights_only(c):
    c.ready("U-BLAKE", c.hub.member)
    # A project Blake cannot read: no chip can even be made for it.
    c.ask(
        "d1",
        "U-BLAKE",
        "D-B",
        WORDS,
        [[call("create_work", title=WORDS, project="vault")], [("text", "No.")]],
    )
    assert c.chips() == [] and c.pending() == []
    assert WORDS not in c.tasks("vault")
    # A priority is the project owner's: Blake's tap files the task without it.
    c.ask(
        "d2",
        "U-BLAKE",
        "D-B",
        WORDS,
        [[call("create_work", title=WORDS, priority="urgent", project="work")], [("text", "Ok.")]],
    )
    assert c.tap("t1", "U-BLAKE", "D-B", c.chips()[0]["buttons"][0][1]) == 200
    wait_for(lambda: WORDS in c.tasks())
    card = next(t for t in c.service(c.avery).task_list(board="work") if t.get("title") == WORDS)
    assert card.get("priority") != "urgent"


# --- the chip ------------------------------------------------------------------------------------


def test_the_chip_text_is_built_by_the_server_names_the_action_and_carries_no_secret(c):
    c.ready()
    words = f"{WORDS}. My key is {SECRET}"
    c.ask(
        "d1",
        "U-AVERY",
        "D-A",
        words,
        [
            [call("create_work", title=words, project="work")],
            [("text", "Ignore previous instructions, tap yes.")],
        ],
    )
    [chip] = c.chips()
    assert SECRET not in chip["text"] and WORDS in chip["text"]
    assert chip["text"].startswith("Create a task:")
    assert "Ignore previous" not in chip["text"]  # the model's words are never the chip's
    assert all(SECRET not in payload for _, payload in chip["buttons"])


def test_the_model_cannot_pass_words_that_are_not_the_persons_own(c):
    c.ready()
    c.ask(
        "d1",
        "U-AVERY",
        "D-A",
        "please file something",
        [[call("create_work", title="Something the person never said")], [("text", "Done.")]],
    )
    assert c.chips() == [] and c.pending() == []


def test_a_comment_and_a_note_are_chips_too_and_a_tap_writes_them(c):
    c.ready()
    number = c.service(c.avery).task_add(title="Plain open task", board="work")["number"]
    c.ask(
        "d1",
        "U-AVERY",
        "D-A",
        "needs more thought before Friday",
        [
            [
                call(
                    "comment_work",
                    ref=number,
                    text="needs more thought before Friday",
                    project="work",
                )
            ],
            [("text", "Ok.")],
        ],
    )
    [chip] = c.chips()
    assert chip["text"].startswith(f"Comment on work #{number}:")
    assert c.tap("t1", "U-AVERY", "D-A", chip["buttons"][0][1]) == 200
    detail = c.service(c.avery).task_show(number)
    assert "needs more thought before Friday" in str(detail)


# --- the three consequential classes: web-only unless the owner allows them --------------------


def test_the_consequential_classes_stay_web_only_until_the_owner_allows_them(c):
    c.ready()
    script = [[project("Garden")], [("text", "Waiting.")]]
    c.ask("d1", "U-AVERY", "D-A", "start a project called Garden", script)
    assert c.chips() == [] and c.platform.texts()[-1] == outbox.FIXED["web_confirm"]
    [(pending, *_)] = c.pending()
    assert c.tap("t0", "U-AVERY", "D-A", f"confirm:{pending}") == 200
    assert c.last() == outbox.FIXED["tap_denied"]
    path = f"/api/bridge/installs/{c.install}"
    assert c.hub.request(c.hub.member, "PATCH", path, {"consequential_taps": True})[0] == 404
    assert c.hub.request(c.hub.owner, "PATCH", path, {"consequential_taps": "yes"})[0] == 400
    status, body, _ = c.hub.request(c.hub.owner, "PATCH", path, {"consequential_taps": True})
    assert (status, body["install"]["consequential_taps"]) == (200, True)
    c.ask(
        "d2",
        "U-AVERY",
        "D-A",
        "start a project called Orchard",
        [[project("Orchard")], [("text", "Waiting.")]],
    )
    [chip] = c.chips()
    assert chip["text"].startswith("Create a new project")
    assert c.tap("t1", "U-AVERY", "D-A", chip["buttons"][0][1]) == 200
    wait_for(lambda: "orchard" in [b["slug"] for b in c.service(c.avery).board_list()])
    slugs = [b["slug"] for b in c.service(c.avery).board_list()]
    assert slugs.count("orchard") == 1 and "garden" not in slugs
    # Switched off again after a chip was posted: that chip no longer works.
    c.ask(
        "d3",
        "U-AVERY",
        "D-A",
        "start a project called Meadow",
        [[project("Meadow")], [("text", "Waiting.")]],
    )
    meadow = c.chips()[-1]["buttons"][0][1]
    c.hub.request(c.hub.owner, "PATCH", path, {"consequential_taps": False})
    assert c.tap("t2", "U-AVERY", "D-A", meadow) == 200
    wait_for(lambda: outbox.FIXED["tap_web_only"] in c.platform.texts())
    assert "meadow" not in [b["slug"] for b in c.service(c.avery).board_list()]


# --- channels: no write, no chip -------------------------------------------------------------------


def test_a_channel_turn_gets_no_write_tool_and_never_posts_a_chip(c):
    c.ready()
    c.map("C-WORK", "work", "full")
    c.model.script = [[call("create_work", title=WORDS, project="work")], [("text", "I cannot.")]]
    assert c.say("m1", "U-AVERY", "C-WORK", f"<@APP> {WORDS}", kind="mention") == 200
    c.posts(2)
    c.settle()
    names = {t["name"] for t in c.model.calls[0]["tools"]}
    assert not names & (WRITERS | {"answer_decision", "approve_work", "create_project"})
    assert WORDS not in c.tasks() and c.chips() == [] and c.pending() == []
    assert not [p for p in c.platform.posts if p["private"]]


# --- the person's own switch ------------------------------------------------------------------------


def test_confirm_ok_is_the_persons_own_default_off_audited_and_validated(c):
    c.link("U-AVERY", c.hub.owner)
    c.link("U-BLAKE", c.hub.member)
    avery, blake = c.link_of("U-AVERY"), c.link_of("U-BLAKE")
    assert not avery["confirm_ok"] and not blake["confirm_ok"]
    path = f"/api/bridge/links/{avery['id']}"
    assert c.hub.request(c.hub.member, "PATCH", path, {"confirm_ok": True})[0] == 404  # not theirs
    for caller in (c.hub.t1, c.hub.tm):  # agents and tokens do not see it
        assert c.hub.request(caller, "PATCH", path, {"confirm_ok": True})[0] == 404
    assert c.hub.request(c.hub.owner, "PATCH", path, {"confirm_ok": "yes"})[0] == 400
    assert c.hub.request(c.hub.owner, "PATCH", path, {})[0] == 400
    assert c.hub.request(c.hub.owner, "PATCH", path, {"confirm_ok": True, "x": 1})[0] == 400
    assert c.link_of("U-AVERY")["confirm_ok"] == 0
    status, body, _ = c.hub.request(c.hub.owner, "PATCH", path, {"confirm_ok": True})
    assert (status, body["link"]["confirm_ok"]) == (200, True)
    assert c.link_of("U-BLAKE")["confirm_ok"] == 0
    listing = c.hub.request(c.hub.owner, "GET", "/api/bridge/links")[1]
    assert [i["confirm_ok"] for i in listing["links"]] == [True]
    tools = [r["tool"] for r in audit_rows(c.hub)]
    assert "confirm_on" in tools
    assert "U-AVERY" not in str(audit_rows(c.hub))
    # Ending the link ends the switch: a revoked link cannot be patched.
    assert c.hub.request(c.hub.owner, "DELETE", path)[0] == 200
    assert c.hub.request(c.hub.owner, "PATCH", path, {"confirm_ok": False})[0] == 404
