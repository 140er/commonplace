"""Shared bots, effective access: bot access list ∩ the talker ∩ the owner, enforced per layer.

Generic members: Avery owns the workspace (and reads private projects), Blake and Casey are
members who cannot. No share routes exist yet, so a bot is marked shared through the store's own
``share`` (fixture only). A bot that is not shared behaves as before.
"""

import json
import sqlite3

import pytest

from commonplace.bots import (
    access,
    context,
    machines,
    sharing_setting,
    tools,
    tools_machines,
)
from commonplace.chat import model_stream
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_app_grant import app as app_token
from tests.test_bots_project_tools import OPTIONS, confirm, question
from tests.test_bots_turn import Fake, Stream, call, configure
from tests.test_commonplace_boards import service

ROUTE = "/api/bots/{}/access"
WORDS = "zebra quartz lantern 7731"


def entry(kind, ref, mode):
    return {"kind": kind, "ref": ref, "mode": mode}


class Team:
    """Avery (workspace owner), Blake and Casey (members); projects ``docs`` (everyone) and
    ``vault`` (a private wing only a workspace owner reads); sharing switched on."""

    def __init__(self, hub):
        self.hub = hub
        self.gateway = hub.hub.gateway
        self.workspace = hub.hub.workspace
        self.avery, self.blake = hub.hub.admin, hub.member_user
        self.casey = hub.hub.db.create_user("casey@example.com")
        hub.hub.db.add_member(self.workspace, self.casey, "member", actor=self.avery)
        self.auth = {
            self.avery: hub.owner,
            self.blake: hub.member,
            self.casey: hub.cookie("casey@example.com", self.workspace),
        }
        boards = service(hub.hub, hub.person(self.avery))
        boards.board_add(slug="docs", name="Docs", wing="shared")
        boards.board_add(slug="vault", name="Vault", wing="private")
        sharing_setting.set_enabled(hub.hub.db, hub.person(self.avery), True)
        self.store = hub.store()

    def bot(self, owner=None, entries=(), members=(), board=None, name="Team"):
        """A shared custom bot with ``entries`` granted (set straight in the store)."""
        owner = owner or self.avery
        bot_id = self.store.create(owner, name, board=board)["id"]
        for member in members or (self.blake,):
            self.store.share(bot_id, owner, member, True, False)
        if entries:
            version = self.store.access_get(bot_id, owner)["version"]
            self.store.access_set(bot_id, owner, version, list(entries))
        return bot_id

    def turn(self, bot_id, user, text="Hello there"):
        bot = self.store.viewable(bot_id, user)
        message = {"id": 1, "text": text}
        turn = tools.Turn(self.gateway, self.workspace, user, bot, message, "request-direct")
        if not bot["board"]:  # what a running turn offers a model to name
            turn.projects = list(turn.effective().asker)
        return turn

    def put(self, bot_id, entries, auth=None, version=None):
        auth = auth or self.hub.owner
        if version is None:
            version = self.hub.get(auth, ROUTE.format(bot_id))[1].get("version", 0)
        body = {"version": version, "entries": entries}
        return self.hub.request(auth, "PUT", ROUTE.format(bot_id), body)

    def audit(self):
        return [
            r
            for r in self.hub.hub.db.audit_entries(self.workspace)
            if r["action"] == "bot_access:set"
        ]

    def log(self, bot_id, member=None):
        """The log rows of grant, change and remove (share rows are the share's own)."""
        rows = self.store.access_log(bot_id, member or self.avery)
        return [r for r in rows if r["action"] in ("grant", "change", "remove")]


