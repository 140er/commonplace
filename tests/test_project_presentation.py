"""G24: what project settings need from the server to edit the panel and replace the coordinator.

The write rules are the existing panel and board_set rules; these tests pin that they still hold
and that the settings read tells the page who may edit and what a coordinator change affects.
"""

from commonplace.projects import panel
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._projects_helpers import projects_hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import api
from tests.test_commonplace_subagent_asks import REVIEW_CONTEXT, REVIEW_OPTIONS, REVIEW_QUESTION

PANEL = {"format": panel.FORMAT, "title": "Atlas", "highlights": ["Beta ships Friday"], "links": []}


def view(h, auth):
    return h.get(auth, "/api/projects/work")[1]


def test_project_view_says_who_may_edit_the_panel(projects_hub):
    h = projects_hub
    owner, member = view(h, h.owner), view(h, h.member)
    assert owner["can_configure"] is True and owner["can_edit_panel"] is True
    # A member who may write the board edits the panel; they may not configure the project.
    assert member["can_configure"] is False and member["can_edit_panel"] is True


def test_member_cannot_replace_or_clear_the_coordinator(projects_hub):
    h = projects_hub
    actor = "conn:a1"
    status = h.request(h.owner, "PATCH", "/api/boards/work", {"coordinator": actor})[0]
    assert status == 200
    before = h.get(h.owner, "/api/boards/work/coordinator")[1]["coordinator"]
    assert before == actor
    for body in ({"coordinator": None}, {"coordinator": "conn:other"}):
        status, result = h.request(h.member, "PATCH", "/api/boards/work", body)[:2]
        assert status == 403, result
        assert h.request(None, "PATCH", "/api/boards/work", body)[0] == 401
    assert h.request(h.member, "GET", "/api/boards/work/coordinator")[0] == 403
    assert h.get(h.owner, "/api/boards/work/coordinator")[1]["coordinator"] == before


def test_member_panel_failure_keeps_the_old_panel(projects_hub):
    h = projects_hub
    assert h.request(h.owner, "PUT", "/api/projects/work/panel", {"panel": PANEL})[0] == 200
    bad = {"format": panel.FORMAT, "highlights": ["<b>x</b>"]}
    status, body = h.request(h.member, "PUT", "/api/projects/work/panel", {"panel": bad})[:2]
    assert status == 400 and body["code"] == "invalid_panel"
    assert h.get(h.owner, "/api/projects/work/panel")[1]["panel"] == PANEL


def effect(hub, fresh):
    status, result = api(hub, fresh.cookie, "GET", "/api/boards/work/coordinator")
    assert status == 200, result
    return result["effect"]


def test_coordinator_read_counts_what_a_change_affects(hub, fresh):
    assert effect(hub, fresh) == {"open_cards": 0, "waiting_asks": 0}
    orch = fresh.connect("orchestrator:work", trust="standard")
    fresh.owner.board_set("work", orch.principal.actor)
    sub = fresh.connect("sub", parent=orch.id)
    card = fresh.owner.task_add(board="work", title="Sub work", assignee=sub.principal.actor)
    sub.work.task_take_next(task=card["number"])
    asked = sub.work.ask(
        REVIEW_QUESTION, options=REVIEW_OPTIONS, task=card["number"], context=REVIEW_CONTEXT
    )
    assert asked["ask_to"] == orch.principal.actor
    assert effect(hub, fresh) == {"open_cards": 1, "waiting_asks": 1}
    # Clearing sends that ask to the owner: nothing is addressed to the old coordinator.
    status, result = api(hub, fresh.cookie, "PATCH", "/api/boards/work", {"coordinator": None})
    assert status == 200, result
    assert effect(hub, fresh)["waiting_asks"] == 0
