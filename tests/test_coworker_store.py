"""Storage canaries: real SQLite persistence, ACLs and transactional job fences."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from commonplace.chat.coworkers.store import MAX_PENDING, Store, StoreError


@pytest.fixture
def palace(tmp_path):
    path = tmp_path / "coworkers.sqlite3"
    store = Store(path)
    bot = store.create_bot("alice", "Ada", "Keep my exact words.")
    dm = store.create_conversation("alice", "dm", bot_ids=[bot["id"]])
    return path, store, bot, dm["id"]


def intake(store, conversation, request="r1", **kwargs):
    return store.post_message("alice", conversation, "  exact ☃\ntext\x00 ", request, **kwargs)


def denied(call, code="not_found", status=404):
    with pytest.raises(StoreError) as caught:
        call()
    assert (caught.value.code, caught.value.status) == (code, status)


def test_reopen_keeps_persona_membership_verbatim_messages_and_memory(palace):
    path, store, bot, conversation = palace
    posted = intake(store, conversation)
    memory = store.add_memory("alice", conversation, "  remember\n☃\x00  ")
    reopened = Store(path)
    assert reopened.list_bots() == [bot]
    assert reopened.get_conversation("alice", conversation)["member_ids"] == ["alice"]
    assert reopened.list_messages("alice", conversation) == [posted["message"]]
    assert reopened.list_memory("alice", conversation) == [memory]
    assert reopened.list_jobs("alice", conversation) == [posted["job"]]
    assert reopened.post_message("alice", conversation, posted["message"]["text"], "r1") == posted


def test_two_dm_and_group_canaries_are_conversation_scoped(palace):
    _, store, bot, alice_dm = palace
    bob_dm = store.create_conversation("bob", "dm", bot_ids=[bot["id"]])["id"]
    group = store.create_conversation("alice", "group", member_ids=["bob"], bot_ids=[bot["id"]])[
        "id"
    ]
    store.add_memory("alice", alice_dm, "ALICE_DM_CANARY")
    store.add_memory("bob", bob_dm, "BOB_DM_CANARY")
    store.add_memory("alice", group, "SHARED_GROUP_CANARY")
    store.post_message("bob", bob_dm, "BOB_MESSAGE_CANARY", "bob-1")
    assert [m["text"] for m in store.list_memory("alice", alice_dm)] == ["ALICE_DM_CANARY"]
    assert [m["text"] for m in store.list_memory("bob", group)] == ["SHARED_GROUP_CANARY"]
    assert store.list_messages("alice", group) == []
    assert {c["id"] for c in store.list_conversations("alice")} == {alice_dm, group}
    denied(lambda: store.get_conversation("alice", bob_dm))
    denied(lambda: store.list_memory("alice", bob_dm))
    denied(lambda: store.list_messages("alice", bob_dm))
    denied(lambda: store.add_memory("alice", bob_dm, "injection"))
    denied(lambda: store.post_message("alice", bob_dm, "injection", "x"))


def test_current_membership_is_rechecked_for_every_object_and_late_reply(palace):
    path, store, _, conversation = palace
    posted = intake(store, conversation)
    memory = store.add_memory("alice", conversation, "private")
    store.claim_job()
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM members WHERE user_id='alice'")
    for call in (
        lambda: store.get_conversation("alice", conversation),
        lambda: store.list_messages("alice", conversation),
        lambda: store.list_jobs("alice", conversation),
        lambda: store.list_memory("alice", conversation),
        lambda: store.add_memory("alice", conversation, "x"),
        lambda: store.remove_memory("alice", conversation, memory["id"]),
        lambda: store.cancel_job("alice", posted["job"]["id"]),
        lambda: store.add_bot_reply(posted["job"]["id"], "late"),
        lambda: store.finish_job(posted["job"]["id"], "done"),
    ):
        denied(call)
    assert store.list_conversations("alice") == []


def test_memory_tombstones_preserve_words_and_cannot_cross_conversations(palace):
    path, store, bot, conversation = palace
    other = store.create_conversation("alice", "dm", bot_ids=[bot["id"]])["id"]
    memory = store.add_memory("alice", conversation, "exact ORIGINAL\n  ")
    denied(lambda: store.remove_memory("alice", other, memory["id"]))
    denied(lambda: store.remove_memory("bob", conversation, memory["id"]))
    assert store.remove_memory("alice", conversation, memory["id"]) == {"ok": True}
    assert store.remove_memory("alice", conversation, memory["id"]) == {"ok": True}
    assert Store(path).list_memory("alice", conversation) == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT text FROM memories").fetchone()[0] == memory["text"]
        assert conn.execute("SELECT count(*) FROM memory_tombstones").fetchone()[0] == 1
        for table in ("memories", "memory_tombstones"):
            for sql in (f"UPDATE {table} SET actor='changed'", f"DELETE FROM {table}"):
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    conn.execute(sql)


def test_forget_fences_an_answer_using_a_now_hidden_memory(palace):
    _, store, _, conversation = palace
    memory = store.add_memory("alice", conversation, "forgotten canary")
    posted = intake(store, conversation)
    store.claim_job()
    store.remove_memory("alice", conversation, memory["id"])
    denied(
        lambda: store.add_bot_reply(posted["job"]["id"], "forgotten canary"), "context_changed", 409
    )
    assert len(store.list_messages("alice", conversation)) == 1


def test_idempotency_conflicts_and_actor_scoped_requests(palace):
    _, store, bot, conversation = palace
    original = intake(store, conversation)
    assert intake(store, conversation) == original
    denied(
        lambda: store.post_message("alice", conversation, "changed", "r1"), "request_conflict", 409
    )
    # Explicit selection and automatic selection are distinct requests even if
    # they currently resolve to the same bot.
    denied(lambda: intake(store, conversation, bot_id=bot["id"]), "request_conflict", 409)
    group = store.create_conversation("alice", "group", member_ids=["bob"], bot_ids=[bot["id"]])[
        "id"
    ]
    a = store.post_message("alice", group, "a", "same")
    b = store.post_message("bob", group, "b", "same")
    assert a["message"]["id"] != b["message"]["id"]
    assert len(store.list_messages("alice", conversation)) == 1


def test_group_routes_exactly_one_bot_and_unaddressed_posts_create_no_job(palace):
    _, store, ada, _ = palace
    bob = store.create_bot("alice", "Bobby")
    group = store.create_conversation("alice", "group", bot_ids=[ada["id"], bob["id"]])["id"]
    ordinary = store.post_message("alice", group, "hello everyone", "one")
    assert ordinary["job"] is None
    mentioned = store.post_message("alice", group, "@Ada hello", "two")
    assert mentioned["job"]["bot_id"] == ada["id"]
    selected = store.post_message("alice", group, "question", "three", bot_id=bob["id"])
    assert selected["job"]["bot_id"] == bob["id"]
    denied(lambda: store.post_message("alice", group, "@Ada @Bobby", "four"), "ambiguous_bot", 400)
    denied(lambda: store.post_message("alice", group, "hi", "five", bot_id="foreign"))
    assert len(store.list_messages("alice", group)) == 3
    assert len(store.list_jobs("alice", group)) == 2


def test_duplicate_display_names_do_not_confer_routing_authority(palace):
    _, store, ada, _ = palace
    twin = store.create_bot("alice", "Ada")
    group = store.create_conversation("alice", "group", bot_ids=[ada["id"], twin["id"]])["id"]
    denied(lambda: store.post_message("alice", group, "@Ada hi", "one"), "ambiguous_bot", 400)
    assert (
        store.post_message("alice", group, "hi", "two", bot_id=twin["id"])["job"]["bot_id"]
        == twin["id"]
    )


def test_failed_job_insert_rolls_back_message_and_request_key(palace):
    path, store, _, conversation = palace
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TRIGGER injected_job_failure BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT,'test'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        intake(store, conversation)
    assert store.list_messages("alice", conversation) == []
    assert store.list_jobs("alice", conversation) == []
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER injected_job_failure")
    assert intake(store, conversation)["job"]["state"] == "pending"


def test_queue_capacity_is_atomic_and_replay_still_succeeds(palace):
    _, store, _, conversation = palace
    first = intake(store, conversation)
    for n in range(1, MAX_PENDING):
        intake(store, conversation, f"key-{n}")
    denied(lambda: intake(store, conversation, "over-cap"), "at_capacity", 409)
    assert intake(store, conversation) == first
    assert len(store.list_messages("alice", conversation)) == MAX_PENDING


def test_parallel_replay_and_claim_are_atomic_across_store_instances(palace):
    path, store, _, conversation = palace
    # Initialize both connections before contention; actual SQL transactions
    # rather than a Python-only shared lock enforce cross-instance atomics.
    other = Store(path)
    other.list_bots()
    with ThreadPoolExecutor(max_workers=2) as pool:
        posted = list(pool.map(lambda s: intake(s, conversation), [store, other]))
        claims = list(pool.map(lambda s: s.claim_job(), [store, other]))
    assert posted[0] == posted[1]
    assert sum(c is not None for c in claims) == 1
    assert len(store.list_messages("alice", conversation)) == 1


def test_claim_serializes_each_conversation_bot_but_other_work_can_run(palace):
    _, store, bot, conversation = palace
    second = store.create_conversation("alice", "dm", bot_ids=[bot["id"]])["id"]
    first = intake(store, conversation)
    waiting = intake(store, conversation, "two")
    independent = intake(store, second, "three")
    assert store.claim_job()["id"] == first["job"]["id"]
    assert store.claim_job()["id"] == independent["job"]["id"]
    assert store.claim_job() is None
    reply = store.add_bot_reply(
        first["job"]["id"], "real bounded answer", model={"provider": "local", "model": "fast"}
    )
    assert reply["reply_to"] == first["message"]["id"]
    assert reply["actor"] == "bot:" + bot["id"]
    assert store.add_bot_reply(first["job"]["id"], "retry") == reply
    assert store.claim_job()["id"] == waiting["job"]["id"]
    assert len(store.list_jobs("alice", conversation)) == 2  # no bot-to-bot job


def test_cancelled_late_result_and_finish_cannot_change_terminal_job(palace):
    _, store, _, conversation = palace
    post = intake(store, conversation)
    job_id = post["job"]["id"]
    store.claim_job()
    cancelled = store.cancel_job("alice", job_id)
    assert cancelled["state"] == "cancelled"
    denied(lambda: store.add_bot_reply(job_id, "late"), "job_terminal", 409)
    denied(lambda: store.finish_job(job_id, "done", task=123), "job_terminal", 409)
    assert store.cancel_job("alice", job_id) == cancelled
    assert store.list_messages("alice", conversation) == [post["message"]]


def test_cancel_and_reply_race_has_one_atomic_outcome(palace):
    path, store, _, conversation = palace
    job_id = intake(store, conversation)["job"]["id"]
    store.claim_job()
    other = Store(path)
    other.list_bots()
    barrier = Barrier(2)

    def cancel():
        barrier.wait(timeout=3)
        return store.cancel_job("alice", job_id)

    def reply():
        barrier.wait(timeout=3)
        try:
            return other.add_bot_reply(job_id, "answer")
        except StoreError as exc:
            assert exc.code == "job_terminal"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelled, answered = pool.submit(cancel), pool.submit(reply)
        cancelled.result(timeout=5)
        answer = answered.result(timeout=5)
    terminal = store.list_jobs("alice", conversation)[0]
    assert terminal["state"] == ("done" if answer else "cancelled")
    assert len(store.list_messages("alice", conversation)) == (2 if answer else 1)


def test_reply_and_completion_rollback_together(palace):
    path, store, _, conversation = palace
    job_id = intake(store, conversation)["job"]["id"]
    store.claim_job()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TRIGGER injected_finish_failure BEFORE UPDATE OF state ON jobs WHEN NEW.state='done' BEGIN SELECT RAISE(ABORT,'test'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.add_bot_reply(job_id, "answer")
    assert len(store.list_messages("alice", conversation)) == 1
    assert store.list_jobs("alice", conversation)[0]["state"] == "running"


def test_restart_preserves_pending_and_retires_inflight_without_replay(palace):
    path, store, _, conversation = palace
    running = intake(store, conversation)
    pending = intake(store, conversation, "two")
    store.claim_job()
    reopened = Store(path)
    assert reopened.recover_jobs() == 1
    assert reopened.recover_jobs() == 0
    assert reopened.list_jobs("alice", conversation)[0]["state"] == "interrupted"
    denied(lambda: reopened.add_bot_reply(running["job"]["id"], "late"), "job_terminal", 409)
    assert reopened.claim_job()["id"] == pending["job"]["id"]
    reopened.add_bot_reply(pending["job"]["id"], "one answer")
    assert Store(path).recover_jobs() == 0
    assert len(reopened.list_messages("alice", conversation)) == 3


def test_messages_and_intake_keys_are_append_only(palace):
    path, store, _, conversation = palace
    intake(store, conversation)
    with sqlite3.connect(path) as conn:
        for statement in (
            "UPDATE messages SET text='rewrite'",
            "DELETE FROM messages",
            "DELETE FROM intake",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(statement)


@pytest.mark.parametrize("after,limit", [(True, 1), (-1, 1), (0, 101), (0, False), (2**63, 1)])
def test_pagination_bounds(palace, after, limit):
    _, store, _, conversation = palace
    denied(
        lambda: store.list_messages("alice", conversation, after=after, limit=limit),
        "invalid_request",
        400,
    )


def test_bounded_output_and_metadata_do_not_complete_job_on_invalid_result(palace):
    _, store, _, conversation = palace
    job_id = intake(store, conversation)["job"]["id"]
    store.claim_job()
    for kwargs in ({"text": "x" * 2001}, {"text": "ok", "model": {"key": "not allowed"}}):
        denied(lambda kwargs=kwargs: store.add_bot_reply(job_id, **kwargs), "invalid_request", 400)
    assert store.list_jobs("alice", conversation)[0]["state"] == "running"
    assert len(store.list_messages("alice", conversation)) == 1


def test_encrypted_workspace_uses_real_sqlcipher_and_reopens(tmp_path, monkeypatch):
    pytest.importorskip("sqlcipher3")
    from mempalace import sqlite_crypto

    directory = tmp_path / "workspaces" / "ws_synthetic"
    directory.mkdir(parents=True)
    (directory / "workspace.json").write_text(
        json.dumps(
            {
                "encryption": {
                    "version": 1,
                    "cipher": "sqlcipher",
                    "workspace_id": "ws_synthetic",
                    "key_id": "a" * 32,
                    "database_paths": [],
                }
            }
        )
    )
    # Synthetic key only: exercise the production SQLCipher connect machinery
    # without reading any operator key file or environment configuration.
    monkeypatch.setattr(sqlite_crypto, "workspace_key", lambda *_: b"k" * 32)
    path = directory / "coworkers.sqlite3"
    store = Store(path)
    bot = store.create_bot("alice", "Encryption canary")
    dm = store.create_conversation("alice", "dm", bot_ids=[bot["id"]])["id"]
    post = store.post_message("alice", dm, "VERBATIM_ENCRYPTED_CANARY", "one")
    assert Store(path).list_messages("alice", dm) == [post["message"]]
    assert b"VERBATIM_ENCRYPTED_CANARY" not in path.read_bytes()
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT * FROM messages").fetchall()


def test_different_bots_in_one_group_are_independently_claimable(palace):
    _, store, ada, _ = palace
    other = store.create_bot("alice", "Other")
    group = store.create_conversation("alice", "group", bot_ids=[ada["id"], other["id"]])["id"]
    first = store.post_message("alice", group, "for Ada", "one", bot_id=ada["id"])
    waiting = store.post_message("alice", group, "also Ada", "two", bot_id=ada["id"])
    independent = store.post_message("alice", group, "for Other", "three", bot_id=other["id"])
    assert store.claim_job()["id"] == first["job"]["id"]
    assert store.claim_job()["id"] == independent["job"]["id"]
    assert store.claim_job() is None
    store.finish_job(first["job"]["id"], "needs_configuration", "off")
    assert store.claim_job()["id"] == waiting["job"]["id"]


def test_runtime_finish_cannot_complete_unclaimed_work(palace):
    _, store, _, conversation = palace
    job_id = intake(store, conversation)["job"]["id"]
    denied(lambda: store.finish_job(job_id, "done"), "job_terminal", 409)
    denied(lambda: store.add_bot_reply(job_id, "not claimed"), "job_terminal", 409)
    assert store.list_jobs("alice", conversation)[0]["state"] == "pending"
