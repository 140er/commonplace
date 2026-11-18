"""The recovery view of Project settings: the REST contract it relies on (G20).

``web/static/orchstatus.js`` reads the restore status and the checkpoint (findings, steps)
and offers one action, acknowledging a restore. These tests pin that the server rules the
page leans on are unchanged: a member reads the restore state but cannot acknowledge it, a
wrong text or a refused call leaves the restore pending, the checkpoint carries the finding
and step fields the page shows, and a finding cannot be acknowledged without the owning
role's lease (so the page offers no such button). Names and tokens are synthetic.
"""

import json

import pytest

from commonplace import sessions
from commonplace.orch import store as orch_store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.runner_harness import http
from tests.test_orch_surfaces import BOARD, ROLE, Env

BASE = f"boards/{BOARD}/roles/{ROLE}"
EXPECTED = "effects after 2030-02-03T04:05:06Z may be unknown"


@pytest.fixture
def env(hub):
    return Env(hub)


def flag_restore(env):
    directory = env.hub.gateway.workspace_dir(env.ws)
    marker = {"version": 1, "backup_created_at": "2030-02-03T04:05:06Z", "id": "c" * 32}
    with open(f"{directory}/{orch_store.RESTORE_MARKER}", "w") as handle:
        json.dump(marker, handle)


def cursor_of(env):
    with env.runner_db() as conn:
        row = conn.execute("SELECT cursor, gen FROM orch_cursor WHERE board=?", (BOARD,)).fetchone()
    return None if row is None else tuple(row)


def member_cookie(hub):
    user = hub.db.create_user("member@example.com")
    hub.db.add_member(hub.workspace, user, "member", actor=hub.admin)
    link = sessions.create_login_link(hub.db, "member@example.com", hub.workspace)
    return f"{sessions.COOKIE}={sessions.redeem_login_link(hub.db, link)}"


def claim(env):
    status, claimed = env.rest("POST", f"{BASE}/resume", {}, token=env.a_token)
    assert status == 200, claimed
    return {"epoch": claimed["epoch"], "token": claimed["token"]}


def test_restore_state_is_readable_by_a_member_but_only_the_owner_can_acknowledge(env, hub):
    flag_restore(env)
    cookie = member_cookie(hub)
    status, state = http(hub.port, "GET", "/api/orch/restore", cookie=cookie)
    assert status == 200 and state["restore_pending"] is True
    assert state["ack_text"] == EXPECTED and state["after"] == "2030-02-03T04:05:06Z"
    status, body = http(
        hub.port, "POST", "/api/orch/restore/ack", cookie=cookie, body={"text": EXPECTED}
    )
    assert (status, body["code"]) == (403, "owner_only")
    assert env.person("GET", "restore")[1]["restore_pending"] is True


@pytest.mark.parametrize("text", ["", "effects may be unknown", EXPECTED + " ", EXPECTED.upper()])
def test_a_wrong_acknowledgement_text_leaves_the_restore_pending(env, text):
    flag_restore(env)
    cursor = cursor_of(env)
    status, body = env.person("POST", "restore/ack", {"text": text})
    assert status in (400, 403), body
    assert body["code"] in ("restore_text", "invalid_argument")
    state = env.person("GET", "restore")[1]
    assert state["restore_pending"] is True and state["acked_by"] is None
    assert cursor_of(env) == cursor, "no project was re-armed"


def test_an_unconfirmed_page_changes_nothing(env):
    """Opening the preview and cancelling is two reads: the pending state is identical."""
    flag_restore(env)
    first = env.person("GET", "restore")[1]
    second = env.person("GET", "restore")[1]
    assert first == second and first["restore_pending"] is True
    assert [e["kind"] for e in env.events()] == []


def test_a_restore_cannot_be_acknowledged_twice_and_the_status_names_who_did(env):
    flag_restore(env)
    status, body = env.person("POST", "restore/ack", {"text": EXPECTED})
    assert status == 200 and body["acknowledged"] is True
    status, body = env.person("POST", "restore/ack", {"text": EXPECTED})
    assert (status, body["code"]) == (409, "restore_not_pending")
    state = env.person("GET", "restore")[1]
    assert state["restore_pending"] is False and state["acked_by"] and state["acked_at"]


def test_the_checkpoint_carries_what_the_findings_and_steps_lists_show(env):
    lease = claim(env)
    card = env.boards.task_add("Synthetic todo card", board=BOARD, status="todo")["number"]
    status, step = env.rest(
        "POST",
        f"{BASE}/journal",
        {**lease, "step_key": "thread-1", "kind": "thread", "card": card, "detail": "builder"},
        token=env.a_token,
    )
    assert status == 200, step
    status, found = env.rest(
        "POST",
        f"{BASE}/findings",
        {**lease, "step_key": "f-1", "card": card, "detail": "Missing a test"},
        token=env.a_token,
    )
    assert status == 200, found
    status, checkpoint = env.person("GET", f"{BASE}/checkpoint")
    assert status == 200, checkpoint
    assert checkpoint["config"]["version"] >= 1 and checkpoint["lease"]["epoch"] == lease["epoch"]
    [finding] = checkpoint["findings"]
    assert (finding["card"], finding["detail"], finding["state"]) == (
        card,
        "Missing a test",
        "parked",
    )
    assert finding["reason"] == "no_owning_session"
    assert finding["destination"] == {"kind": "parked", "reason": "no_owning_session"}
    assert finding["owner_role"] is None
    [open_step] = checkpoint["open"]
    assert {"step_key", "kind", "state", "card", "epoch", "created_epoch", "job_id"} <= set(
        open_step
    )
    assert (open_step["kind"], open_step["card"]) == ("thread", card)


def test_an_owner_cannot_acknowledge_a_finding_without_the_owning_roles_lease(env):
    lease = claim(env)
    card = env.boards.task_add("Synthetic todo card", board=BOARD, status="todo")["number"]
    env.rest(
        "POST",
        f"{BASE}/findings",
        {**lease, "step_key": "f-1", "card": card, "detail": "Missing a test"},
        token=env.a_token,
    )
    finding = env.person("GET", f"{BASE}/checkpoint")[1]["findings"][0]
    status, body = env.person(
        "POST", f"{BASE}/findings/{finding['id']}", {"epoch": 1, "token": "guess"}
    )
    assert status in (403, 409), body
    assert env.person("GET", f"{BASE}/checkpoint")[1]["findings"][0]["state"] == "parked"
