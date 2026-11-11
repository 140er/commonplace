"""Owner-granted extra write wings for a runner worker (start, writes, authority, defaults)."""

import json

import pytest

from tests.runner_harness import (
    attest,
    card,
    cli_token,
    http,
    member,
    owners as owners,
    signup_hub as signup_hub,
)
from tests.test_runner_start import _accepted, _enroll, _machine_row, _ok, _start, _tool

WINGS = ["platform", "compute", "notes"]


def _patch(hub, machine, body, **auth):
    return http(hub.port, "PATCH", "/api/runners/" + machine["machine_id"], body=body, **auth)


def _granted(hub, owner, write_wings=("compute",)):
    """A machine that reads three wings and writes the first plus ``write_wings``.

    Starts below come from the signed-in owner, who can see every board."""
    machine = _enroll(hub, owner, wings=WINGS, max_in_flight=4)
    status, body = _patch(hub, machine, {"write_wings": list(write_wings)}, cookie=owner["cookie"])
    assert status == 200, (status, body)
    assert body["write_set"] == ["platform", *write_wings], body
    attest(hub, owner, machine)
    return machine


def _policy(hub, owner, machine):
    principal = hub.db.authenticate_connection(
        _machine_row(hub, owner, machine)["worker_connection_id"]
    )
    return principal.policy


