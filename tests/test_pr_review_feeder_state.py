"""Durable feeder publication and bounded actionable context, using real stored state."""

import errno
import json
import os
import secrets
import stat

import pytest
from test_pr_review import batch, card, comment, post, world as world

from commonplace.extensions import ToolContext
from commonplace.pr_review import context, feeder, status as review_status
from tests._console_helpers import console_hub_fixture as console_hub_fixture


def reader(path):
    return feeder.Feeder(
        "https://hub.example.invalid", secrets.token_hex(20), secrets.token_hex(20), path
    )


def test_failed_state_flush_preserves_the_previous_published_read(tmp_path, monkeypatch):
    path = tmp_path / "reader.json"
    original: dict[str, object] = {"prs": {"example/project#7": {"seen": {"inline": {"1": {}}}}}}
    path.write_text(json.dumps(original))
    old_bytes = path.read_bytes()
    f = reader(path)
    f.state = {"prs": {"example/project#8": {"seen": {"inline": {"2": {}}}}}}

    def refuse_flush(fd):
        assert stat.S_ISREG(os.fstat(fd).st_mode)
        raise OSError(errno.EIO, "synthetic storage flush failure")

    monkeypatch.setattr(feeder.os, "fsync", refuse_flush)
    with pytest.raises(OSError, match="synthetic storage flush failure"):
        f._save()
    assert path.read_bytes() == old_bytes
    assert set(tmp_path.iterdir()) == {path}


def test_successful_state_publication_flushes_real_private_file(tmp_path, monkeypatch):
    path = tmp_path / "reader.json"
    f = reader(path)
    f.state = {"prs": {"example/project#7": {"seen": {"inline": {"1": {}}}}}}
    actual_flush = os.fsync
    flushed = []

    def record_flush(fd):
        actual_flush(fd)
        flushed.append(True)

    monkeypatch.setattr(feeder.os, "fsync", record_flush)
    f._save()
    assert json.loads(path.read_text()) == f.state
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert flushed == [True]
    assert set(tmp_path.iterdir()) == {path}


def test_failed_atomic_publication_keeps_old_state_and_removes_only_its_staging_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "reader.json"
    original = b'{"prs": {}}\n'
    path.write_bytes(original)
    f = reader(path)
    f.state = {"prs": {"example/project#7": {"seen": {}}}}

    def refuse_replace(source, target):
        assert json.loads(source.read_text()) == f.state
        assert target == path
        raise OSError(errno.EIO, "synthetic publication failure")

    monkeypatch.setattr(feeder.os, "replace", refuse_replace)
    with pytest.raises(OSError, match="synthetic publication failure"):
        f._save()
    assert path.read_bytes() == original
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.parametrize("resolved", [0, 501])
def test_start_context_returns_waiting_comments_beyond_resolved_history(world, resolved):
    h = world
    number = card(h)
    total = resolved + 30
    for start in range(1, total + 1, 100):
        payload = batch(
            *[
                comment(i, f"Review item {i}.", thread=f"t{i:04d}")
                for i in range(start, min(total + 1, start + 100))
            ]
        )
        code, answer, _ = post(h, payload)
        assert code == 200, answer
    with h.store._tx() as connection:
        connection.execute(
            "UPDATE pr_review_comments SET status=? WHERE CAST(gh_id AS INTEGER)<=?",
            (review_status.ANSWERED, resolved),
        )
        connection.execute(
            "UPDATE pr_review_comments SET status=?, holder=? WHERE gh_id=?",
            (review_status.WORKING, "synthetic-worker", str(resolved + 2)),
        )
    ctx = ToolContext(principal=h.principal(h.t1), gateway=h.gateway)
    got = context.for_card(ctx, number)
    assert got is not None, "resolved history must not hide later actionable comments"
    assert [c["gh_id"] for c in got["comments"]] == [
        str(i) for i in range(resolved + 1, resolved + 26)
    ]
    assert got["comments"][1]["status"] == review_status.WORKING
    assert all(c["status"] in (review_status.NEW, review_status.WORKING) for c in got["comments"])
