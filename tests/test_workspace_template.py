"""Workspace template export and preview: no tokens, collisions listed, secrets refused, no writes."""

import hashlib
import json
from pathlib import Path

import pytest

from commonplace import routines, workspace_template as wt
from commonplace.boards import goals as board_goals
from commonplace.boards.store import BoardStore
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolError
from commonplace.orch.store import Store
from commonplace.projects import seats
from tests.test_orch_store import MACHINE, Clock, accept_text, config_text

BOARD = "acme-shop"
TOKEN = "cmmn_" + "A" * 43  # planted: never a real credential


def source(**over):
    value = {
        "board": BOARD,
        "config_body": config_text(board=BOARD, planner=["planner-acme"]),
        "seats": [{"role": "main", "name": "orch-main"}],
        "routines": [
            {
                "name": "Weekly review",
                "template": {"board": BOARD, "title": "Review {date}", "priority": "high"},
                "schedule": {"every_minutes": 10080},
                "timezone": "UTC",
                "state": "active",
            }
        ],
        "goals": [
            {"id": 7, "parent": None, "title": "Ship", "detail": "why", "archived": False},
            {"id": 9, "parent": 7, "title": "Beta", "detail": "", "archived": False},
        ],
    }
    value.update(over)
    return wt.Source(**value)


def dump(value):
    return json.dumps(value, sort_keys=True)


# ------------------------------------------------------------------ export


def test_export_is_versioned_and_strips_tokens_and_grants():
    pkg = wt.build_package(
        source(), ("roles", "skills", "routines", "goals", "seats", "orch_config")
    )
    assert pkg["format"] == wt.FORMAT and pkg["version"] == wt.VERSION == 1
    assert pkg["memory_included"] is False
    for forbidden in (MACHINE, "planner-acme", "builder-acme"):
        assert forbidden not in dump(pkg), forbidden
    for forbidden in ("runner", "triggers", "connections", "mcps", "secret_ref"):
        assert forbidden not in dump(pkg["content"]), forbidden
    assert set(pkg["content"]) == {"roles", "skills", "routines", "goals", "seats", "orch_config"}
    builder = next(r for r in pkg["content"]["roles"] if r["name"] == "builder")
    assert builder["parent"] == "main" and builder["workflow"] == "build-unit"
    assert "connections" in pkg["stripped"] and "runner" in pkg["stripped"]
    # goal ids are renumbered, parents follow
    assert [(g["ref"], g["parent"], g["title"]) for g in pkg["content"]["goals"]] == [
        (1, None, "Ship"),
        (2, 1, "Beta"),
    ]
    assert pkg["content"]["orch_config"]["proposal_only"] is True
    assert pkg["digest"] == wt.digest(pkg["content"])


def test_export_only_chosen_sections():
    pkg = wt.build_package(source(), ("goals",))
    assert pkg["sections"] == ["goals"] and set(pkg["content"]) == {"goals"}


def test_export_redacts_a_planted_token_and_reports_where():
    src = source(
        goals=[
            {"id": 1, "parent": None, "title": "Ship", "detail": f"key {TOKEN}", "archived": False}
        ]
    )
    pkg = wt.build_package(src, ("goals",))
    assert TOKEN not in dump(pkg)
    assert pkg["redactions"] and pkg["redactions"][0]["path"] == "goals[0].detail"
    assert wt.preview(pkg, source(goals=[], routines=[], seats=[]))["ok"] is True


def test_no_memory_unless_explicitly_requested():
    drawers = [{"wing": "w", "room": "r", "text": "verbatim words"}]
    default = wt.build_package(source(), wt.DEFAULT_SECTIONS, memory_reader=lambda: drawers)
    assert "memory" not in default["content"] and "verbatim words" not in dump(default)
    with pytest.raises(ToolError, match="memory_needs_opt_in"):
        wt.build_package(source(), ("memory",), memory_reader=lambda: drawers)
    with pytest.raises(ToolError, match="memory_needs_opt_in"):
        wt.build_package(source(), wt.DEFAULT_SECTIONS, include_memory=True)
    pkg = wt.build_package(
        source(), ("memory",), include_memory=True, memory_reader=lambda: drawers
    )
    assert pkg["memory_included"] is True and pkg["content"]["memory"] == drawers


