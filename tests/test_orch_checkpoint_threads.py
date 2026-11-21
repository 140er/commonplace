"""Checkpoint visibility of builders started outside the runner (kind ``thread``).

A journaled thread start for a card that is in ``doing`` under a live claim shows up in
the checkpoint's ``jobs``; either fact alone does not. The checkpoint stays read-only.
"""

import hashlib
from dataclasses import replace

import pytest

from commonplace.boards.service import BoardService
from commonplace.boards.store import BoardStore
from commonplace.extensions import ToolError
from commonplace.orch import durable, store as orch_store, tools
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_durable import (
    BOARD,
    OTHER_ROLE,
    ROLE,
    Env,
    _orch_job,
    _start_step,
    at,
    config_text,
    lease,
)

OTHER_BOARD = "plain"
NOTE = "T3 thread thr_example started for the card"
LIVE_UNTIL = at(3600)
NOW = at(10)


@pytest.fixture
def env(hub):
    return Env(hub)


def claim(env, principal, card, *, until=LIVE_UNTIL, board=BOARD):
    """Put ``card`` in doing under ``principal`` with a claim that is live at ``T0``."""
    BoardService(env.ctx(principal)).task_take_next(board=board, task=card)
    with env.boards_db() as conn:
        conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (until, card))


def start_thread(env, epoch, token, key, card, *, board=BOARD, role=ROLE, state="started"):
    ctx = env.ctx(env.a)
    durable.journal_intent(
        ctx, board, role, epoch, token, key, "thread", card=card, detail="builder", now=at(1)
    )
    if state is None:
        return
    durable.journal_record(
        ctx,
        board,
        role,
        epoch,
        token,
        key,
        state,
        evidence={"type": "holder_attested", "note": NOTE},
        now=at(2),
    )


def thread_jobs(env, board=BOARD, role=ROLE, now=NOW):
    jobs = durable.checkpoint(env.ctx(env.a), board, role, now=now)["jobs"]
    return [job for job in jobs if job["source"] == "journal"]


def test_journaled_thread_start_with_live_doing_assignee_is_a_checkpoint_job(env):
    epoch, token = lease(env)
    card = env.card("Build it")
    claim(env, env.b, card)
    start_thread(env, epoch, token, "t3-1", card)

    assert thread_jobs(env) == [
        {
            "id": None,
            "task_number": card,
            "state": "started",
            "outcome_code": None,
            "thread_id": None,
            "started_at": at(2),
            "updated_at": at(2),
            "step_key": "t3-1",
            "source": "journal",
            "assignee": env.b.actor,
            "claim_until": at(3600),
            "detail": "builder",
            "evidence": {
                "type": "holder_attested",
                "note": NOTE,
                "by": durable._actor_ref(env.a),
            },
        }
    ]


def test_thread_job_needs_both_the_recorded_start_and_a_live_doing_assignee(env):
    epoch, token = lease(env)
    intent_only, todo, expired, finished, voided, done_card, no_card = (
        env.card(title) for title in ("i", "t", "e", "f", "v", "d", "n")
    )
    for card in (intent_only, expired, finished, voided, done_card, no_card):
        claim(env, env.b, card)
    start_thread(env, epoch, token, "intent-only", intent_only, state=None)
    start_thread(env, epoch, token, "todo", todo)
    start_thread(env, epoch, token, "expired", expired)
    start_thread(env, epoch, token, "finished", finished)
    start_thread(env, epoch, token, "voided", voided)
    start_thread(env, epoch, token, "done-card", done_card)
    # A doing card with a live assignee but no journaled start for it.
    durable.journal_intent(
        env.ctx(env.a), BOARD, ROLE, epoch, token, "no-card", "thread", now=at(1)
    )
    durable.journal_record(
        env.ctx(env.a), BOARD, ROLE, epoch, token, "no-card", "started", now=at(2)
    )
    with env.boards_db() as conn:
        conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (at(5), expired))
    durable.journal_record(
        env.ctx(env.a), BOARD, ROLE, epoch, token, "finished", "failed", now=at(3)
    )
    durable.void_step(env.ctx(env.owner), BOARD, ROLE, "voided", now=at(3))
    BoardService(env.ctx(env.b)).task_update(done_card, status="done")

    assert thread_jobs(env) == []
    # The same expired card was listed while its claim was live.
    assert [job["step_key"] for job in thread_jobs(env, now=at(4))] == ["expired"]


