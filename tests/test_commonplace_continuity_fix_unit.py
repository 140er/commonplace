"""Store/control crash recovery and continued legacy import listing regressions."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from commonplace import capture, continuity as core, imports
from commonplace.import_grant import grant_id
from commonplace.import_parts import append_part
from commonplace.sessions import person_policy
from tests.test_commonplace_continuity_unit import (
    all_archives,
    descriptor,
    env as env,
    native,
    push,
    seed,
    snapshot,
    streams,
)


@pytest.mark.parametrize("kind", ["capture", "snapshot", "part"])
def test_fix5_retry_adopts_orphan_between_store_and_control_commits(env, monkeypatch, kind):
    e = env
    first, suffix = native(word="unique-first"), native(word="unique-suffix")
    core.backfill(e.db, e.store, e.ws, e.user)  # Startup migration already finished.
    sid = grant_id(e.user, "claude-code", "multipart")
    part = descriptor(first + suffix, first)

    def submit():
        if kind == "snapshot":
            return snapshot(e, first)
        if kind == "part":
            return push(e, first, stream=sid, part=part)
        return push(e, first)

    original = e.db._tx

    @contextmanager
    def crash_after_store_commit():
        with original() as conn:
            yield conn
            raise RuntimeError("injected failure before control commit")

    with monkeypatch.context() as patch:
        patch.setattr(e.db, "_tx", crash_after_store_commit)
        with pytest.raises(RuntimeError, match="before control commit"):
            submit()
    assert not streams(e)
    assert all_archives(e) == first
    stored = {
        thread.id: e.store.messages(thread.id)
        for prefix in ("capture:", "import-private:")
        for thread in e.store.list_source_range(e.ws, prefix, limit=10)
    }
    submit()  # Exact replay from offset zero after process restart.
    push(e, first + suffix, stream="capture:conn-second:claude-code:second")
    raw = all_archives(e)
    assert (raw.count(first), raw.count(suffix)) == (1, 1)
    assert len(streams(e)) == 2
    for thread_id, previous in stored.items():
        assert e.store.messages(thread_id)[: len(previous)] == previous


def test_fix6_legacy_multipart_listing_grows_by_own_submission_formula(env):
    e = env
    first, suffix, extra = native(), native(word="second"), native(word="extra")
    sid = grant_id(e.user, "claude-code", "multipart")
    result = append_part(
        e.store,
        e.ws,
        "claude-code",
        "multipart",
        "claude-code",
        first.decode(),
        descriptor(first + suffix, first),
        actor="synthetic",
        maximum=None,
        source_id=sid,
        wing=capture.PRIVATE_WING,
    )
    original = e.store.messages(result["stored_thread_id"])
    seed(e, first + suffix + extra, name="longer", capture_=True)
    core.backfill(e.db, e.store, e.ws, e.user)
    assert streams(e)[sid]["legacy"] == 1
    push(
        e,
        suffix,
        before=first,
        stream=sid,
        part=descriptor(first + suffix, suffix, offset=len(first), index=1),
    )
    routes = {}
    principal = SimpleNamespace(
        person=True, user_id=e.user, workspace_id=e.ws, policy=person_policy()
    )
    ctx = SimpleNamespace(
        control_db=e.db,
        store_for=lambda ws: e.store,
        gateway=SimpleNamespace(
            principal_for=lambda h: principal,
            add_route=lambda method, path, handler: routes.__setitem__((method, path), handler),
        ),
    )
    imports.register(ctx)
    replies = []
    handler = SimpleNamespace(path="/api/imports", _send_json=lambda *args: replies.append(args))
    routes["GET", "/api/imports"](handler)
    assert replies[0][0] == 200
    assert replies[0][1]["imports"][0]["stored_messages"] == 2 * 2 + 2 - 1
    assert e.store.messages(result["stored_thread_id"]) == original