def test_memory_with_a_secret_refuses_instead_of_rewriting():
    drawers = [{"wing": "w", "room": "r", "text": f"token {TOKEN}"}]
    with pytest.raises(ToolError, match="memory_contains_secret") as exc:
        wt.build_package(source(), ("memory",), include_memory=True, memory_reader=lambda: drawers)
    assert TOKEN not in str(exc.value)


def test_unknown_section_and_missing_reader_are_refused():
    with pytest.raises(ToolError, match="unknown_section"):
        wt.build_package(source(), ("nope",))
    with pytest.raises(ToolError, match="memory_unavailable"):
        wt.build_package(source(), ("memory",), include_memory=True)


# ------------------------------------------------------------------ preview


def test_preview_lists_collisions_and_would_create():
    pkg = wt.build_package(source(), ("roles", "routines", "goals", "seats", "orch_config"))
    target = source(
        config_body=config_text(board=BOARD),
        routines=[
            {
                "name": "weekly REVIEW",
                "template": {},
                "schedule": {},
                "timezone": "UTC",
                "state": "paused",
            }
        ],
        goals=[{"id": 1, "parent": None, "title": "ship", "detail": "", "archived": False}],
        seats=[{"role": "main", "name": "x"}],
    )
    result = wt.preview(pkg, target)
    assert result["ok"] and result["writes"] == 0
    keys = {(c["section"], c["key"]) for c in result["collisions"]}
    assert keys == {
        ("roles", "main"),
        ("roles", "builder"),
        ("routines", "weekly review"),
        ("goals", "Ship"),
        ("seats", "main"),
        ("orch_config", BOARD),
    }
    assert all(c["resolution"] == "skip" for c in result["collisions"])
    # Beta sits under the colliding Ship goal but is itself new
    assert result["would_create"]["goals"] == 1
    assert result["would_create"]["roles"] == 1  # the planner is the only role the target lacks
    assert result["proposals"][0]["section"] == "orch_config"
    assert result["proposals"][0]["status"] == "proposal_only"


def test_preview_lists_dropped_fields_and_never_imports_grants():
    pkg = wt.build_package(source(), ("roles", "orch_config"))
    pkg["content"]["roles"][0]["connections"] = ["x"]
    pkg["content"]["roles"][0]["owner_user_id"] = "u1"
    pkg["content"]["orch_config"]["config"]["runner"] = {"machine": MACHINE}
    pkg["content"]["extra"] = []
    pkg = wt.seal(pkg)
    result = wt.preview(pkg, source(config_body=None, seats=[], routines=[], goals=[]))
    dropped = {(d["path"], d["reason"]) for d in result["dropped"]}
    assert ("roles[0].connections", "not_importable") in dropped
    assert ("roles[0].owner_user_id", "not_importable") in dropped
    assert ("orch_config.config.runner", "needs_owner_acceptance") in dropped
    assert ("extra", "unknown_section") in dropped


def test_preview_refuses_a_planted_token_without_echoing_it():
    pkg = wt.build_package(source(), ("goals",))
    pkg["content"]["goals"][0]["detail"] = f"use {TOKEN}"
    result = wt.preview(wt.seal(pkg), source(goals=[]))
    assert result["ok"] is False
    assert {r["code"] for r in result["refused"]} == {"secret_found"}
    assert result["findings"][0]["path"] == "goals[0].detail"
    assert result["findings"][0]["kind"] == "cmmn_token"
    assert TOKEN not in dump(result)
    assert result["would_create"] == {}