def test_worker_starts_and_writes_cards_in_every_granted_write_wing(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    for board in ("compute", "platform"):
        task = card(hub, owner, board=board)
        _accepted(_start(hub, machine, task, owner=owner))
        worker = machine["worker_token"]
        _ok(_tool(hub, worker, "task_take_next", task=task["number"]))
        _ok(_tool(hub, worker, "task_comment", task=task["number"], text="Synthetic progress."))
    policy = _policy(hub, owner, machine)
    assert policy.write_wing == "platform" and policy.write_wings == ("compute",)
    assert policy.can_write_wing("compute") and not policy.can_write_wing("notes")


def test_wing_without_write_grant_is_still_outside_the_worker_wing(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    code, body = _start(hub, machine, card(hub, owner, board="notes"), owner=owner)
    assert code == 403 and body["error"].endswith("outside_worker_wing"), (code, body)
    assert not _policy(hub, owner, machine).can_write_wing("notes")
    # A card already reserved for the worker passes the board-write rule (own card), so
    # only the start check's write set keeps a read-only wing out.
    from commonplace.boards.store import BoardStore

    worker = hub.db.authenticate_connection(
        _machine_row(hub, owner, machine)["worker_connection_id"]
    )
    task = card(hub, owner, board="notes")
    with BoardStore(hub.gateway.workspace_dir(owner["workspace"])).transaction() as conn:
        conn.execute("UPDATE tasks SET assignee=? WHERE id=?", (worker.actor, task["number"]))
    code, body = _start(hub, machine, task, owner=owner)
    assert code == 403 and body["error"].endswith("outside_worker_wing"), (code, body)


@pytest.mark.parametrize("caller", ["member", "trusted", "worker", "foreign-owner"])
def test_only_the_workspace_owner_changes_the_write_set(signup_hub, owners, caller):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    if caller == "member":
        auth = {"cookie": member(hub, owner, owners["B"])["cookie"]}
    elif caller == "foreign-owner":
        auth = {"cookie": owners["B"]["cookie"]}
    elif caller == "worker":
        auth = {"token": machine["worker_token"]}
    else:
        _, token = cli_token(owner, "admin-agent", profile="standard", trust="trusted")
        auth = {"token": token}
    for body in ({"write_wings": []}, {"write_wings": ["compute", "notes"]}):
        status, result = _patch(hub, machine, body, **auth)
        assert status == (404 if caller == "foreign-owner" else 403), (status, result)
    row = _machine_row(hub, owner, machine)
    assert row["write_set"] == ["platform", "compute"] and row["binding_state"] == (
        "owner_attested"
    )
    assert _policy(hub, owner, machine).write_wings == ("compute",)


@pytest.mark.parametrize(
    "wing", ["private.notes", "private", "compute*", "missing", "Private-Notes"]
)
def test_private_pattern_and_unknown_wings_cannot_be_added(signup_hub, owners, wing):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    status, body = _patch(hub, machine, {"write_wings": ["compute", wing]}, cookie=owner["cookie"])
    assert (status, body) == (
        400,
        {"error": "choose existing nonprivate workspace wings without patterns"},
    )
    status, body = _patch(
        hub, machine, {"wings": [*WINGS, wing], "write_wings": [wing]}, cookie=owner["cookie"]
    )
    assert status == 400, (status, body)
    assert _machine_row(hub, owner, machine)["write_set"] == ["platform", "compute"]
    assert _policy(hub, owner, machine).write_wings == ("compute",)


def test_write_wings_must_be_granted_and_narrowing_wings_drops_them(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    _enroll(hub, owner, label="spare", wings=["shared", "extra"])
    machine = _granted(hub, owner)
    status, body = _patch(hub, machine, {"write_wings": ["extra"]}, cookie=owner["cookie"])
    assert (status, body) == (400, {"error": "write_wings must be granted wings"})
    status, body = _patch(hub, machine, {"wings": ["platform", "notes"]}, cookie=owner["cookie"])
    assert status == 200 and body["write_set"] == ["platform"], (status, body)
    assert body["write_wings"] == [] and body["binding_state"] == "unbound"
    assert _policy(hub, owner, machine).write_wings == ()


def test_write_set_change_is_audited_and_resets_the_binding(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    with hub.db._lock:
        rows = hub.db._conn.execute(
            "SELECT action, reason, target_ids FROM audit_log WHERE workspace_id=? "
            "AND action='runner.patch' ORDER BY seq",
            (owner["workspace"],),
        ).fetchall()
    assert [(r[0], machine["machine_id"] in r[2]) for r in rows] == [("runner.patch", True)]
    status, body = _patch(hub, machine, {"write_wings": []}, cookie=owner["cookie"])
    assert status == 200 and body["binding_state"] == "unbound", (status, body)
    with hub.db._lock:
        reasons = [
            r[0]
            for r in hub.db._conn.execute(
                "SELECT reason FROM audit_log WHERE workspace_id=? AND action='runner.patch' "
                "ORDER BY seq",
                (owner["workspace"],),
            )
        ]
    assert reasons == ["binding_reset;write_set +compute", "binding_reset;write_set -compute"]
    # Unchanged set: no reset and no diff; narrowing wings records the extras it drops;
    # swapping the first write wing records the new primary even with the same members.
    swap = {"wings": ["compute", "platform"], "write_wings": ["platform"]}
    for patch in (
        {"write_wings": []},
        {"write_wings": ["compute", "notes"]},
        {"wings": WINGS[:2]},
        swap,
    ):
        status, body = _patch(hub, machine, patch, cookie=owner["cookie"])
        assert status == 200, (status, body)
    with hub.db._lock:
        reasons = [
            r[0]
            for r in hub.db._conn.execute(
                "SELECT reason FROM audit_log WHERE workspace_id=? AND action='runner.patch' "
                "ORDER BY seq",
                (owner["workspace"],),
            )
        ]
    assert reasons[2:] == [
        "",
        "binding_reset;write_set +compute +notes",
        "binding_reset;write_set -notes",
        "binding_reset;primary platform->compute",
    ]


def _refused(response, text="no: this connection cannot change this board"):
    """The specific refusal (by default the write-set one), not just any error."""
    assert response.get("result", {}).get("isError"), response
    assert response["result"]["content"][0]["text"] == text, response


def test_submissions_follow_read_scope_not_the_write_set(signup_hub, owners):
    """Characterises existing behaviour (unchanged by write_wings): comments and asks are
    submissions, gated by the worker's read wings rather than its write set."""
    hub, owner = signup_hub, owners["A"]
    machine = _enroll(hub, owner, wings=WINGS)
    worker = machine["worker_token"]
    readable, unreadable = card(hub, owner, board="notes"), card(hub, owner)
    question = {
        "question": "Which synthetic option should this example card use next?",
        "options": ["First option", "Second option"],
        "context": "Synthetic context for a characterisation test.",
    }
    _ok(_tool(hub, worker, "task_comment", task=readable["number"], text="Synthetic note."))
    _ok(_tool(hub, worker, "ask", board="notes", **question))
    # Asking about an existing unassigned card hands it off, which needs write access.
    _refused(_tool(hub, worker, "ask", task=readable["number"], **question))
    # A card assigned to the worker can be handed off by asking, outside the write set too.
    from commonplace.boards.store import BoardStore

    actor = hub.db.authenticate_connection(
        _machine_row(hub, owner, machine)["worker_connection_id"]
    ).actor
    assigned = card(hub, owner, board="notes")
    with BoardStore(hub.gateway.workspace_dir(owner["workspace"])).transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='doing', assignee=? WHERE id=?", (actor, assigned["number"])
        )
    _ok(_tool(hub, worker, "ask", task=assigned["number"], **question))
    # Unreadable wings stay invisible rather than refused.
    _refused(
        _tool(hub, worker, "task_comment", task=unreadable["number"], text="Synthetic."),
        "no: card not found",
    )
    _refused(_tool(hub, worker, "ask", board="shared", **question), "no: board not found")


def _task_row(hub, owner, number):
    from commonplace.boards.store import BoardStore

    with BoardStore(hub.gateway.workspace_dir(owner["workspace"])).transaction() as conn:
        return dict(
            conn.execute(
                "SELECT id, title, status, assignee, return_to FROM tasks WHERE id=?", (number,)
            ).fetchone()
        )


def test_worker_cannot_rewrite_its_own_question_on_a_read_only_board(signup_hub, owners):
    """Asking on a readable board is allowed, but the question card does not become the
    worker's to edit: only cards assigned to it skip the write-set check."""
    hub, owner = signup_hub, owners["A"]
    machine = _enroll(hub, owner, wings=WINGS)  # writes platform only; reads notes
    worker = machine["worker_token"]
    held = card(hub, owner, board="notes")  # also creates the notes board
    _ok(
        _tool(
            hub,
            worker,
            "ask",
            board="notes",
            question="Which synthetic option should this example card use next?",
            options=["First option", "Second option"],
            context="Synthetic context for a regression test.",
        )
    )
    asked = held["number"] + 1
    actor = hub.db.authenticate_connection(
        _machine_row(hub, owner, machine)["worker_connection_id"]
    ).actor
    before = _task_row(hub, owner, asked)
    assert before["status"] == "review" and before["return_to"] == actor, before
    _refused(_tool(hub, worker, "task_update", task=asked, title="Worker rewrote its question"))
    assert _task_row(hub, owner, asked)["title"] == before["title"]
    # Linking through the tool is refused by the same check, before any link is written.
    _refused(_tool(hub, worker, "task_update", task=asked, link="drawer_synthetic"))
    # The memory-link path has the same own-card bypass; the worker has no link tool, so
    # check the store with its principal directly.
    from commonplace.boards.store import BoardStore
    from commonplace.extensions import ToolError

    principal = hub.db.authenticate_connection(
        _machine_row(hub, owner, machine)["worker_connection_id"]
    )
    store = BoardStore(hub.gateway.workspace_dir(owner["workspace"]))
    with pytest.raises(ToolError, match="cannot change this board"):
        store.link_drawer(asked, "drawer_synthetic", actor, principal=principal)
    # A card assigned to the worker stays editable outside the write set.

    with BoardStore(hub.gateway.workspace_dir(owner["workspace"])).transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='doing', assignee=? WHERE id=?", (actor, held["number"])
        )
    _ok(_tool(hub, worker, "task_update", task=held["number"], title="Held card edit"))
    assert _task_row(hub, owner, held["number"])["title"] == "Held card edit"


def _lesson_wing(hub, owner, worker, task):
    from commonplace.boards.store import BoardStore

    lesson = "Synthetic lesson for card " + str(task["number"])
    # Lessons are recorded when the card is finished.
    _ok(_tool(hub, worker, "task_update", task=task["number"], status="done", lesson=lesson))
    with BoardStore(hub.gateway.workspace_dir(owner["workspace"])).transaction() as conn:
        (drawer,) = conn.execute(
            "SELECT drawer_id FROM task_links WHERE task_id=?", (task["number"],)
        ).fetchone()
    status, body = http(hub.port, "GET", "/api/memory/" + drawer, cookie=owner["cookie"])
    assert status == 200 and body["text"] == lesson, (status, body)
    return body["theme"]


def test_lessons_stay_with_the_card_wing_inside_the_write_set(signup_hub, owners):
    """Runner workers cannot save lessons (no memory tools), so this uses an agent whose
    policy has the same shape: a first write wing plus a literal extra write wing."""
    hub, owner = signup_hub, owners["A"]
    _enroll(hub, owner, wings=WINGS)  # creates the three wings through public HTTP
    agent_id, agent = cli_token(
        owner,
        "lesson-agent",
        profile="standard",
        read_wings=",".join(WINGS),
        write_wing="platform",
        write_wings="compute",
    )
    actor = hub.db.authenticate_connection(agent_id).actor
    from commonplace.boards.store import BoardStore

    for board, expected in (("compute", "compute"), ("platform", "platform"), ("notes", None)):
        task = card(hub, owner, board=board)
        if expected is None:
            # A held card outside the write set (assigned by the owner) keeps the first wing.
            with BoardStore(hub.gateway.workspace_dir(owner["workspace"])).transaction() as conn:
                conn.execute(
                    "UPDATE tasks SET status='doing', assignee=? WHERE id=?",
                    (actor, task["number"]),
                )
            expected = "platform"
        else:
            _ok(_tool(hub, agent, "task_take_next", task=task["number"]))
        assert _lesson_wing(hub, owner, agent, task) == expected, board


def test_restricted_connections_keep_lessons_in_their_write_wing(signup_hub, owners):
    """A standard token whose write wing is the inbox keeps lessons there, even for a card
    in one of its extra write wings."""
    hub, owner = signup_hub, owners["A"]
    _enroll(hub, owner, wings=WINGS)
    agent_id, agent = cli_token(
        owner,
        "inbox-agent",
        profile="standard",
        trust="standard",
        read_wings=",".join([*WINGS, "inbox"]),
        write_wing="inbox",
        write_wings="compute",
    )
    actor = hub.db.authenticate_connection(agent_id).actor
    task = card(hub, owner, board="compute")
    from commonplace.boards.store import BoardStore

    with BoardStore(hub.gateway.workspace_dir(owner["workspace"])).transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='doing', assignee=? WHERE id=?", (actor, task["number"])
        )
    assert _lesson_wing(hub, owner, agent, task) == "inbox"


def test_untrusted_connections_keep_lessons_in_their_write_wing(signup_hub, owners):
    """The untrusted half of the restricted rule: a lesson on a card in an extra write wing
    still goes to the connection's own write wing."""
    hub, owner = signup_hub, owners["A"]
    _enroll(hub, owner, wings=WINGS)
    agent_id, agent = cli_token(
        owner,
        "untrusted-agent",
        profile="standard",
        trust="untrusted",
        read_wings=",".join(WINGS),
        write_wing="compute",
        write_wings="notes",
    )
    actor = hub.db.authenticate_connection(agent_id).actor
    task = card(hub, owner, board="notes")
    from commonplace.boards.store import BoardStore

    with BoardStore(hub.gateway.workspace_dir(owner["workspace"])).transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status='doing', assignee=? WHERE id=?", (actor, task["number"])
        )
    assert _lesson_wing(hub, owner, agent, task) == "compute"