def test_thread_job_is_scoped_to_its_board_and_role(env):
    text = config_text(board=OTHER_BOARD)
    orch_store.open_store(env.hub.gateway, env.ws).write(
        env.owner,
        OTHER_BOARD,
        text,
        connections=[],
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    other_card = env.owner_board.task_add("Elsewhere", board=OTHER_BOARD)["number"]
    claim(env, env.b, other_card, board=OTHER_BOARD)
    claim_other = durable.resume(env.ctx(env.a), OTHER_BOARD, ROLE, now=at(0))
    start_thread(
        env, claim_other["epoch"], claim_other["token"], "t3-1", other_card, board=OTHER_BOARD
    )
    role_card = env.card("Other role")
    claim(env, env.b, role_card)
    role_epoch, role_token = lease(env, role=OTHER_ROLE)
    start_thread(env, role_epoch, role_token, "t3-2", role_card, role=OTHER_ROLE)

    assert thread_jobs(env) == []
    assert [job["task_number"] for job in thread_jobs(env, board=OTHER_BOARD)] == [other_card]
    assert [job["task_number"] for job in thread_jobs(env, role=OTHER_ROLE)] == [role_card]


def test_thread_job_stays_listed_when_a_new_epoch_marks_the_step_in_doubt(env):
    epoch, token = lease(env)
    started, never_started = env.card("Started"), env.card("Never started")
    claim(env, env.b, started, until=at(9000))
    claim(env, env.b, never_started, until=at(9000))
    start_thread(env, epoch, token, "t3-1", started)
    start_thread(env, epoch, token, "t3-2", never_started, state=None)
    later = at(durable.TTL_SECONDS + 60)
    env.resume(env.a, now=later)

    jobs = thread_jobs(env, now=later)
    assert [(job["step_key"], job["state"], job["started_at"]) for job in jobs] == [
        ("t3-1", "in_doubt", at(2))
    ]


def test_checkpoint_with_thread_jobs_writes_nothing(env):
    epoch, token = lease(env)
    card = env.card()
    claim(env, env.b, card)
    start_thread(env, epoch, token, "t3-1", card)

    def snapshot():
        with env.boards_db() as conn:
            tasks = [dict(row) for row in conn.execute("SELECT * FROM tasks ORDER BY id")]
        conn = env.runner_db()
        try:
            return tasks, {
                table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
                for table in ("orch_steps", "orch_journal", "orch_leases")
            }
        finally:
            conn.close()

    before = snapshot()
    assert len(thread_jobs(env)) == 1
    assert snapshot() == before


def test_stale_open_thread_steps_do_not_crowd_out_a_live_builder(env, monkeypatch):
    monkeypatch.setattr(durable, "MAX_LIST", 2)
    epoch, token = lease(env)
    live = env.card("Live")
    claim(env, env.b, live)
    start_thread(env, epoch, token, "live", live)
    for index in range(3):
        start_thread(env, epoch, token, f"stale-{index}", env.card(f"Stale {index}"))

    assert [job["step_key"] for job in thread_jobs(env)] == ["live"]


def test_no_thread_step_means_no_extra_read():
    # Neither store is opened without a candidate step: a context or store of None
    # would raise on first use.
    steps = [{"kind": "merge", "card": 1, "voided_at": None, "state": "started"}]
    assert durable._thread_jobs(None, None, {"id": 1, "slug": BOARD}, ROLE, steps, NOW) == []
    assert durable._thread_jobs(None, None, {"id": 1, "slug": BOARD}, ROLE, [], NOW) == []


SESSION = "root-session-one"
RECEIPT = {
    "type": "external_thread",
    "thread_ref": "opaque:thread/one",
    "delegate_ref": "opaque:attempt/one",
}


def database_snapshot(env):
    """Compare database contents without printing fixture credentials on failure."""
    snapshots = []
    with env.boards_db() as conn:
        snapshots.append(hashlib.sha256("\n".join(conn.iterdump()).encode()).hexdigest())
    conn = env.runner_db()
    try:
        snapshots.append(hashlib.sha256("\n".join(conn.iterdump()).encode()).hexdigest())
    finally:
        conn.close()
    return snapshots


@pytest.fixture
def receipt_setup(env, monkeypatch):
    epoch, token = lease(env)
    ctx = env.ctx(env.a).with_claim_session(SESSION)
    card = env.card("External work")
    BoardService(ctx).task_take_next(board=BOARD, task=card)
    with env.boards_db() as conn:
        conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (at(9000), card))
    clock = {"now": at(1)}
    monkeypatch.setattr(durable, "_now", lambda: clock["now"])
    arguments = {"board": BOARD, "role": ROLE, "epoch": epoch, "token": token}
    tools.dispatch(
        ctx,
        "orch_journal_intent",
        {**arguments, "step_key": "external-one", "kind": "thread", "card": card},
    )
    clock["now"] = at(2)
    return ctx, clock, arguments, card


