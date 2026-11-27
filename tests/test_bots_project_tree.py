"""Projects under a bot (the sidebar tree): seeding, moving, read-back, who may move, who sees what.

The store half uses a plain workspace directory; the API half runs over real HTTP with a workspace
owner, a member who cannot read a private project, and a person of another workspace. Names are
fixtures: nothing in the code knows them.
"""

import sqlite3
from types import SimpleNamespace

import pytest

from commonplace.bots.project_links import fold
from commonplace.bots.store import BotStore, Invalid, Missing
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service

BOARDS = [
    ("blue-eye", "Blue Eye"),
    ("halo-avatar", "HALO avatar"),
    ("phone-relay", "Phone relay"),
    ("commonplace", "commonplace"),
    ("loose", "Loose ends"),
]


@pytest.fixture(name="bots")
def bots_fixture(tmp_path):
    supervisor = SimpleNamespace(workspace_dir=lambda workspace: str(tmp_path / workspace))
    opened = [BotStore(supervisor, "ws1")]
    opened[0].path = tmp_path / "ws1" / "bots.sqlite3"
    opened[0].reopen = lambda: opened.append(BotStore(supervisor, "ws1")) or opened[-1]
    yield opened[0]
    for each in opened:
        each.close()


def tree(bots, user="u1"):
    """``{bot name: [slug, ...]}`` for the live bots with at least one project, from the links."""
    links = bots.project_links(user)
    names = {b["id"]: b["name"] for b in bots.roster(user)}
    out: dict = {}
    for slug, bot_id in links.items():
        out.setdefault(names[bot_id], []).append(slug)
    return {name: sorted(slugs) for name, slugs in out.items()}


def test_fold_ignores_case_and_punctuation():
    assert fold("HALO avatar") == fold("halo-avatar") == "haloavatar"
    assert fold("  Phone_relay!! ") == fold("phone-relay")
    assert fold("") == fold(None) == fold("--") == ""


def test_each_project_starts_under_its_own_project_bot(bots):
    bots.ensure_defaults("u1", BOARDS)
    links = bots.project_links("u1")
    project_bots = {b["board"]: b["id"] for b in bots.roster("u1") if b["kind"] == "project"}
    assert links == project_bots and set(links) == {slug for slug, _ in BOARDS}
    general = next(b for b in bots.roster("u1") if b["kind"] == "general")
    assert general["id"] not in links.values()  # a bot without projects still exists, empty


def test_a_project_without_a_project_bot_goes_to_the_custom_bot_that_has_its_name(bots):
    # Fifty live bots stop the defaults, so only the name can place a project.
    named = {
        name: bots.create("u1", name)["id"]
        for name in ("Blue Eye", "halo AVATAR", "Phone-relay", "Commonplace", "Unrelated")
    }
    bots.seed_projects("u1", BOARDS)
    links = bots.project_links("u1")
    assert links == {
        "blue-eye": named["Blue Eye"],
        "halo-avatar": named["halo AVATAR"],
        "phone-relay": named["Phone-relay"],
        "commonplace": named["Commonplace"],
    }
    assert "loose" not in links  # unmatched: Unassigned


def test_the_link_to_the_project_bot_wins_over_a_name_match(bots):
    twin = bots.create("u1", "Blue Eye")["id"]
    bots.ensure_defaults("u1", [("blue-eye", "Blue Eye")])
    project_bot = next(b["id"] for b in bots.roster("u1") if b["kind"] == "project")
    assert bots.project_links("u1") == {"blue-eye": project_bot} and twin != project_bot


def test_two_bots_with_the_same_name_are_ambiguous_and_leave_the_project_unassigned(bots):
    bots.create("u1", "Blue Eye")
    bots.create("u1", "blue-eye")
    bots.seed_projects("u1", [("blue-eye", "Blue Eye")])
    assert bots.project_links("u1") == {}