def test_registry_permission_error_is_a_403_not_a_500(signup_hub, owners, monkeypatch):
    hub, owner = signup_hub, owners["A"]
    machine = _enroll(hub, owner)
    from commonplace.runner import registry

    def refuse(*_args, **_kwargs):
        raise PermissionError("a signed-in workspace owner is required")

    # The route guard runs first; this pins the response if the registry is the one refusing.
    monkeypatch.setattr(registry, "patch", refuse)
    status, body = _patch(hub, machine, {"max_in_flight": 2}, cookie=owner["cookie"])
    assert (status, body) == (403, {"error": "a signed-in workspace owner is required"})


def test_existing_grants_keep_the_first_wing_as_the_only_write_wing(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _enroll(hub, owner, wings=WINGS, max_in_flight=4)
    attest(hub, owner, machine)
    row = _machine_row(hub, owner, machine)
    assert row.get("write_wings") is None and row.get("write_set") == ["platform"], row
    policy = _policy(hub, owner, machine)
    assert (policy.write_wing, policy.write_wings) == ("platform", ())
    _accepted(_start(hub, machine, card(hub, owner, board="platform"), owner=owner))
    for board in ("compute", "notes"):
        code, body = _start(hub, machine, card(hub, owner, board=board), owner=owner)
        assert code == 403 and body["error"].endswith("outside_worker_wing"), (code, body)
    # An unrelated grant change leaves both the write set and the stored column alone.
    status, body = _patch(hub, machine, {"max_in_flight": 2}, cookie=owner["cookie"])
    assert status == 200 and body["write_wings"] is None, (status, body)
    assert body["write_set"] == ["platform"] and body["binding_state"] == "owner_attested"


def test_older_registry_tables_gain_a_nullable_write_wings_column(tmp_path):
    from commonplace.control_db import ControlDB
    from commonplace.runner import registry

    db = ControlDB(tmp_path / "control.sqlite3")
    with db._lock:
        db._conn.execute(
            "CREATE TABLE runner_machines (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, "
            "label TEXT NOT NULL, machine_connection_id TEXT NOT NULL UNIQUE, "
            "worker_connection_id TEXT NOT NULL UNIQUE, projects TEXT NOT NULL, "
            "topics TEXT NOT NULL, wings TEXT NOT NULL, revoked_at TEXT)"
        )
        db._conn.execute(
            "INSERT INTO runner_machines VALUES ('mach_0123456789abcdef','ws','old','m','w',"
            "'[]','[]',?,NULL)",
            (json.dumps(["platform", "compute"]),),
        )
    registry.ensure(db)
    registry.ensure(db)
    row = registry.get_machine(db, "ws", "mach_0123456789abcdef")
    assert "write_wings" in row.keys() and row["write_wings"] is None
    assert registry.machine_write_set(row) == ["platform"]