def record_receipt(setup, **updates):
    ctx, _clock, arguments, _card = setup
    return tools.dispatch(
        ctx,
        "orch_journal_record",
        {
            **arguments,
            "step_key": "external-one",
            "state": "started",
            "session_id": SESSION,
            "evidence": RECEIPT,
            **updates,
        },
    )


def receipt_heartbeat(setup, **updates):
    ctx, _clock, arguments, _card = setup
    return tools.dispatch(
        ctx,
        "orch_heartbeat",
        {**arguments, "step_key": "external-one", "session_id": SESSION, **updates},
    )


def test_external_receipt_round_trip_over_real_mcp_and_rest(hub, monkeypatch):
    from tests.test_orch_surfaces import Env as SurfaceEnv

    net = SurfaceEnv(hub)
    clock = {"now": at(0)}
    monkeypatch.setattr(durable, "_now", lambda: clock["now"])
    ok, current = net.mcp(net.a_token, "orch_resume", board=BOARD, role=ROLE, session_id=SESSION)
    assert ok
    arguments = {"epoch": current["epoch"], "token": current["token"]}
    card = net.boards.task_add("Actual caller-owned card", board=BOARD)["number"]
    ctx = net.ctx(net.a).with_claim_session(SESSION)
    BoardService(ctx).task_take_next(board=BOARD, task=card)
    with BoardStore(ctx.workspace_dir()).connection() as conn:
        conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (at(9000), card))
    base = f"boards/{BOARD}/roles/{ROLE}"
    status, intent = net.rest(
        "POST",
        base + "/journal",
        {**arguments, "step_key": "external-one", "kind": "thread", "card": card},
        token=net.a_token,
    )
    assert status == 200 and intent["state"] == "intent"
    clock["now"] = at(2)
    ok, recorded = net.mcp(
        net.a_token,
        "orch_journal_record",
        board=BOARD,
        role=ROLE,
        **arguments,
        step_key="external-one",
        state="started",
        session_id=SESSION,
        evidence=RECEIPT,
    )
    assert ok, "authenticated external_thread receipt was refused by the real MCP operation"
    assert recorded["evidence"] == {
        **RECEIPT,
        "by": durable._actor_ref(net.a),
        "session_id": SESSION,
    }
    clock["now"] = at(30)
    status, refreshed = net.rest(
        "POST",
        base + "/heartbeat",
        {**arguments, "step_key": "external-one", "session_id": SESSION},
        token=net.a_token,
    )
    assert status == 200 and refreshed["epoch"] == current["epoch"]
    status, checkpoint = net.rest("GET", base + "/checkpoint", token=net.reader_token)
    assert status == 200
    job = checkpoint["jobs"][0]
    assert (job["source"], job["id"], job["thread_id"]) == ("journal", None, RECEIPT["thread_ref"])
    assert job["delegate_ref"] == RECEIPT["delegate_ref"]
    assert job["last_heartbeat_at"] == at(30)
    assert (job["age_seconds"], job["heartbeat_age_seconds"], job["liveness"]) == (
        28,
        0,
        "reported",
    )
    assert checkpoint["next_step"]["step_key"] == "external-one"
    assert isinstance(checkpoint["next"], str)
    conn = net.runner_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("age,uncertain", [(1799, False), (1800, True), (1801, True)])