def test_an_archived_bot_is_never_a_target_and_its_links_read_as_unassigned(bots):
    bots.ensure_defaults("u1", [("blue-eye", "Blue Eye")])
    project_bot = next(b["id"] for b in bots.roster("u1") if b["kind"] == "project")
    bots.update(project_bot, "u1", archived=True)
    assert bots.project_links("u1") == {}
    bots.seed_projects("u1", [("blue-eye", "Blue Eye")])  # the row is kept, not redone
    bots.update(project_bot, "u1", archived=False)
    assert bots.project_links("u1") == {"blue-eye": project_bot}


def test_seeding_is_idempotent_and_never_moves_or_deletes(bots):
    bots.ensure_defaults("u1", BOARDS)
    before = bots.project_links("u1")
    rows = (
        sqlite3.connect(bots.path).execute("SELECT * FROM bot_projects ORDER BY board").fetchall()
    )
    target = bots.create("u1", "Elsewhere")["id"]
    bots.move_project("u1", "blue-eye", target)
    moved = bots.project_links("u1")
    for _ in range(3):
        bots.ensure_defaults("u1", BOARDS)
        bots.seed_projects("u1", BOARDS)
    assert bots.project_links("u1") == moved and moved != before
    again = (
        sqlite3.connect(bots.path).execute("SELECT * FROM bot_projects ORDER BY board").fetchall()
    )
    assert len(again) == len(rows) and {r[1] for r in again} == {r[1] for r in rows}


def test_reopening_a_file_from_before_the_link_table_adds_it_and_loses_nothing(bots):
    bots.ensure_defaults("u1", BOARDS)
    bots.close()
    conn = sqlite3.connect(bots.path)
    conn.execute("DROP TABLE bot_projects")  # a file written by the release before this one
    conn.commit()
    conn.close()
    count = len(bots.reopen().roster("u1"))
    again = bots.reopen()
    assert again.project_links("u1") == {}
    again.ensure_defaults("u1", BOARDS)
    assert len(again.roster("u1")) == count and len(again.project_links("u1")) == len(BOARDS)


def test_move_reads_back_and_a_project_has_at_most_one_bot(bots):
    bots.ensure_defaults("u1", BOARDS)
    a, b = bots.create("u1", "A")["id"], bots.create("u1", "B")["id"]
    assert bots.move_project("u1", "blue-eye", a) is not None  # was under its project bot
    assert bots.move_project("u1", "blue-eye", b) == a
    assert bots.project_links("u1")["blue-eye"] == b
    assert [s for s, bot in bots.project_links("u1").items() if bot == a] == []
    for slug in ("halo-avatar", "phone-relay"):
        bots.move_project("u1", slug, b)  # one bot owns several
    assert tree(bots)["B"] == ["blue-eye", "halo-avatar", "phone-relay"]


def test_unassigning_is_remembered_and_not_undone_by_the_next_listing(bots):
    bots.ensure_defaults("u1", BOARDS)
    project_bot = bots.project_links("u1")["loose"]
    assert bots.release_project("u1", project_bot, "loose") is True
    assert bots.release_project("u1", project_bot, "loose") is False  # idempotent
    bots.ensure_defaults("u1", BOARDS)
    assert "loose" not in bots.project_links("u1")
    assert bots.move_project("u1", "loose", None) is None  # to Unassigned directly: still fine


def test_release_only_acts_on_the_bot_that_holds_the_project(bots):
    bots.ensure_defaults("u1", BOARDS)
    other = bots.create("u1", "Other")["id"]
    assert bots.release_project("u1", other, "loose") is False
    assert "loose" in bots.project_links("u1")


def test_links_and_moves_are_per_person(bots):
    bots.ensure_defaults("u1", BOARDS)
    bots.ensure_defaults("u2", BOARDS)
    mine = bots.create("u1", "Mine")["id"]
    bots.move_project("u1", "blue-eye", mine)
    assert bots.project_links("u2")["blue-eye"] != mine
    with pytest.raises(Missing):
        bots.move_project("u2", "blue-eye", mine)  # a foreign bot looks unknown
    with pytest.raises(Missing):
        bots.release_project("u2", mine, "blue-eye")
    assert bots.project_links("u1")["blue-eye"] == mine


