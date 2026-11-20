"""Optional connection reminders must never replace a card-start account refusal."""

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from commonplace.orch import runner_bridge
from commonplace.projects import model_modes
from mempalace.sqlite_crypto import connect, row_factory
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_runner import BOARD, PLAIN, PROVIDER, World


@pytest.fixture(params=["configured", "manual"])
def start_case(hub, request):
    world = World(hub)
    configured = request.param == "configured"
    if configured:
        world.configure()
    board = BOARD if configured else PLAIN
    card = world.card(board=board)
    model_modes.put(world.db, world.owner, {"mode": "personal"})
    return world, board, card


def _decision(world, board):
    return model_modes.decide(
        world.db,
        world.ws,
        board,
        person=world.owner.user_id,
        provider=PROVIDER if board == BOARD else None,
    )


@contextmanager
def _needs_store(world, failure):
    if failure == "dropped":
        with world.db._lock, world.db._tx() as conn:
            conn.execute("DROP TABLE model_connection_needs")
        yield
    elif failure == "locked":
        # Only the optional table is locked; account and admission reads stay real.
        uri = Path(world.db.path).as_uri() + "?cache=shared"
        with (
            closing(
                connect(uri, uri=True, check_same_thread=False, isolation_level=None)
            ) as reader,
            closing(connect(uri, uri=True, isolation_level=None)) as writer,
        ):
            reader.row_factory = row_factory
            reader.execute("PRAGMA foreign_keys = ON")
            with world.db._lock:
                original, world.db._conn = world.db._conn, reader
            try:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE model_connection_needs SET provider=provider")
                yield
            finally:
                with world.db._lock:
                    writer.rollback()
                    world.db._conn = original
    else:
        yield


@pytest.mark.parametrize("failure", ["dropped", "locked"])
def test_optional_store_failure_preserves_the_exact_refusal_and_creates_no_job(start_case, failure):
    world, board, card = start_case
    expected = _decision(world, board).refusal
    assert expected.code == "connection_missing"
    with _needs_store(world, failure):
        # Prove the intended real SQLite failure rather than a setup error or mock.
        with pytest.raises(sqlite3.OperationalError) as storage:
            model_modes.note_missing(world.db, world.ws, world.owner.user_id, board, None)
        assert ("no such table" if failure == "dropped" else "locked") in str(storage.value)
        assert _decision(world, board).refusal == expected
        with pytest.raises(runner_bridge.StartRefused) as refusal:
            world.person_start(card)
        assert refusal.value.code == expected.code
        assert refusal.value.status == 409
        assert str(refusal.value) == f"no: {expected.message}"
        assert world.all_jobs() == []
        assert world.rows("SELECT * FROM run_ledger") == []
    if failure == "locked":
        assert (
            world.db._conn.execute("SELECT COUNT(*) FROM model_connection_needs").fetchone()[0] == 0
        )


def test_healthy_store_keeps_the_refusal_and_records_one_reminder(start_case):
    world, board, card = start_case
    expected = _decision(world, board).refusal
    for _ in range(2):
        with pytest.raises(runner_bridge.StartRefused) as refusal:
            world.person_start(card)
        assert refusal.value.code == "connection_missing"
        assert str(refusal.value) == f"no: {expected.message}"
    assert world.all_jobs() == []
    [row] = world.db._conn.execute("SELECT * FROM model_connection_needs").fetchall()
    assert (row["workspace_id"], row["user_id"], row["board"], row["provider"]) == (
        world.ws,
        world.owner.user_id,
        board,
        PROVIDER if board == BOARD else "",
    )


@pytest.mark.parametrize("failure", ["healthy", "dropped"])
def test_valid_personal_account_still_queues_its_original_account(start_case, failure):
    world, _board, card = start_case
    model_modes.add_connection(
        world.db,
        world.owner,
        {"provider": PROVIDER, "label": "Example subscription", "machine": world.machine_id},
    )
    with _needs_store(world, failure):
        status, _ = world.person_start(card)
    assert status == 201
    [job] = world.all_jobs()
    assert job["state"] == "queued"
    assert job["account_kind"] == "person"
    assert job["account_person"] == world.owner.user_id
    assert job["machine_id"] == world.machine_id


def test_unreadable_account_authority_still_fails_closed(start_case):
    world, _board, card = start_case
    with world.db._lock, world.db._tx() as conn:
        conn.execute("DROP TABLE model_mode_settings")
    with pytest.raises(runner_bridge.StartRefused) as refusal:
        world.person_start(card)
    assert refusal.value.code == "mode_unavailable"
    assert world.all_jobs() == []


def test_non_storage_bookkeeping_errors_are_not_swallowed(start_case, monkeypatch):
    world, _board, card = start_case

    def denied(*_args, **_kwargs):
        raise model_modes.ModeDenied("not found")

    monkeypatch.setattr(model_modes, "note_missing", denied)
    with pytest.raises(model_modes.ModeDenied):
        world.person_start(card)
    assert world.all_jobs() == []