def test_external_receipt_expiry_uses_report_clock_and_is_read_only(
    env, receipt_setup, age, uncertain
):
    ctx, clock, arguments, _card = receipt_setup
    record_receipt(receipt_setup)
    clock["now"] = at(1700)
    tools.dispatch(ctx, "orch_heartbeat", arguments)
    clock["now"] = at(2 + age)
    before = database_snapshot(env)
    checkpoint = durable.checkpoint(ctx, BOARD, ROLE)
    assert database_snapshot(env) == before
    job = checkpoint["jobs"][0]
    expected = "in_doubt" if uncertain else "started"
    assert job["state"] == expected
    assert job["thread_ref"] == RECEIPT["thread_ref"]
    assert job["last_heartbeat_at"] == at(2), "lease renewal is not a thread heartbeat"
    assert job["age_seconds"] == job["heartbeat_age_seconds"] == age
    assert job["liveness"] == ("in_doubt" if uncertain else "reported")
    assert [step["step_key"] for step in checkpoint["in_doubt"]] == (
        ["external-one"] if uncertain else []
    )
    assert [step["step_key"] for step in checkpoint["open"]] == (
        [] if uncertain else ["external-one"]
    )
    assert checkpoint["next_step"]["state"] == expected
    assert ("Reconcile" if uncertain else "Await") in checkpoint["next"]


def test_external_receipt_heartbeat_refreshes_only_its_report(env, receipt_setup):
    ctx, clock, _arguments, _card = receipt_setup
    record_receipt(receipt_setup)
    clock["now"] = at(1000)
    receipt_heartbeat(receipt_setup)
    clock["now"] = at(2799)
    job = durable.checkpoint(ctx, BOARD, ROLE)["jobs"][0]
    assert (job["started_at"], job["last_heartbeat_at"]) == (at(2), at(1000))
    assert (job["age_seconds"], job["heartbeat_age_seconds"]) == (2797, 1799)
    assert job["state"] == "started" and job["liveness"] == "reported"


@pytest.mark.parametrize("state,code", [("done", None), ("failed", None), ("failed", "cancelled")])
def test_external_receipt_terminal_idempotence_and_no_resurrection(env, receipt_setup, state, code):
    ctx, clock, arguments, _card = receipt_setup
    record_receipt(receipt_setup)
    tools.dispatch(
        ctx,
        "orch_journal_intent",
        {
            **arguments,
            "step_key": "old-prose",
            "kind": "next",
            "detail": "Builder is still running",
        },
    )
    clock["now"] = at(3)
    finished = record_receipt(receipt_setup, state=state, code=code)
    clock["now"] = at(4)
    assert record_receipt(receipt_setup, state=state, code=code) == finished
    checkpoint = durable.checkpoint(ctx, BOARD, ROLE)
    assert checkpoint["jobs"] == [] and checkpoint["open"] == []
    assert checkpoint["next"] is None and checkpoint["next_step"] is None
    before = database_snapshot(env)
    with pytest.raises(ToolError) as exc:
        receipt_heartbeat(receipt_setup)
    assert getattr(exc.value, "code", None) == "transition"
    assert database_snapshot(env) == before
    with pytest.raises(ToolError):
        record_receipt(receipt_setup)
    assert database_snapshot(env) == before
    assert finished["evidence"]["thread_ref"] == RECEIPT["thread_ref"]