def test_move_refuses_bad_input_and_an_archived_bot(bots):
    bots.ensure_defaults("u1", BOARDS)
    bot_id = bots.create("u1", "Gone")["id"]
    bots.update(bot_id, "u1", archived=True)
    with pytest.raises(Invalid):
        bots.move_project("u1", "blue-eye", bot_id)
    for board in ("", None, 5):
        with pytest.raises(Invalid):
            bots.move_project("u1", board, bot_id)
    with pytest.raises(Missing):
        bots.move_project("u1", "blue-eye", "nope")


# The API


def under(hub, auth, bot_id):
    status, body, _ = hub.get(auth, f"/api/bots/{bot_id}/projects")
    assert status == 200, body
    return body["projects"]


def test_listing_carries_the_projects_under_each_bot(bots_hub):
    hub = bots_hub
    hub.add_board("garden", "Garden")
    bots = hub.by_board(hub.owner)
    assert bots["garden"]["projects"] == ["garden"] and bots["work"]["projects"] == ["work"]
    assert bots["general"]["projects"] == []
    assert {"id", "name", "kind", "board", "last", "unread", "waiting"} <= set(bots["garden"])


def test_the_owner_moves_a_project_and_reads_it_back(bots_hub):
    hub = bots_hub
    hub.add_board("garden", "Garden")
    bots = hub.by_board(hub.owner)
    status, made, _ = hub.post(hub.owner, "/api/bots", {"name": "Plans"})
    assert status == 201
    target = made["bot"]["id"]
    status, body, _ = hub.request(hub.owner, "PUT", f"/api/bots/{target}/projects/garden")
    assert (status, body) == (200, {"projects": ["garden"]})
    assert under(hub, hub.owner, target) == ["garden"]
    assert under(hub, hub.owner, bots["garden"]["id"]) == []
    listed = hub.by_board(hub.owner)
    assert listed["custom"]["projects"] == ["garden"] and listed["garden"]["projects"] == []
    # Unassigned: delete from the holder, read back, idempotent.
    for _ in range(2):
        status, body, _ = hub.request(hub.owner, "DELETE", f"/api/bots/{target}/projects/garden")
        assert (status, body) == (200, {"projects": []})
    assert all("garden" not in b["projects"] for b in hub.bots(hub.owner)["bots"])


def test_a_move_is_audited_with_ids_only_and_a_repeat_adds_no_row(bots_hub):
    hub = bots_hub
    target = hub.post(hub.owner, "/api/bots", {"name": "Plans"})[1]["bot"]["id"]
    path = f"/api/bots/{target}/projects/work"

    def rows():
        return [
            r
            for r in hub.hub.db.audit_entries(hub.hub.workspace)
            if r["action"] == "bot_project:move"
        ]

    assert hub.request(hub.owner, "PUT", path)[0] == 200
    assert hub.request(hub.owner, "PUT", path)[0] == 200  # already there
    assert len(rows()) == 1
    assert hub.request(hub.owner, "DELETE", path)[0] == 200
    assert hub.request(hub.owner, "DELETE", path)[0] == 200
    moves = rows()
    assert len(moves) == 2
    assert [("project=work" in r["reason"]) for r in moves] == [True, True]
    assert any("to=unassigned" in r["reason"] for r in moves)
    assert all("Plans" not in str(r) for r in moves)


def test_nobody_else_can_move_into_or_out_of_a_bot(bots_hub):
    hub = bots_hub
    owner_bot = hub.by_board(hub.owner)["work"]["id"]
    path = f"/api/bots/{owner_bot}/projects/work"
    for auth in (hub.member, hub.other):  # another person's bot id is simply unknown
        assert hub.request(auth, "PUT", path)[0] == 404
        assert hub.request(auth, "DELETE", path)[0] == 404
        assert hub.get(auth, f"/api/bots/{owner_bot}/projects")[0] == 404
    assert under(hub, hub.owner, owner_bot) == ["work"]