@pytest.fixture
def fake(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(model_stream, "stream", fake)
    return fake


@pytest.fixture(name="team")
def team_fixture(bots_hub):
    return Team(bots_hub)


# The reach of a turn: grant ∩ talker ∩ owner


@pytest.mark.parametrize(
    ("label", "owner", "talker", "entries", "reads", "writes"),
    [
        ("no grant reaches nothing", "avery", "blake", [], set(), set()),
        ("read grant", "avery", "blake", [("project", "docs", "read")], {"docs"}, set()),
        ("write grant", "avery", "blake", [("project", "docs", "write")], {"docs"}, {"docs"}),
        ("the owner talking", "avery", "avery", [("project", "docs", "write")], {"docs"}, {"docs"}),
        # The owner reads a project the member cannot: the member never reaches it.
        ("owner lends vault", "avery", "blake", [("project", "vault", "read")], set(), set()),
        (
            "vault for its reader",
            "avery",
            "avery",
            [("project", "vault", "write")],
            {"vault"},
            {"vault"},
        ),
        # The talker reads what the owner cannot: the grant goes dormant.
        ("owner lost vault", "blake", "avery", [("project", "vault", "read")], set(), set()),
        (
            "owner member, docs",
            "blake",
            "avery",
            [("project", "docs", "write")],
            {"docs"},
            {"docs"},
        ),
        (
            "both grants, one reachable",
            "avery",
            "casey",
            [("project", "docs", "read"), ("project", "vault", "write")],
            {"docs"},
            set(),
        ),
    ],
)
def test_a_turn_reaches_only_grant_and_talker_and_owner(
    team, label, owner, talker, entries, reads, writes
):
    ids = {"avery": team.avery, "blake": team.blake, "casey": team.casey}
    bot_id = team.bot(
        ids[owner],
        [entry(*e) for e in entries],
        members=(ids[talker],) if talker != owner else (team.casey,),
    )
    turn = team.turn(bot_id, ids[talker])
    effective = turn.effective()
    assert set(turn.readable()) == reads, label
    assert {slug for slug in ("docs", "vault") if effective.can_write(slug)} == writes, label


def test_a_project_outside_the_grant_is_refused_by_who_asks(team):
    # Casey reads docs but the bot may not use it; Casey cannot read vault at all.
    bot_id = team.bot(entries=[entry("project", "vault", "read")], members=(team.casey,))
    turn = team.turn(bot_id, team.casey)
    with pytest.raises(access.OutsideBot) as outside:
        turn.board({"project": "docs"})
    assert outside.value.code == "outside_bot_access"
    assert outside.value.text == "This bot is not allowed to use that project. Its owner sets that."
    with pytest.raises(tools.Gone):  # not found, as for a project that does not exist
        turn.board({"project": "vault"})
    with pytest.raises(tools.Gone):
        turn.board({"project": "no-such-project"})
    # The owner can read vault and docs: docs is outside the bot, vault is granted.
    avery = team.turn(bot_id, team.avery)
    assert avery.board({"project": "vault"}) == "vault"
    with pytest.raises(access.OutsideBot):
        avery.board({"project": "docs"})
    for text in access.SENTENCES.values():  # fixed text, never a project name
        assert "docs" not in text and "vault" not in text


def test_a_grant_goes_dormant_when_its_owner_loses_access(team):
    bot_id = team.bot(entries=[entry("project", "docs", "write")])
    blake = team.turn(bot_id, team.blake)
    assert blake.readable() == ["docs"]
    hub = team.hub.hub
    with hub.db._lock:
        hub.db._conn.execute(
            "UPDATE users SET disabled_at='2020-01-01T00:00:00Z' WHERE id=?", (team.avery,)
        )
    assert blake.readable() == []  # fresh on every call: nobody reaches what the owner lost
    with pytest.raises(access.OutsideBot):
        blake.board({"project": "docs"})


def test_an_unreadable_or_revoked_share_fails_closed(team):
    bot_id = team.bot(entries=[entry("project", "docs", "read")])
    turn = team.turn(bot_id, team.blake)
    assert turn.readable() == ["docs"]
    team.store.revoke(bot_id, team.avery, team.blake)
    assert turn.readable() == []
    assert turn.effective().shared


def test_a_bot_that_is_not_shared_is_unaffected_even_with_stored_rows(team):
    bot_id = team.store.create(team.blake, "Mine")["id"]
    path = team.store._path
    with sqlite3.connect(path) as conn:  # a stale list on a private bot is never consulted
        conn.execute(
            "INSERT INTO bot_access (bot_id,kind,ref,mode,set_by,set_at) VALUES (?,?,?,?,?,?)",
            (bot_id, "project", "vault", "write", team.blake, "2026-01-01T00:00:00Z"),
        )
    turn = team.turn(bot_id, team.blake)
    assert sorted(turn.readable()) == ["docs", "work"]  # their own reach, grants or not
    assert not turn.effective().shared
    assert turn.board({"project": "docs"}) == "docs"
    offered = {t.name for t in tools.offered(turn)}
    assert "my_access" not in offered and {"remember", "list_machines", "create_work"} <= offered


# Tools: registration, offering and the per-call gate


def _tool(name, reach, scope="any", **extra):
    return tools.Tool(
        name=name,
        description="x",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        kind="read",
        run=lambda turn, args: tools.Result(True),
        label=lambda turn, args: "x",
        scope=scope,
        reach=reach,
        **extra,
    )


@pytest.mark.parametrize(
    ("reach", "scope"),
    [
        (None, "any"),  # reach is required
        ("everything", "any"),
        ("project:read", "any"),  # a project reach needs a project scope
        ("none", "project"),  # a project tool reaches a project or a machine
        ("wing:read", "project"),
    ],
)
def test_registration_refuses_a_missing_or_inconsistent_reach(reach, scope):
    with pytest.raises(ValueError, match="reach|project"):
        tools.register(_tool("probe_tool", reach, scope))
    assert "probe_tool" not in tools.REGISTRY


def test_every_registered_tool_has_a_reach():
    assert tools.REGISTRY
    assert all(t.reach in access.REACHES for t in tools.REGISTRY.values())
    assert tools.REGISTRY["remember"].reach == tools.REGISTRY["my_access"].reach == "none"
    assert tools.REGISTRY["create_work"].reach == "project:write"
    assert tools.REGISTRY["list_work"].reach == "project:read"
    assert tools.REGISTRY["start_work"].reach == "machine"
    assert tools.REGISTRY["create_project"].reach == "wing:write"


def test_a_shared_bot_is_offered_only_tools_its_list_names_and_its_reach_covers(team):
    grants = [entry("project", "docs", "read"), entry("tool", "list_work", "use")]
    grants += [entry("tool", "create_work", "use"), entry("tool", "start_work", "use")]
    turn = team.turn(team.bot(entries=grants), team.blake)
    offered = {t.name for t in tools.offered(turn)}
    # the built-ins always; list_work is named and read-covered; create_work needs a write grant
    # and start_work a machine grant, so neither is offered; unnamed tools never are.
    assert offered == {"remember", "forget", "my_access", "list_work"}
    writer = team.turn(
        team.bot(entries=[entry("project", "docs", "write"), *grants[1:3]], name="Writer"),
        team.blake,
    )
    assert {t.name for t in tools.offered(writer)} == offered | {"create_work"}


def test_a_fresh_share_offers_only_the_built_ins(team):
    turn = team.turn(team.bot(), team.blake)
    assert {t.name for t in tools.offered(turn)} == {"remember", "forget", "my_access"}
    assert turn.readable() == []


def test_the_gate_checks_every_call_against_the_current_list(team):
    grants = [entry("project", "docs", "read"), entry("tool", "create_work", "use")]
    bot_id = team.bot(entries=grants)
    turn = team.turn(bot_id, team.blake, text="File this")
    create = tools.REGISTRY["create_work"]
    with pytest.raises(access.ReadOnly) as read_only:  # a read grant cannot write
        tools.gate(turn, create, {"project": "docs", "title": "File this"})
    assert read_only.value.text == "This bot may only read that project."
    owner = team.avery
    version = team.store.access_get(bot_id, owner)["version"]
    team.store.access_set(bot_id, owner, version, [entry("project", "docs", "write"), grants[1]])
    tools.gate(turn, create, {"project": "docs", "title": "File this"})  # same turn, fresh list
    team.store.access_set(bot_id, owner, version + 1, [entry("project", "docs", "write")])
    with pytest.raises(access.OutsideBot) as named:  # the tool is no longer named
        tools.gate(turn, create, {"project": "docs", "title": "File this"})
    assert named.value.text == "This bot is not allowed to use that. Its owner sets that."


def test_every_project_tool_honours_the_grant(team):
    """A tool never reaches a project outside the effective access, read or write."""
    names = sorted(t.name for t in tools.REGISTRY.values() if t.reach.startswith("project:"))
    assert names  # the tool set under test is not empty
    grants = [entry("project", "docs", "read"), *(entry("tool", n, "use") for n in names)]
    turn = team.turn(team.bot(entries=grants), team.blake, text=WORDS)
    for name in names:
        tool = tools.REGISTRY[name]
        args = {"project": "docs"}
        if tool.reach == "project:write":
            with pytest.raises(access.ReadOnly):
                tools.gate(turn, tool, args)
        else:
            tools.gate(turn, tool, args)
        with pytest.raises(tools.Gone):  # a project the asker cannot read
            tools.gate(turn, tool, {"project": "vault"})
        with pytest.raises(access.OutsideBot):  # one the asker reads but the bot does not
            tools.gate(turn, tool, {"project": "work"})


def test_my_access_tells_what_is_reachable_without_leaking(team):
    grants = [entry("project", "docs", "write"), entry("project", "vault", "read")]
    turn = team.turn(team.bot(entries=grants), team.blake)
    tool = tools.REGISTRY["my_access"]
    listed = tool.run(turn, {}).data
    assert listed["projects"] == [{"project": "docs", "write": True}]  # vault: not for Blake
    assert "vault" not in json.dumps(listed)
    assert (
        tool.run(turn, {"project": "work"}).data["reason"]
        == access.SENTENCES["outside_bot_project"]
    )
    assert tool.run(turn, {"project": "vault"}).data == {
        "project": "vault",
        "reach": "none",
        "reason": "I can't find that.",
    }
    assert tool.run(turn, {"project": "docs"}).data == {"project": "docs", "reach": "write"}


def test_the_prompt_names_only_what_this_asker_may_reach(team):
    grants = [entry("project", "docs", "read"), entry("project", "vault", "read")]
    bot_id = team.bot(entries=grants)
    prompt = context.system_prompt(
        team.store.viewable(bot_id, team.blake), team.turn(bot_id, team.blake).effective()
    )
    assert '"docs"' in prompt and "vault" not in prompt
    plain = team.store.create(team.blake, "Mine")["id"]
    own = team.turn(plain, team.blake)
    assert "shared with a team" not in context.system_prompt(own.bot, own.effective())


def test_the_snapshot_is_built_only_for_a_project_in_the_reach(team):
    hub = team.hub
    plain = team.bot(entries=[entry("project", "vault", "read")], board="docs", name="Docs bot")
    turn = team.turn(plain, team.blake)
    principal = hub.person(team.blake)
    boards = service(hub.hub, principal)
    built = context.build(turn, principal, boards, [], [])
    assert built.snapshot == {}  # docs is not granted: nothing of it reaches the prompt
    granted = team.bot(entries=[entry("project", "docs", "read")], board="docs", name="Granted")
    built = context.build(team.turn(granted, team.blake), principal, boards, [], [])
    assert built.snapshot["project"] == "Docs"


# Machines


class Provider:
    """A provider whose machines depend on who asks: owners see two, members see the first."""

    name = "fake"
    started = []

    def machines(self, gateway, principal):
        rows = [("m-a", "alpha"), ("m-b", "beta")][: 2 if principal.role == "owner" else 1]
        return [
            {
                "id": mid,
                "label": label,
                "capabilities": {"start_agent": True, "run_command": False},
                "grants": {},
                "trust": "owner_attested",
            }
            for mid, label in rows
        ]

    def why_none(self, gateway, principal):
        return "No machine."

    def start_agent(self, gateway, principal, card, machine, request_key):
        Provider.started.append((card, machine))
        return {"job": "job-1", "state": "queued"}

    def start_status(self, gateway, principal, card):
        return []

    def cancel_start(self, gateway, principal, job):
        raise machines.Refused("start_not_found")


@pytest.fixture(name="provider")
def provider_fixture():
    Provider.started = []
    machines.register_provider(Provider())
    yield Provider
    machines.unregister_provider("fake")


def test_machines_are_grant_and_talker_and_owner(team, provider):
    grants = [entry("machine", "m-a", "use"), entry("machine", "m-b", "use")]
    bot_id = team.bot(entries=grants)
    assert [m["id"] for m in team.turn(bot_id, team.avery).machines()] == ["m-a", "m-b"]
    assert [m["id"] for m in team.turn(bot_id, team.blake).machines()] == ["m-a"]  # talker term
    only_b = team.bot(entries=[entry("machine", "m-b", "use")], name="Only B")
    assert team.turn(only_b, team.avery).machines()[0]["id"] == "m-b"
    assert team.turn(only_b, team.blake).machines() == []
    owned = team.bot(owner=team.blake, entries=grants, members=(team.avery,), name="Blake's")
    assert [m["id"] for m in team.turn(owned, team.avery).machines()] == ["m-a"]  # owner term
    plain = team.store.create(team.avery, "Mine")["id"]
    assert [m["id"] for m in team.turn(plain, team.avery).machines()] == ["m-a", "m-b"]


def test_list_machines_names_only_the_bots_machines_and_says_why_not(team, provider):
    grants = [entry("machine", "m-b", "use"), entry("tool", "list_machines", "use")]
    turn = team.turn(team.bot(entries=grants), team.avery)
    shown = tools_machines._list(turn, {})
    assert [m["id"] for m in shown.data["machines"]] == ["m-b"]
    assert shown.blocks == ({"type": "machines", "ids": ["m-b"]},)
    nothing = team.turn(team.bot(entries=[], name="Empty"), team.avery)
    reason = tools_machines._list(nothing, {}).data["reason"]
    assert reason == access.SENTENCES["outside_bot_other"]


def test_a_machine_outside_the_grant_is_refused_and_never_picked_by_the_runner(team, provider):
    boards = service(team.hub.hub, team.hub.person(team.avery))
    number = boards.task_add(title="Docs task", board="docs")["number"]
    grants = [
        entry("project", "docs", "write"),
        entry("machine", "m-a", "use"),
        entry("tool", "start_work", "use"),
    ]
    turn = team.turn(team.bot(entries=grants), team.avery)
    args = {"project": "docs", "ref": number}
    with pytest.raises(access.OutsideBot):  # the person may use m-b; the bot may not
        tools_machines._start_check(turn, {**args, "machine": "beta"})
    tools_machines._start_check(turn, args)  # no machine named: the one granted machine
    started = tools_machines._start(turn, args)
    assert started.ok and provider.started == [(number, "m-a")]
    none = team.turn(team.bot(entries=[grants[0], grants[2]], name="No machine"), team.avery)
    with pytest.raises(access.OutsideBot):  # no granted machine: not the runner's pick
        tools_machines._start_check(none, args)


def test_start_work_needs_a_write_grant_on_the_project(team, provider):
    grants = [
        entry("project", "docs", "read"),
        entry("machine", "m-a", "use"),
        entry("tool", "start_work", "use"),
    ]
    turn = team.turn(team.bot(entries=grants), team.avery)
    with pytest.raises(access.ReadOnly):
        tools.gate(turn, tools.REGISTRY["start_work"], {"project": "docs", "ref": 1})


# The access routes


def test_members_read_the_list_and_the_log_and_others_get_404(team):
    bot_id = team.bot(entries=[entry("project", "docs", "read")])
    hub = team.hub
    for auth in (hub.owner, hub.member):
        status, body, _ = hub.get(auth, ROUTE.format(bot_id))
        assert status == 200 and body["version"] == 1
        assert [(e["kind"], e["ref"], e["mode"]) for e in body["entries"]] == [
            ("project", "docs", "read")
        ]
        assert "set_by" not in body["entries"][0]
        status, body, _ = hub.get(auth, ROUTE.format(bot_id) + "/log")
        assert status == 200 and [r["action"] for r in body["log"]] == ["share", "grant"]
    assert hub.get(team.auth[team.casey], ROUTE.format(bot_id))[0] == 404  # no share
    assert hub.get(team.auth[team.casey], ROUTE.format(bot_id) + "/log")[0] == 404
    assert hub.get(hub.other, ROUTE.format(bot_id))[0] == 404  # other workspace
    assert hub.get(hub.t1, ROUTE.format(bot_id))[0] == 404  # agent token
    assert hub.get(None, ROUTE.format(bot_id))[0] == 401
    assert hub.get(hub.owner, ROUTE.format("no-such-bot"))[0] == 404


def test_switching_sharing_off_hides_the_list_from_members_only(team):
    bot_id = team.bot(entries=[entry("project", "docs", "read")])
    sharing_setting.set_enabled(team.hub.hub.db, team.hub.person(team.avery), False)
    assert team.hub.get(team.hub.member, ROUTE.format(bot_id))[0] == 404
    assert team.hub.get(team.hub.owner, ROUTE.format(bot_id))[0] == 200


def test_the_owner_edits_with_one_log_row_and_one_audit_row_per_change(team):
    bot_id = team.bot()
    hub = team.hub
    status, body, _ = team.put(
        bot_id, [entry("project", "docs", "read"), entry("tool", "list_work", "use")]
    )
    assert status == 200 and body["version"] == 1 and len(body["entries"]) == 2
    assert [r["action"] for r in team.log(bot_id)] == ["grant", "grant"]
    (audit,) = team.audit()
    assert audit["user_id"] == team.avery and audit["decision"] == "ok"
    assert audit["reason"].startswith(f"bot={bot_id} version=1 ")
    assert "grant:project:docs:-" in audit["reason"] and "grant:tool:list_work" in audit["reason"]
    # An equal payload is a no-op: no version step, no log row, no audit row.
    status, body, _ = team.put(
        bot_id, [entry("tool", "list_work", "use"), entry("project", "docs", "read")]
    )
    assert status == 200 and body["version"] == 1
    assert len(team.log(bot_id)) == 2 and len(team.audit()) == 1
    # A stale version is a conflict and nothing is written.
    status, body, _ = team.put(bot_id, [], version=0)
    assert (status, body["code"]) == (409, "conflict")
    assert len(team.log(bot_id)) == 2 and len(team.audit()) == 1
    # A change and a removal: one log row each, one audit row for the request.
    status, body, _ = team.put(bot_id, [entry("project", "docs", "write")])
    assert status == 200 and body["version"] == 2
    assert [r["action"] for r in team.log(bot_id)[2:]] == ["change", "remove"]
    assert len(team.audit()) == 2
    assert hub.get(hub.member, ROUTE.format(bot_id) + "/log")[1]["log"][-1]["version"] == 2


def test_only_the_owner_changes_the_list(team):
    bot_id = team.bot(entries=[entry("project", "docs", "read")])
    hub = team.hub
    status, body, _ = team.put(bot_id, [], auth=hub.member, version=1)
    assert (status, body["code"]) == (403, "owner_only")
    assert team.put(bot_id, [], auth=team.auth[team.casey], version=1)[0] == 404
    assert team.put(bot_id, [], auth=hub.other, version=1)[0] == 404
    status, _body, _ = hub.request(
        hub.t1, "PUT", ROUTE.format(bot_id), {"version": 1, "entries": []}
    )
    assert status == 404  # an agent cannot change access
    token, _cid = app_token(hub)
    status, _body, _ = hub.request(
        token, "PUT", ROUTE.format(bot_id), {"version": 1, "entries": []}
    )
    assert status == 404  # nor an app: web session only
    assert team.store.access_get(bot_id, team.avery)["version"] == 1
    assert team.audit() == []


def test_a_grant_beyond_the_owners_own_reach_is_refused_and_writes_nothing(team):
    bot_id = team.bot(owner=team.blake, members=(team.avery,))
    status, body, _ = team.put(bot_id, [entry("project", "vault", "read")], auth=team.hub.member)
    assert (status, body["code"]) == (403, "outside_your_scope")
    status, body, _ = team.put(bot_id, [entry("machine", "m-z", "use")], auth=team.hub.member)
    assert (status, body["code"]) == (403, "outside_your_scope")
    assert team.store.access_get(bot_id, team.blake) == {"version": 0, "entries": []}
    assert team.log(bot_id) == [] and team.audit() == []
    assert team.put(bot_id, [entry("project", "docs", "write")], auth=team.hub.member)[0] == 200


def test_a_kept_entry_the_owner_lost_does_not_block_saving_the_rest(team):
    bot_id = team.bot(owner=team.blake, members=(team.avery,))
    version = team.store.access_get(bot_id, team.blake)["version"]
    team.store.access_set(bot_id, team.blake, version, [entry("project", "vault", "read")])
    kept = [entry("project", "vault", "read"), entry("project", "docs", "read")]
    assert team.put(bot_id, kept, auth=team.hub.member)[0] == 200  # vault unchanged: kept dormant
    assert team.turn(bot_id, team.avery).readable() == ["docs"]


def test_bad_lists_are_refused_with_fixed_codes(team):
    bot_id = team.bot()
    for entries, status, code in [
        ([entry("wing", "shared", "read")], 400, "unsupported_kind"),
        ([entry("tool", "no_such_tool", "use")], 400, "invalid"),
        ([entry("project", "docs", "use")], 400, "invalid"),
        ([entry("project", "docs", "read")] * 2, 400, "invalid"),
        ([{"kind": "project", "ref": "docs"}], 400, "invalid"),
        ("docs", 400, "invalid"),
    ]:
        got, body, _ = team.put(bot_id, entries)
        assert (got, body["code"]) == (status, code), entries
    got, body, _ = team.hub.request(
        team.hub.owner, "PUT", ROUTE.format(bot_id), {"version": "0", "entries": []}
    )
    assert (got, body["code"]) == (400, "invalid")
    got, body, _ = team.hub.request(
        team.hub.owner, "PUT", ROUTE.format(bot_id), {"entries": [], "x": 1}
    )
    assert got == 400
    plain = team.store.create(team.avery, "Mine")["id"]  # a bot that is not shared has no list
    got, body, _ = team.put(plain, [entry("project", "docs", "read")])
    assert (got, body["code"]) == (400, "invalid")
    assert team.audit() == [] and team.log(bot_id) == []


def test_audit_rows_and_logs_carry_ids_and_codes_never_text(team):
    bot_id = team.bot(name=WORDS)
    team.put(bot_id, [entry("project", "docs", "read")])
    log = team.hub.get(team.hub.member, ROUTE.format(bot_id) + "/log")[1]["log"]
    assert set(log[-1]) == {
        "id",
        "version",
        "actor_id",
        "action",
        "kind",
        "ref",
        "mode_before",
        "mode_after",
        "member_id",
        "at",
    }
    (audit,) = team.audit()
    assert WORDS not in json.dumps([dict(r) for r in team.hub.hub.db.audit_entries(team.workspace)])
    assert audit["tool"] is None and audit["decision"] == "ok"


# End to end: a shared bot's turn over real HTTP with a scripted model


def ask(team, fake, bot_id, *calls, request_id="request-0001", text="File a note: Weekly sync"):
    configure(team.hub)
    fake.script = [list(calls), [("text", "Done.")]]
    return Stream(team.hub, team.hub.owner, bot_id, text, request_id).until_end()


def outcomes(events):
    return [
        (d["tool"], d["state"], d.get("code"))
        for e, d in events
        if e == "action" and d["state"] != "running"
    ]


def tool_names(fake):
    return {t["name"] for t in fake.calls[0]["tools"]}


def test_a_shared_bots_turn_offers_tools_by_its_list_and_refuses_the_rest(team, fake):
    grants = [
        entry("project", "docs", "write"),
        entry("project", "work", "read"),
        entry("tool", "list_work", "use"),
        entry("tool", "create_work", "use"),
    ]
    bot_id = team.bot(entries=grants)
    events = ask(
        team,
        fake,
        bot_id,
        call("list_work", "c1", project="docs"),
        call("list_work", "c2", project="vault"),  # the owner reads it; the bot may not
        call("create_work", "c3", project="work", title="Weekly sync"),  # read grant only
        call("create_work", "c4", project="docs", title="Weekly sync"),
    )
    assert tool_names(fake) == {"remember", "forget", "my_access", "list_work", "create_work"}
    assert "shared with a team" in fake.calls[0]["system"]
    assert outcomes(events) == [
        ("list_work", "ok", None),
        ("list_work", "refused", "outside_bot_access"),
        ("create_work", "refused", "bot_read_only"),
        ("create_work", "ok", None),
    ]
    assert "project_status" not in tool_names(fake)  # not in the list: never offered
    boards = service(team.hub.hub, team.hub.person(team.avery))
    assert [c["title"] for c in boards.task_list(board="docs", all_statuses=True)] == [
        "Weekly sync"
    ]
    assert boards.task_list(board="work", all_statuses=True) == []
    done = sorted(
        r["decision"]
        for r in team.hub.hub.db.audit_entries(team.workspace)
        if r["tool"] == "bot:create_work"
    )
    assert done == ["deny", "ok"]  # the refused read-only write is audited as a denial


def test_a_confirm_is_gated_again_at_the_tap(team, fake):
    grants = [entry("project", "docs", "write"), entry("tool", "answer_decision", "use")]
    bot_id = team.bot(entries=grants)
    number = question(team.hub, board="docs")
    events = ask(
        team,
        fake,
        bot_id,
        call("answer_decision", "c1", project="docs", ref=number, choice=OPTIONS[0]),
        text="Ship it",
    )
    (chip,) = [d for e, d in events if e == "block" and d["type"] == "confirm"]
    version = team.store.access_get(bot_id, team.avery)["version"]
    team.store.access_set(
        bot_id, team.avery, version, [entry("project", "docs", "read"), grants[1]]
    )
    status, body, _ = confirm(team.hub, bot_id, chip["pending"])
    assert status == 200
    shown = [b for b in body["message"]["blocks"] if b["type"] == "action"]
    assert [(b["ok"], b["code"]) for b in shown] == [(False, "bot_read_only")]
    boards = service(team.hub.hub, team.hub.person(team.avery))
    assert boards.task_show(number)["status"] == "review"  # nothing was answered
    team.store.access_set(bot_id, team.avery, version + 1, grants)  # the list changes back
    status, body, _ = confirm(team.hub, bot_id, chip["pending"])
    assert status == 200
    assert boards.task_show(number)["status"] != "review"  # the same chip now runs, once


def test_an_unshared_bot_is_untouched_when_sharing_is_off(team, fake):
    sharing_setting.set_enabled(team.hub.hub.db, team.hub.person(team.avery), False)
    plain = team.store.create(team.avery, "Mine")["id"]
    ask(team, fake, plain, call("list_work", "c1", project="docs"))
    assert {"list_work", "create_work", "start_work", "list_machines"} <= tool_names(fake)
    assert "my_access" not in tool_names(fake)
    assert "shared with a team" not in fake.calls[0]["system"]
    work = [
        m
        for e, m in Stream(team.hub, team.hub.owner, plain, "Again", "request-0002").until_end()
        if e == "done"
    ]
    assert work