def test_external_receipt_late_heartbeat_cannot_clear_uncertainty_but_terminal_resolution_can(
    env, receipt_setup
):
    ctx, clock, arguments, _card = receipt_setup
    record_receipt(receipt_setup)
    clock["now"] = at(1700)
    tools.dispatch(ctx, "orch_heartbeat", arguments)
    clock["now"] = at(1802)
    before = database_snapshot(env)
    with pytest.raises(ToolError) as exc:
        receipt_heartbeat(receipt_setup)
    assert getattr(exc.value, "code", None) == "thread_in_doubt"
    assert database_snapshot(env) == before
    assert durable.checkpoint(ctx, BOARD, ROLE)["jobs"][0]["state"] == "in_doubt"
    record_receipt(receipt_setup, state="done")
    assert durable.checkpoint(ctx, BOARD, ROLE)["jobs"] == []


@pytest.mark.parametrize(
    "wrong", ["session", "actor", "foreign_claim", "expired_claim", "workspace"]
)
def test_external_receipt_registration_refuses_wrong_ownership_without_writes(
    env, receipt_setup, wrong
):
    ctx, _clock, arguments, card = receipt_setup
    updates = {}
    if wrong == "session":
        updates["session_id"] = "other-session"
    elif wrong == "actor":
        ctx = env.ctx(env.b).with_claim_session(SESSION)
    elif wrong in ("foreign_claim", "expired_claim"):
        with env.boards_db() as conn:
            if wrong == "foreign_claim":
                conn.execute("UPDATE tasks SET assignee=? WHERE id=?", (env.b.actor, card))
            else:
                conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (at(2), card))
    else:
        workspace = env.hub.db.create_workspace("elsewhere", env.hub.admin)
        ctx = env.ctx(replace(env.a, workspace_id=workspace)).with_claim_session(SESSION)
    before = database_snapshot(env)
    with pytest.raises(ToolError):
        tools.dispatch(
            ctx,
            "orch_journal_record",
            {
                **arguments,
                "step_key": "external-one",
                "state": "started",
                "session_id": SESSION,
                "evidence": RECEIPT,
                **updates,
            },
        )
    assert database_snapshot(env) == before


@pytest.mark.parametrize("wrong", ["session", "actor", "epoch", "token"])
def test_external_receipt_heartbeat_and_terminal_record_refuse_wrong_identity(
    env, receipt_setup, wrong
):
    ctx, clock, arguments, card = receipt_setup
    record_receipt(receipt_setup)
    clock["now"] = at(3)
    changes = {
        "session": {"session_id": "other-session"},
        "epoch": {"epoch": arguments["epoch"] + 1},
        "token": {"token": "not-the-lease-token"},
        "actor": {},
    }[wrong]
    if wrong == "actor":
        ctx = env.ctx(env.b).with_claim_session(SESSION)
    altered_setup = ctx, clock, arguments, card
    before = database_snapshot(env)
    for operation in (
        lambda: receipt_heartbeat(altered_setup, **changes),
        lambda: record_receipt(altered_setup, state="done", **changes),
    ):
        with pytest.raises(ToolError):
            operation()
        assert database_snapshot(env) == before


