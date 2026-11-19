"""Account export and deletion with a published preview and its events present.

The preview tables are workspace data: an export carries their rows (never the published
files), and deleting the only account of a workspace removes the preview rows and the files
with the workspace while the append-only event record stays, like the audit log.
"""

import sqlite3

import pytest

from commonplace import sessions
from commonplace.account import deletion
from commonplace.boards.service import BoardService
from commonplace.extensions import ToolContext
from commonplace.previews import service as previews
from tests._commonplace_account_helpers import site_fixture  # noqa: F401

DAY = 86400
PAGE = [
    {"path": "index.html", "text": "<h1>Preview words nobody else may receive</h1>"},
    {"path": "assets/app.css", "text": "body{color:#222}"},
]


@pytest.fixture
def published(site, monkeypatch):
    """A workspace only Alice belongs to holds a card with a ready and a stopped preview."""
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    site.studio = site.db.create_workspace("studio", site.alice)
    person = sessions.person_principal(site.db, site.alice, site.studio)
    board = BoardService(ToolContext(principal=person, gateway=site.gateway))
    board.board_add(slug="work", wing="shared")
    number = board.task_add(board="work", title="Build the page")["number"]
    ready, _ = previews.publish(site.gateway, person, number, "home", PAGE)
    other, _ = previews.publish(site.gateway, person, number, "docs", PAGE[:1])
    previews.stop(site.gateway, person, other["id"])
    site.preview_ids = (ready["id"], other["id"])
    return site


def test_export_carries_the_preview_records_but_not_the_published_files(published):
    site = published
    archive = site.export(site.alice, site.alice_home)
    prefix = f"workspaces/{site.studio}"
    for table, count in (("previews", 2), ("preview_revisions", 2), ("preview_events", 3)):
        assert len(archive.rows(f"{prefix}/control/{table}.ndjson")) >= count, table
    names = {r["name"] for r in archive.rows(f"{prefix}/control/previews.ndjson")}
    assert names == {"home", "docs"}
    assert not [n for n in archive.names if n.startswith(f"{prefix}/files/previews/")]
    assert "Preview words nobody else may receive" not in archive.text()
    assert archive.manifest["complete"] is True


def test_deleting_the_account_removes_the_preview_rows_and_files_with_the_workspace(published):
    site = published
    folder = site.data_dir / "workspaces" / site.studio / "previews"
    assert (folder / site.preview_ids[0] / "r1" / "index.html").is_file()
    status, _ = site.json(
        "POST",
        "/api/account/deletion",
        session=site.session(site.alice, site.alice_home),
        body={"confirm": "alice@example.test"},
    )
    assert status == 200
    site.advance(15 * DAY)
    (result,) = deletion.purge_due(site.gateway)
    assert result["state"] == "purged", result
    assert site.studio in result["deleted_workspaces"]
    assert not (site.data_dir / "workspaces" / site.studio).exists()
    for table in ("previews", "preview_revisions", "preview_rules"):
        assert site.rows(f"SELECT * FROM {table}") == [], table
    # The append-only record stays and holds ids and fixed reason codes, never the files.
    events = site.rows("SELECT * FROM preview_events")
    assert {e["kind"] for e in events} >= {"published", "stopped"}
    assert all(
        set(e) == {"id", "workspace_id", "preview_id", "card", "kind", "actor", "reason", "at"}
        for e in events
    )
    with site.db._lock:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            site.db._conn.execute("DELETE FROM preview_events")


def test_other_workspaces_previews_are_untouched_by_the_purge(published):
    site = published
    bob_studio = site.db.create_workspace("bob studio", site.bob)
    bob = sessions.person_principal(site.db, site.bob, bob_studio)
    board = BoardService(ToolContext(principal=bob, gateway=site.gateway))
    board.board_add(slug="team", wing="shared")
    number = board.task_add(board="team", title="Team page")["number"]
    kept, _ = previews.publish(site.gateway, bob, number, "team-home", PAGE)
    site.json(
        "POST",
        "/api/account/deletion",
        session=site.session(site.alice, site.alice_home),
        body={"confirm": "alice@example.test"},
    )
    site.advance(15 * DAY)
    deletion.purge_due(site.gateway)
    assert [r["id"] for r in site.rows("SELECT id FROM previews")] == [kept["id"]]
    assert (
        site.data_dir / "workspaces" / bob_studio / "previews" / kept["id"] / "r1" / "index.html"
    ).is_file()