def test_preview_refuses_secret_keys_and_long_text_secrets():
    pkg = wt.build_package(source(), ("goals",))
    pkg["content"]["goals"][0]["api_key"] = "hunter2hunter2"
    pkg["content"]["goals"][1]["detail"] = ("filler " * 5000) + TOKEN
    result = wt.preview(wt.seal(pkg), source(goals=[]))
    paths = {f["path"] for f in result["findings"]}
    assert result["ok"] is False and {"goals[0].api_key", "goals[1].detail"} <= paths


def test_preview_refuses_tampering_wrong_format_and_undeclared_memory():
    pkg = wt.build_package(source(), ("goals",))
    tampered = json.loads(dump(pkg))
    tampered["content"]["goals"][0]["title"] = "Other"
    assert "digest_mismatch" in {r["code"] for r in wt.preview(tampered, source())["refused"]}
    future = dict(pkg, version=2)
    assert "unsupported_version" in {r["code"] for r in wt.preview(future, source())["refused"]}
    other = dict(pkg, format="something-else")
    assert "unsupported_format" in {r["code"] for r in wt.preview(other, source())["refused"]}
    pkg["content"]["memory"] = [{"wing": "w", "room": "r", "text": "hi"}]
    sealed = wt.seal(pkg)
    assert "memory_not_declared" in {r["code"] for r in wt.preview(sealed, source())["refused"]}


def test_preview_memory_needs_opt_in_on_import_side_too():
    pkg = wt.build_package(
        source(),
        ("memory",),
        include_memory=True,
        memory_reader=lambda: [{"wing": "w", "room": "r", "text": "hi"}],
    )
    default = wt.preview(pkg, source())
    assert default["ok"] and default["would_create"] == {}
    assert ("memory", "memory_not_included_by_default") in {
        (d["path"], d["reason"]) for d in default["dropped"]
    }
    assert wt.preview(pkg, source(), include_memory=True)["would_create"] == {"memory": 1}


def test_preview_needs_a_target_board_for_board_sections():
    pkg = wt.build_package(source(), ("roles",))
    result = wt.preview(pkg, source(board=None))
    assert "target_board_required" in {r["code"] for r in result["refused"]}


def test_preview_rejects_non_object_and_oversized_packages():
    assert wt.preview([], source())["ok"] is False
    with pytest.raises(ToolError, match="package_too_large"):
        wt.load_package("x" * (wt.MAX_PACKAGE_BYTES + 1))
    with pytest.raises(ToolError, match="package_invalid"):
        wt.load_package("{not json")


# ------------------------------------------------------------------ real stores