def test_external_receipt_retries_preserve_report_and_reject_duplicate_attempts(env, receipt_setup):
    ctx, clock, arguments, card = receipt_setup
    first = record_receipt(receipt_setup)
    clock["now"] = at(60)
    assert record_receipt(receipt_setup) == first
    job = durable.checkpoint(ctx, BOARD, ROLE)["jobs"][0]
    assert job["last_heartbeat_at"] == at(2)
    before = database_snapshot(env)
    with pytest.raises(ToolError) as exc:
        record_receipt(receipt_setup, evidence={**RECEIPT, "thread_ref": "other-thread"})
    assert getattr(exc.value, "code", None) == "step_exists"
    assert database_snapshot(env) == before
    tools.dispatch(
        ctx,
        "orch_journal_intent",
        {**arguments, "step_key": "duplicate-attempt", "kind": "thread", "card": card},
    )
    before = database_snapshot(env)
    with pytest.raises(ToolError) as exc:
        record_receipt(receipt_setup, step_key="duplicate-attempt")
    assert getattr(exc.value, "code", None) == "step_exists"
    assert database_snapshot(env) == before
    assert len(durable.checkpoint(ctx, BOARD, ROLE)["jobs"]) == 1


def test_external_receipt_distinct_attempts_can_share_a_thread(env, receipt_setup):
    ctx, clock, arguments, card = receipt_setup
    record_receipt(receipt_setup)
    clock["now"] = at(3)
    tools.dispatch(
        ctx,
        "orch_journal_intent",
        {**arguments, "step_key": "external-two", "kind": "thread", "card": card},
    )
    record_receipt(
        receipt_setup,
        step_key="external-two",
        evidence={**RECEIPT, "delegate_ref": "opaque:attempt/two"},
    )
    jobs = durable.checkpoint(ctx, BOARD, ROLE)["jobs"]
    assert len(jobs) == 2
    assert {job["delegate_ref"] for job in jobs} == {"opaque:attempt/one", "opaque:attempt/two"}
    assert {job["thread_ref"] for job in jobs} == {RECEIPT["thread_ref"]}


@pytest.mark.parametrize("loss", ["claim_expired", "lease_expired", "lease_changed"])
def test_external_receipt_stays_visible_but_uncertain_after_ownership_loss(
    env, receipt_setup, loss
):
    ctx, clock, _arguments, card = receipt_setup
    record_receipt(receipt_setup)
    if loss == "claim_expired":
        with env.boards_db() as conn:
            conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (at(3), card))
        clock["now"] = at(3)
    elif loss == "lease_expired":
        clock["now"] = at(1802)
    else:
        durable.resume(env.ctx(env.owner), BOARD, ROLE, force=True, now=at(3))
        clock["now"] = at(3)
    before = database_snapshot(env)
    checkpoint = durable.checkpoint(ctx, BOARD, ROLE)
    assert database_snapshot(env) == before
    assert checkpoint["jobs"][0]["state"] == "in_doubt"
    assert checkpoint["in_doubt"][0]["step_key"] == "external-one"
    assert "Reconcile" in checkpoint["next"]


def test_external_receipt_next_reconciles_uncertainty_before_waiting_for_fresh_work(
    env, receipt_setup
):
    ctx, clock, arguments, card = receipt_setup
    record_receipt(receipt_setup)
    clock["now"] = at(200)
    tools.dispatch(
        ctx,
        "orch_journal_intent",
        {**arguments, "step_key": "fresh-thread", "kind": "thread", "card": card},
    )
    record_receipt(
        receipt_setup,
        step_key="fresh-thread",
        evidence={**RECEIPT, "delegate_ref": "opaque:attempt/two"},
    )
    clock["now"] = at(1700)
    tools.dispatch(ctx, "orch_heartbeat", arguments)
    clock["now"] = at(1900)
    checkpoint = durable.checkpoint(ctx, BOARD, ROLE)
    assert {job["state"] for job in checkpoint["jobs"]} == {"started", "in_doubt"}
    assert checkpoint["next_step"]["step_key"] == "external-one"
    assert "Reconcile" in checkpoint["next"]