def test_a_member_holding_a_share_cannot_move_projects_under_the_shared_bot(bots_hub):
    hub = bots_hub
    assert hub.request(hub.owner, "PATCH", "/api/bots/settings", {"sharing": True})[0] == 200
    shared = hub.post(hub.owner, "/api/bots", {"name": "Team"})[1]["bot"]["id"]
    member_id = hub.member_user
    assert hub.post(
        hub.owner,
        f"/api/bots/{shared}/shares",
        {"user": member_id, "talk": True, "see_shared": False},
    )[0] in (200, 201)
    assert hub.bots(hub.member)["bots"], "the member lists the shared bot"
    path = f"/api/bots/{shared}/projects/work"
    assert hub.request(hub.member, "PUT", path)[0] == 403
    assert hub.request(hub.member, "DELETE", path)[0] == 403
    assert hub.get(hub.member, f"/api/bots/{shared}/projects")[0] == 403
    member_view = next(b for b in hub.bots(hub.member)["bots"] if b["id"] == shared)
    assert member_view["projects"] == []  # the owner's arrangement is not shown to a member
    assert under(hub, hub.owner, shared) == []


def test_a_member_arranges_only_their_own_tree(bots_hub):
    hub = bots_hub
    mine = hub.post(hub.member, "/api/bots", {"name": "Mine"})[1]["bot"]["id"]
    assert hub.request(hub.member, "PUT", f"/api/bots/{mine}/projects/work")[0] == 200
    assert under(hub, hub.member, mine) == ["work"]
    owner_work = hub.by_board(hub.owner)["work"]
    assert owner_work["projects"] == ["work"]  # the owner's tree did not change


def test_a_project_the_person_cannot_read_is_refused_and_never_listed(bots_hub):
    hub = bots_hub
    service(hub.hub, hub.person(hub.hub.admin)).board_add(
        slug="vault", name="Vault", wing="private"
    )
    owner = hub.by_board(hub.owner)
    assert owner["vault"]["projects"] == ["vault"]
    member_bots = hub.bots(hub.member)["bots"]
    assert all("vault" not in b["projects"] and b["board"] != "vault" for b in member_bots)
    mine = hub.post(hub.member, "/api/bots", {"name": "Mine"})[1]["bot"]["id"]
    status, body, _ = hub.request(hub.member, "PUT", f"/api/bots/{mine}/projects/vault")
    assert (status, body["code"]) == (400, "invalid")
    assert "vault" not in str(under(hub, hub.member, mine))
    # Even a link row for it (a project made private after the move) is not listed.
    hub.store().move_project(hub.member_user, "vault", mine)
    assert under(hub, hub.member, mine) == []
    assert all("vault" not in b["projects"] for b in hub.bots(hub.member)["bots"])


def test_bad_paths_and_bodies_are_refused(bots_hub):
    hub = bots_hub
    mine = hub.by_board(hub.owner)["general"]["id"]
    assert hub.request(hub.owner, "PUT", f"/api/bots/{mine}/projects")[0] == 404
    assert hub.request(hub.owner, "PUT", f"/api/bots/{mine}/projects/work/extra")[0] == 404
    assert hub.request(hub.owner, "PUT", f"/api/bots/{mine}/projects/missing")[0] == 400
    assert hub.request(hub.owner, "PUT", f"/api/bots/{mine}/projects/work", {"x": 1})[0] == 400
    assert hub.request(hub.owner, "PUT", "/api/bots/nope/projects/work")[0] == 404
    assert hub.request(hub.owner, "PATCH", f"/api/bots/{mine}/projects/work", {})[0] == 404
    archived = hub.post(hub.owner, "/api/bots", {"name": "Old"})[1]["bot"]["id"]
    hub.request(hub.owner, "PATCH", f"/api/bots/{archived}", {"archived": True})
    assert hub.request(hub.owner, "PUT", f"/api/bots/{archived}/projects/work")[0] == 400


def test_agent_tokens_and_anonymous_callers_are_refused(bots_hub):
    hub = bots_hub
    mine = hub.by_board(hub.owner)["work"]["id"]
    for method in ("GET", "PUT", "DELETE"):
        assert hub.request(hub.t1, method, f"/api/bots/{mine}/projects/work")[0] == 404
        assert hub.request(None, method, f"/api/bots/{mine}/projects/work")[0] in (401, 404)