def tree_hash(directory):
    out = {}
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file():
            out[str(path.relative_to(directory))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture
def workspace(tmp_path):
    directory = tmp_path / "ws"
    orch = Store(directory, now=Clock())
    accept_text(orch, config_text(board=BOARD, planner=["planner-acme"]))
    with BoardStore(directory).connection() as conn:
        top = board_goals.put(conn, "u1", "2026-11-26T10:00:00Z", title="Ship", detail="why")
        board_goals.put(conn, "u1", "2026-11-26T10:00:01Z", title="Beta", parent=top["id"])
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    routines.ensure_schema(db)
    seats.ensure_schema(db)
    with db._lock:
        db._conn.execute(
            "INSERT INTO routines (id, workspace_id, owner_user_id, name, template, schedule, timezone, "
            "state, cursor, anchor_at, created_by, created_at, updated_at) VALUES "
            "('r1','ws_a','u1','Weekly review',?,?,'UTC','active','c','a','u1','t','t')",
            (json.dumps({"board": BOARD, "title": "Review"}), json.dumps({"every_minutes": 10080})),
        )
        db._conn.execute(
            "INSERT INTO routines (id, workspace_id, owner_user_id, name, template, schedule, timezone, "
            "state, cursor, anchor_at, created_by, created_at, updated_at) VALUES "
            "('r2','ws_other','u1','Other workspace',?,?,'UTC','active','c','a','u1','t','t')",
            ("{}", "{}"),
        )
        db._conn.execute(
            "INSERT INTO project_orchestrators VALUES ('ws_a', ?, 'main', 'orch-main', 'conn_secret', 't', 't')",
            (BOARD,),
        )
        db._conn.commit()
    return db, directory


def test_read_source_export_and_read_only_preview_over_real_stores(workspace, tmp_path):
    db, directory = workspace
    src = wt.read_source(db, "ws_a", directory, BOARD)
    assert [r["name"] for r in src.routines] == ["Weekly review"]
    assert src.seats == [{"role": "main", "name": "orch-main"}]
    pkg = wt.build_package(src, ("roles", "routines", "goals", "seats", "skills"))
    assert "conn_secret" not in dump(pkg) and "Other workspace" not in dump(pkg)
    assert [g["title"] for g in pkg["content"]["goals"]] == ["Ship", "Beta"]

    before = tree_hash(directory)
    result = wt.preview(pkg, wt.read_source(db, "ws_a", directory, BOARD))
    assert result["ok"] and result["writes"] == 0 and len(result["collisions"]) >= 5
    assert tree_hash(directory) == before


def test_read_source_of_an_empty_workspace_is_empty(tmp_path):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    src = wt.read_source(db, "ws_none", tmp_path / "missing", BOARD)
    assert (src.config_body, src.goals, src.routines, src.seats) == (None, [], [], [])
    assert not (tmp_path / "missing").exists()


# ------------------------------------------------------------------ CLI


def test_cli_export_then_preview_round_trip_and_refusals(tmp_path, capsys):
    from commonplace import cli
    from commonplace.workspace_store import workspace_path

    data = tmp_path / "data"
    assert cli.main(["--data", str(data), "init", "--admin-email", "owner@example.invalid"]) == 0
    db = ControlDB(str(data / "control.sqlite3"))
    ws = db.workspace("personal")["id"]
    db.close()
    with BoardStore(workspace_path(data, ws)).connection() as conn:
        board_goals.put(conn, "u1", "2026-11-26T10:00:00Z", title="Ship", detail=f"key {TOKEN}")
    capsys.readouterr()

    out = tmp_path / "template.json"
    args = ["--data", str(data), "template"]
    assert (
        cli.main(
            [*args, "export", "--workspace", "personal", "--sections", "goals", "-o", str(out)]
        )
        == 0
    )
    written = json.loads(out.read_text())
    assert written["content"]["goals"][0]["title"] == "Ship" and TOKEN not in out.read_text()
    assert written["redactions"][0]["path"] == "goals[0].detail"
    # an existing file is never overwritten
    assert (
        cli.main(
            [*args, "export", "--workspace", "personal", "--sections", "goals", "-o", str(out)]
        )
        == 2
    )
    assert out.read_text()[0] == "{" and TOKEN not in out.read_text()
    capsys.readouterr()

    assert cli.main([*args, "preview", str(out), "--workspace", "personal"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and [c["section"] for c in result["collisions"]] == ["goals"]

    out.write_text(
        json.dumps(
            wt.seal(
                {
                    **written,
                    "content": {
                        "goals": [
                            {
                                "ref": 1,
                                "parent": None,
                                "title": "T",
                                "detail": TOKEN,
                                "archived": False,
                            }
                        ]
                    },
                }
            )
        )
    )
    assert cli.main([*args, "preview", str(out), "--workspace", "personal"]) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["refused"][0]["code"] == "secret_found" and TOKEN not in json.dumps(refusal)


def test_cli_memory_flag_is_refused_without_a_reader(tmp_path, capsys):
    from commonplace import cli

    data = tmp_path / "data"
    cli.main(["--data", str(data), "init", "--admin-email", "owner@example.invalid"])
    code = cli.main(
        [
            "--data",
            str(data),
            "template",
            "export",
            "--workspace",
            "personal",
            "--sections",
            "goals",
            "--include-memory",
        ]
    )
    assert code != 0 and "memory_unavailable" in capsys.readouterr().err