def test_external_receipt_completion_preserves_real_nonexternal_next_work(env, receipt_setup):
    ctx, clock, arguments, card = receipt_setup
    record_receipt(receipt_setup)
    tools.dispatch(
        ctx,
        "orch_journal_intent",
        {
            **arguments,
            "step_key": "actual-hold",
            "kind": "hold",
            "card": card,
            "detail": "Review result",
        },
    )
    clock["now"] = at(3)
    record_receipt(receipt_setup, state="done")
    checkpoint = durable.checkpoint(ctx, BOARD, ROLE)
    assert checkpoint["next_step"]["step_key"] == "actual-hold"
    assert isinstance(checkpoint["next"], str) and checkpoint["next"]


def test_external_receipt_native_jobs_coexist_and_linked_projection_is_not_duplicated(
    env, receipt_setup
):
    ctx, clock, arguments, card = receipt_setup
    record_receipt(receipt_setup)
    job_id = "job_aaaaaaaaaaaaaaaa"
    _orch_job(env, job=job_id, card=card, role=ROLE, step_key="native-one", state="queued")
    _start_step(
        env,
        epoch=arguments["epoch"],
        token=arguments["token"],
        step_key="native-one",
        card=card,
        job=job_id,
    )
    checkpoint = durable.checkpoint(ctx, BOARD, ROLE)
    assert sorted(job["source"] for job in checkpoint["jobs"]) == ["journal", "runner"]
    clock["now"] = at(3)
    record_receipt(receipt_setup, state="done")
    checkpoint = durable.checkpoint(ctx, BOARD, ROLE)
    assert checkpoint["next_step"]["step_key"] == "native-one"
    assert [job["id"] for job in checkpoint["jobs"]] == [job_id]
    # Older journal links can share the same job; preserve one real runner row.
    with env.runner_db() as conn:
        conn.execute("UPDATE orch_steps SET job_id=? WHERE step_key='external-one'", (job_id,))
    checkpoint = durable.checkpoint(ctx, BOARD, ROLE)
    assert [(job["id"], job["source"], job["step_key"]) for job in checkpoint["jobs"]] == [
        (job_id, "runner", "native-one")
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("thread_ref", ""),
        ("thread_ref", "x" * 2001),
        ("thread_ref", 3),
        ("delegate_ref", ""),
        ("by", "another-actor"),
        ("session_id", "another-session"),
    ],
)
def test_external_receipt_rejects_invalid_or_caller_stamped_metadata(
    env, receipt_setup, field, value
):
    before = database_snapshot(env)
    with pytest.raises(ToolError) as exc:
        record_receipt(receipt_setup, evidence={**RECEIPT, field: value})
    assert getattr(exc.value, "code", None) == "invalid_evidence"
    assert database_snapshot(env) == before


def test_external_receipt_can_omit_delegate_ref_but_cannot_act_as_verified_evidence(
    env, receipt_setup
):
    ctx, _clock, _arguments, _card = receipt_setup
    evidence = {"type": "external_thread", "thread_ref": "opaque:thread/without-task-ref"}
    record_receipt(receipt_setup, evidence=evidence)
    assert durable.checkpoint(ctx, BOARD, ROLE)["jobs"][0]["delegate_ref"] is None
    record_receipt(receipt_setup, state="in_doubt", evidence=evidence)
    record_receipt(receipt_setup, state="needs_person", evidence=evidence)
    before = database_snapshot(env)
    with pytest.raises(ToolError) as exc:
        record_receipt(receipt_setup, state="done", evidence=evidence)
    assert getattr(exc.value, "code", None) == "evidence_required"
    assert database_snapshot(env) == before


def test_external_receipt_void_retires_progress_without_cancelling_a_process(env, receipt_setup):
    ctx, clock, _arguments, _card = receipt_setup
    record_receipt(receipt_setup)
    clock["now"] = at(3)
    durable.void_step(env.ctx(env.owner), BOARD, ROLE, "external-one")
    assert durable.checkpoint(ctx, BOARD, ROLE)["jobs"] == []
    before = database_snapshot(env)
    with pytest.raises(ToolError) as exc:
        receipt_heartbeat(receipt_setup)
    assert getattr(exc.value, "code", None) == "step_voided"
    assert database_snapshot(env) == before
