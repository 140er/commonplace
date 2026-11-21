"""Board helpers use explicit connections without opening their own store."""

import pytest

from commonplace.boards import service as board_service
from commonplace.boards.service import BoardService
from commonplace.boards.store import task_row
from commonplace.extensions import ToolContext
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import env as env


def refuse_open(*args, **kwargs):
    raise AssertionError("a no-store helper must not open BoardStore")


def test_no_store_helpers_keep_explicit_connections_and_write_authority(hub, env, monkeypatch):
    _token, principal, person, _cookie, owner = env
    added = owner.task_add(board="work", title="Synthetic helper control")
    monkeypatch.setattr(board_service, "open_store", refuse_open)

    for actor in (principal, person):
        helper = BoardService(ToolContext(principal=actor, gateway=hub.gateway), store=False)
        assert helper.store is None
        with owner.store.connection() as conn:
            board = helper._board(conn, "work")
            row = task_row(conn, added["number"])
            assert helper._claimable_origin(row)
            assert helper._write(board, row=row) is None
            report = helper.approvals_for_me(conn, limit=5, budget="session")
        assert report["items"] == []
        assert report["truncated"] is False and report["more"] is False
        assert helper.store is None


def test_no_store_approval_projection_preserves_the_parent_review(hub, fresh, monkeypatch):
    parent = fresh.connect("parent")
    child = fresh.connect("child", parent=parent.id, trust="untrusted", write_wing="inbox")
    proposed = child.work.task_add(board="work", title="Synthetic parent review")
    monkeypatch.setattr(board_service, "open_store", refuse_open)
    helper = BoardService(ToolContext(principal=parent.principal, gateway=hub.gateway), store=False)

    with parent.work.store.connection() as conn:
        expected = parent.work.approvals_for_me(conn)
        actual = helper.approvals_for_me(conn)
    assert [item["number"] for item in actual["items"]] == [proposed["number"]]
    assert actual["items"] == expected["items"]
    assert actual["truncated"] == expected["truncated"]
    assert actual["more"] == expected["more"]
    assert actual["role_checks"] == expected["role_checks"]
    assert helper.store is None


@pytest.mark.parametrize("operation", ["directory", "approvals", "transaction"])
def test_no_store_storage_operations_refuse_without_opening(hub, env, monkeypatch, operation):
    _token, _principal, person, _cookie, _owner = env
    monkeypatch.setattr(board_service, "open_store", refuse_open)
    helper = BoardService(ToolContext(principal=person, gateway=hub.gateway), store=False)

    with pytest.raises(RuntimeError, match="^board operation requires a store$"):
        if operation == "directory":
            helper.board_directory(names_only=True)
        elif operation == "approvals":
            helper.approvals_for_me()
        else:
            with helper.transaction():
                pytest.fail("a no-store service must not start a board transaction")
    assert helper.store is None


def test_storage_precondition_returns_the_existing_store(env):
    _token, _principal, _person, _cookie, owner = env
    assert owner._require_store() is owner.store
