"""Workspace template import: a confirmed preview writes goals, routines and seats into a second
workspace; roles, skills and any orchestrator config land only as a proposal; nothing is overwritten."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace import cli, routines, workspace_template as wt, workspace_template_import as wti
from commonplace.boards import goals as board_goals
from commonplace.boards.store import BoardStore
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolError
from commonplace.orch import schema, store as orch_store
from commonplace.projects import seats
from commonplace.workspace_store import workspace_path
from tests.test_orch_store import MACHINE, Clock, accept_text, config_text

BOARD_A = "acme-shop"
BOARD_B = "beta-shop"
TOKEN = "cmmn_" + "B" * 43  # planted: never a real credential
ALL = ("roles", "seats", "skills", "routines", "goals", "orch_config")


def add_board(directory, slug):
    with BoardStore(directory).transaction() as conn:
        conn.execute(
            "INSERT INTO boards(slug,name,wing,stamp_user,stamp_trust,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (slug, slug, slug, "u", "trusted", "t"),
        )


def insert_routine(db, workspace, name, board, rid):
    with db._lock:
        db._conn.execute(
            "INSERT INTO routines (id, workspace_id, owner_user_id, name, template, schedule, "
            "timezone, state, cursor, anchor_at, created_by, created_at, updated_at) "
            "VALUES (?,?,'u1',?,?,?,'UTC','active','c','a','u1','t','t')",
            (
                rid,
                workspace,
                name,
                json.dumps(
                    {
                        "board": board,
                        "title": "Review {date}",
                        "body": "",
                        "labels": [],
                        "priority": "high",
                        "status": "todo",
                    }
                ),
                json.dumps({"every_minutes": 10080}),
            ),
        )
        db._conn.commit()


@pytest.fixture
def world(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = ControlDB(str(data / "control.sqlite3"))
    routines.ensure_schema(db)
    seats.ensure_schema(db)
    owner = db.create_user("owner@example.invalid", is_admin=True)
    a = db.create_workspace("personal", owner, personal=True)
    b = db.create_workspace("Second", owner)
    w = SimpleNamespace(data=data, db=db, owner=owner, a=a, b=b, dir_a=workspace_path(data, a))
    w.dir_b = workspace_path(data, b)
    add_board(w.dir_a, BOARD_A)
    add_board(w.dir_b, BOARD_B)
    accept_text(
        orch_store.Store(w.dir_a, now=Clock()), config_text(board=BOARD_A, planner=["planner-acme"])
    )
    with BoardStore(w.dir_a).transaction() as conn:
        top = board_goals.put(conn, "u1", "2026-11-26T10:00:00Z", title="Ship", detail="why")
        board_goals.put(conn, "u1", "2026-11-26T10:00:01Z", title="Beta", parent=top["id"])
    insert_routine(db, a, "Weekly review", BOARD_A, "r1")
    with db._lock:
        db._conn.execute(
            "INSERT INTO project_orchestrators VALUES (?, ?, 'main', 'orch-main', 'conn_secret', 't', 't')",
            (a, BOARD_A),
        )
        db._conn.commit()
    yield w
    db.close()


def export(w, sections=ALL):
    return wt.build_package(wt.read_source(w.db, w.a, w.dir_a, BOARD_A), sections)


def target(w, board=BOARD_B):
    return wt.read_source(w.db, w.b, w.dir_b, board)


def preview(w, package, **kw):
    return wt.preview(package, target(w), **kw)


def do_import(w, package, *, preview_id="current", actor=None, board=BOARD_B, **kw):
    if preview_id == "current":
        preview_id = preview(w, package, **kw)["preview_id"]
    return wti.apply(
        w.db,
        w.b,
        w.dir_b,
        package,
        board=board,
        preview_id=preview_id,
        actor=actor or w.owner,
        **kw,
    )


def snapshot(w):
    """Every row an import could touch in workspace B, as plain data."""
    src = target(w)
    with BoardStore(w.dir_b).connection() as conn:
        goal_rows = [tuple(r) for r in conn.execute("SELECT * FROM goals ORDER BY id")]
    with w.db._lock:
        routine_rows = [
            tuple(r)
            for r in w.db._conn.execute(
                "SELECT * FROM routines WHERE workspace_id=? ORDER BY id", (w.b,)
            )
        ]
        seat_rows = [
            tuple(r)
            for r in w.db._conn.execute(
                "SELECT * FROM project_orchestrators WHERE workspace_id=?", (w.b,)
            )
        ]
    runner = w.dir_b / "runner.sqlite3"
    versions = []
    if runner.exists():
        st = orch_store.Store.existing(w.dir_b)
        with st.connection() as conn:
            versions = [tuple(r) for r in conn.execute("SELECT * FROM orch_configs")]
    return goal_rows, routine_rows, seat_rows, versions, src.config_body


def all_bytes(directory):
    return b"".join(p.read_bytes() for p in sorted(Path(directory).rglob("*")) if p.is_file())


def proposed(w):
    st = orch_store.Store.existing(w.dir_b)
    with st.connection() as conn:
        return orch_store.accepted_in(conn, BOARD_B), st.versions(BOARD_B)


# ------------------------------------------------------------------ round trip


def test_export_from_a_import_into_a_fresh_second_workspace(world):
    w = world
    package = export(w)
    assert target(w).goals == [] and target(w).routines == [] and target(w).seats == []
    result = do_import(w, package)
    assert result["ok"] is True and result["applied"] == {
        "goals": 2,
        "routines": 1,
        "seats": 1,
        "roles": 3,
    }
    # same goals tree, same routine, same seat
    src_b = target(w)
    assert [(g["title"], g["detail"]) for g in src_b.goals] == [("Ship", "why"), ("Beta", "")]
    assert src_b.goals[1]["parent"] == src_b.goals[0]["id"]
    again = wt.build_package(src_b, ("goals", "routines"))
    original = wt.build_package(wt.read_source(w.db, w.a, w.dir_a, BOARD_A), ("goals", "routines"))
    again_routine = again["content"]["routines"][0]
    original_routine = original["content"]["routines"][0]
    assert again_routine["template"]["board"] == BOARD_B  # re-pointed at the target board
    assert {k: v for k, v in again_routine["template"].items() if k != "board"} == {
        k: v for k, v in original_routine["template"].items() if k != "board"
    }
    assert again_routine["schedule"] == original_routine["schedule"]
    assert again["content"]["goals"] == original["content"]["goals"]
    assert src_b.seats == [{"role": "main", "name": "orch-main"}]
    # roles and skills exist only in a proposed config: same roles, never live
    live, versions = proposed(w)
    assert live is None and len(versions) == 1 and versions[0].status == "proposed"
    config = schema.parse(versions[0].body)
    assert config.project == BOARD_B
    original_cfg = schema.parse(wt.read_source(w.db, w.a, w.dir_a, BOARD_A).config_body)
    assert set(config.roles) == set(original_cfg.roles)
    for name, role in config.roles.items():
        before = original_cfg.roles[name]
        assert (role.kind, role.parent, role.topic, role.workflow, role.prompt, role.skills) == (
            before.kind,
            before.parent,
            before.topic,
            before.workflow,
            before.prompt,
            before.skills,
        )
    assert dict(config.workflows) == dict(original_cfg.workflows)
    # no grants, bindings or secrets anywhere in B
    assert config.runner is None and config.triggers == () and not config.mcps
    assert all(config.roles[r].connections == () for r in config.roles)
    blob = all_bytes(w.dir_b)
    for forbidden in (MACHINE.encode(), b"planner-acme", b"conn_secret", TOKEN.encode()):
        assert forbidden not in blob
    with w.db._lock:
        grants = w.db._conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0]
    assert grants == 0
    assert result["proposal"]["status"] == "proposed" and result["proposal"]["board"] == BOARD_B


def test_a_second_import_of_the_same_package_creates_nothing(world):
    w = world
    package = export(w)
    do_import(w, package)
    before = snapshot(w)
    result = do_import(w, package)
    assert result["applied"] == {} and result["proposal"] is None
    assert snapshot(w) == before
    kinds = {(s["section"]) for s in result["skipped"]}
    assert {"goals", "routines", "seats"} <= kinds


def test_collisions_skip_and_children_attach_to_the_existing_goal(world):
    w = world
    with BoardStore(w.dir_b).transaction() as conn:
        mine = board_goals.put(conn, "u9", "t", title="ship", detail="mine")
    insert_routine(w.db, w.b, "WEEKLY review", BOARD_B, "rb")
    package = export(w, ("goals", "routines"))
    result = do_import(w, package)
    assert result["applied"] == {"goals": 1}
    goals = {g["title"]: g for g in target(w).goals}
    assert goals["ship"]["detail"] == "mine"  # never overwritten
    assert goals["Beta"]["parent"] == mine["id"]
    assert [r["name"] for r in target(w).routines] == ["WEEKLY review"]


def test_live_config_is_untouched_and_only_new_roles_are_proposed(world):
    w = world
    live_text = config_text(board=BOARD_B, runner=False)
    orch_store.Store(w.dir_b, now=Clock()).write(
        SimpleNamespace(
            actor="user:u1", policy=SimpleNamespace(trust="trusted"), auth="local", person=False
        ),
        BOARD_B,
        live_text,
        connections=(),
        sha256=schema.sha256_text(live_text),
    )
    package = export(w, ("roles", "skills"))
    result = do_import(w, package)
    assert result["applied"] == {"roles": 1}  # the planner is the only role B lacks
    live, versions = proposed(w)
    assert live.body == live_text
    by_version = sorted(versions, key=lambda v: v.version)
    assert [v.status for v in by_version] == ["accepted", "proposed"]
    merged = schema.parse(by_version[-1].body)
    assert set(merged.roles) == {"main", "builder", "planner"}
    assert merged.roles["builder"] == schema.parse(live_text).roles["builder"]


def test_orch_config_with_planted_grants_is_a_proposal_without_them(world):
    w = world
    package = export(w, ("orch_config",))
    cfg = package["content"]["orch_config"]["config"]
    cfg["runner"] = {"machine": MACHINE, "role": "builder"}
    cfg["triggers"] = [{"on": "card.todo"}]
    cfg["mcps"] = {"x": {"url": "https://example.invalid/mcp"}}
    package = wt.seal(package)
    first = preview(w, package)
    assert first["ok"]
    dropped = {d["path"]: d["reason"] for d in first["dropped"]}
    assert dropped["orch_config.config.runner"] == "needs_owner_acceptance"
    do_import(w, package)
    live, versions = proposed(w)
    config = schema.parse(versions[0].body)
    assert live is None and config.runner is None and config.triggers == () and not config.mcps
    assert MACHINE not in versions[0].body and "example.invalid" not in versions[0].body


def test_memory_is_not_imported_and_opt_in_has_no_writer(world):
    w = world
    package = wt.build_package(
        wt.read_source(w.db, w.a, w.dir_a, BOARD_A),
        ("goals", "memory"),
        include_memory=True,
        memory_reader=lambda: [{"wing": "w", "room": "r", "text": "verbatim words"}],
    )
    do_import(w, package)
    assert b"verbatim words" not in all_bytes(w.dir_b)
    with pytest.raises(ToolError, match="memory_unavailable"):
        do_import(w, package, include_memory=True)


# ------------------------------------------------------------------ the confirm step


def test_preview_id_names_the_exact_preview(world):
    w = world
    package = export(w)
    one = preview(w, package)
    assert one["ok"] and len(one["preview_id"]) == 64
    assert preview(w, package)["preview_id"] == one["preview_id"]
    assert preview(w, package, include_memory=True)["preview_id"] != one["preview_id"]
    refused = wt.preview({"content": {}}, target(w))
    assert refused["ok"] is False and not refused.get("preview_id")


@pytest.mark.parametrize("bad", [None, "", "abc", "0" * 64, 7])
def test_missing_or_wrong_preview_id_is_refused_and_writes_nothing(world, bad):
    w = world
    package = export(w)
    before = snapshot(w)
    with pytest.raises(ToolError, match="preview_stale"):
        do_import(w, package, preview_id=bad)
    assert snapshot(w) == before


def test_a_changed_package_cannot_be_applied_with_the_old_preview(world):
    w = world
    package = export(w)
    confirm = preview(w, package)["preview_id"]
    changed = json.loads(json.dumps(package))
    changed["content"]["goals"][0]["title"] = "Something else"
    changed = wt.seal(changed)
    before = snapshot(w)
    with pytest.raises(ToolError, match="preview_stale"):
        wti.apply(w.db, w.b, w.dir_b, changed, board=BOARD_B, preview_id=confirm, actor=w.owner)
    assert snapshot(w) == before


def test_a_changed_target_makes_the_preview_stale(world):
    w = world
    package = export(w)
    confirm = preview(w, package)["preview_id"]
    with BoardStore(w.dir_b).transaction() as conn:
        board_goals.put(conn, "u9", "t", title="Ship")
    before = snapshot(w)
    with pytest.raises(ToolError, match="preview_stale"):
        wti.apply(w.db, w.b, w.dir_b, package, board=BOARD_B, preview_id=confirm, actor=w.owner)
    assert snapshot(w) == before


def test_a_refused_package_cannot_be_applied(world):
    w = world
    package = export(w)
    package["content"]["goals"][0]["title"] = "tampered"  # digest no longer matches
    with pytest.raises(ToolError, match="preview_refused"):
        wti.apply(w.db, w.b, w.dir_b, package, board=BOARD_B, preview_id="x" * 64, actor=w.owner)


# ------------------------------------------------------------------ authority


def test_a_non_owner_of_the_target_is_refused(world):
    w = world
    member = w.db.create_user("member@example.invalid")
    w.db.add_member(w.b, member, "member", actor=w.owner)
    stranger = w.db.create_user("stranger@example.invalid")
    package = export(w)
    confirm = preview(w, package)["preview_id"]
    before = snapshot(w)
    for who in (member, stranger, "nobody"):
        with pytest.raises(ToolError, match="owner_only"):
            wti.apply(w.db, w.b, w.dir_b, package, board=BOARD_B, preview_id=confirm, actor=who)
    assert snapshot(w) == before


def test_an_owner_of_another_workspace_only_is_refused(world):
    w = world
    other = w.db.create_user("other@example.invalid")
    w.db.create_workspace("Theirs", other)
    package = export(w)
    confirm = preview(w, package)["preview_id"]
    with pytest.raises(ToolError, match="owner_only"):
        wti.apply(w.db, w.b, w.dir_b, package, board=BOARD_B, preview_id=confirm, actor=other)


# ------------------------------------------------------------------ secrets at write time


def test_a_planted_token_is_refused_without_echo_and_writes_nothing(world):
    w = world
    package = export(w, ("goals", "routines"))
    package["content"]["goals"][1]["detail"] = f"use {TOKEN}"
    package = wt.seal(package)
    before = snapshot(w)
    with pytest.raises(ToolError, match="secret_found") as exc:
        wti.apply(w.db, w.b, w.dir_b, package, board=BOARD_B, preview_id="x" * 64, actor=w.owner)
    assert TOKEN not in str(exc.value)
    assert snapshot(w) == before and TOKEN.encode() not in all_bytes(w.dir_b)


def test_write_time_scan_holds_even_if_the_preview_step_were_bypassed(world, monkeypatch):
    w = world
    package = export(w, ("goals",))
    package["content"]["goals"][0]["title"] = f"key {TOKEN}"
    package = wt.seal(package)
    ok = {"ok": True, "preview_id": "p" * 64, "refused": [], "collisions": [], "would_create": {}}
    monkeypatch.setattr(wt, "preview", lambda *a, **k: ok)
    before = snapshot(w)
    with pytest.raises(ToolError, match="secret_found"):
        wti.apply(w.db, w.b, w.dir_b, package, board=BOARD_B, preview_id="p" * 64, actor=w.owner)
    assert snapshot(w) == before and TOKEN.encode() not in all_bytes(w.dir_b)


# ------------------------------------------------------------------ incremental, all or nothing


def test_failure_filing_the_proposal_leaves_the_target_unchanged(world, monkeypatch):
    w = world
    package = export(w)
    before = snapshot(w)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(orch_store.Store, "propose", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        do_import(w, package)
    assert snapshot(w) == before


def test_failure_creating_routines_rolls_back_the_goals(world, monkeypatch):
    w = world
    package = export(w)
    before = snapshot(w)
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("routine store down")

    monkeypatch.setattr(routines, "insert_row", boom)
    with pytest.raises(RuntimeError, match="routine store down"):
        do_import(w, package)
    assert calls and snapshot(w) == before


def test_failure_after_routines_removes_only_what_the_import_created(world, monkeypatch):
    w = world
    with BoardStore(w.dir_b).transaction() as conn:
        board_goals.put(conn, "u9", "t", title="Mine")
    insert_routine(w.db, w.b, "My own", BOARD_B, "mine")
    package = export(w)
    before = snapshot(w)
    monkeypatch.setattr(
        orch_store.Store, "propose", lambda *a, **k: (_ for _ in ()).throw(OSError("io"))
    )
    with pytest.raises(OSError):
        do_import(w, package)
    assert snapshot(w) == before


def test_import_never_deletes_or_rewrites_existing_rows(world):
    w = world
    with BoardStore(w.dir_b).transaction() as conn:
        board_goals.put(conn, "u9", "t", title="Mine", detail="keep")
    insert_routine(w.db, w.b, "My own", BOARD_B, "mine")
    existing = snapshot(w)
    do_import(w, export(w))
    goals, routine_rows, seat_rows, _, _ = snapshot(w)
    assert set(existing[0]) <= set(goals) and set(existing[1]) <= set(routine_rows)


# ------------------------------------------------------------------ routine boards


def test_routines_need_a_board_in_the_target(world):
    w = world
    package = export(w, ("routines",))
    result = wt.preview(package, target(w, None))
    assert "routine_board_missing" in {r["code"] for r in result["refused"]}
    result = wt.preview(package, target(w, "no-such-board"))
    assert "routine_board_missing" in {r["code"] for r in result["refused"]}


def test_a_routine_keeps_its_board_when_the_target_has_it(world):
    w = world
    add_board(w.dir_b, BOARD_A)
    do_import(w, export(w, ("routines",)))
    assert target(w).routines[0]["template"]["board"] == BOARD_A


# ------------------------------------------------------------------ TOML round trip


def test_the_toml_writer_round_trips_a_full_config():
    text = config_text(board=BOARD_A, planner=["planner-acme"])
    config = schema.parse(text)
    again = schema.parse(wt.to_toml(config.to_dict()))
    assert again.to_dict() == config.to_dict()


def test_the_toml_writer_escapes_awkward_strings():
    prompt = 'quote " back\\slash\nnewline\ttab é \U0001f600 \x7f end'
    data = {
        "schema": schema.SCHEMA_ID,
        "project": "p",
        "roles": {"main": {"kind": "orchestrator", "prompt": prompt}},
    }
    assert schema.parse(wt.to_toml(data)).roles["main"].prompt == prompt


# ------------------------------------------------------------------ CLI


def test_cli_export_preview_and_confirmed_import(world, capsys):
    w = world
    out = w.data / "template.json"
    base = ["--data", str(w.data), "template"]
    capsys.readouterr()
    assert (
        cli.main([*base, "export", "--workspace", "personal", "--board", BOARD_A, "-o", str(out)])
        == 0
    )
    capsys.readouterr()
    assert cli.main([*base, "preview", str(out), "--workspace", "Second", "--board", BOARD_B]) == 0
    confirm = json.loads(capsys.readouterr().out)["preview_id"]
    wrong = ["import", str(out), "--workspace", "Second", "--board", BOARD_B, "--preview-id"]
    assert cli.main([*base, *wrong, "0" * 64]) != 0
    assert "preview_stale" in capsys.readouterr().err
    assert target(w).goals == []
    assert cli.main([*base, *wrong, confirm]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["applied"]["goals"] == 2
    assert [g["title"] for g in target(w).goals] == ["Ship", "Beta"]
    assert cli.main([*base, *wrong, confirm]) != 0  # the target moved on: stale
    capsys.readouterr()
    assert TOKEN not in out.read_text()


def test_cli_import_refuses_a_non_owner(world, capsys):
    w = world
    member = w.db.create_user("member@example.invalid")
    w.db.add_member(w.b, member, "member", actor=w.owner)
    out = w.data / "template.json"
    base = ["--data", str(w.data), "template"]
    cli.main([*base, "export", "--workspace", "personal", "--sections", "goals", "-o", str(out)])
    capsys.readouterr()
    code = cli.main(
        [*base, "import", str(out), "--workspace", "Second", "--by", member, "--preview-id", "x"]
    )
    assert code != 0 and "owner" in capsys.readouterr().err
    assert target(w).goals == []
