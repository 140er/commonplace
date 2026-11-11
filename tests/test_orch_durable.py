"""Durable orchestrator: lease, journal, evidence, gates, checkpoint and resume.

Head-only feature and guard tests (R7, R8, R15, R16, R16b, R19, R23 and the unstarted
todo list). Clocks are injected; the board lives in a real workspace behind the
production gateway, the config is accepted through the orchestrator store and the
machine row is written with raw SQL (only its requester grant matters here).
"""

import hashlib
import json
import sqlite3
import threading

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.boards.store import BoardStore
from commonplace.extensions import ToolContext, ToolError
from commonplace.orch import durable, store as orch_store
from commonplace.runner import jobs, registry
from tests._commonplace_helpers import hub_fixture  # noqa: F401

BOARD = "acme"
ROLE = "main"
OTHER_ROLE = "builder"
MACHINE = "mach_0123456789abcdef"
T0 = "2030-01-01T00:00:00Z"
HEAD = "a" * 40
REPO = "example-org/example-repo"


def at(seconds, base=T0):
    return durable._after(base, seconds)


def config_text(board=BOARD, machine=MACHINE, extra=""):
    return "\n".join(
        [
            'schema = "commonplace.orchestrator/1"',
            f'project = "{board}"',
            "[limits]",
            "max_agents = 3",
            "[roles.main]",
            'kind = "orchestrator"',
            'harness = "t3"',
            'topic = "orchestrate"',
            'model_role = "main"',
            'workflow = "card-pipeline"',
            "prompt = '''Coordinate this project.'''",
            "[roles.builder]",
            'kind = "builder"',
            'parent = "main"',
            'harness = "t3"',
            'topic = "worker"',
            'model_role = "worker"',
            'workflow = "build-unit"',
            "prompt = '''Build the card.'''",
            "[workflows.card-pipeline]",
            'steps = ["builder"]',
            "[workflows.build-unit]",
            'steps = ["implement", "report"]',
            "[runner]",
            'role = "builder"',
            f'machine = "{machine}"',
            'project = "demo"',
            'runtime_mode = "approval-required"',
            "[[triggers]]",
            'on = "card.todo"',
            *([extra] if extra else []),
            "",
        ]
    )


class Env:
    def __init__(self, hub):
        self.hub = hub
        self.ws = hub.workspace
        self.owner = sessions.person_principal(hub.db, hub.admin, hub.workspace)
        self.owner_board = BoardService(self.ctx(self.owner))
        self.owner_board.board_add(slug=BOARD, wing="shared")
        self.owner_board.board_add(slug="plain", wing="shared")
        self.a = self.pat("orch-a")
        self.b = self.pat("orch-b")
        self.unlisted = self.pat("orch-unlisted")
        self.seat = self.pat("connect-seat", trust="standard")
        self.machine(
            requesters=[self.a.connection_id, self.b.connection_id, self.seat.connection_id]
        )
        text = config_text()
        st = orch_store.open_store(hub.gateway, self.ws)
        st.write(
            self.owner,
            BOARD,
            text,
            connections=[],
            sha256=hashlib.sha256(text.encode()).hexdigest(),
        )

    def ctx(self, principal):
        return ToolContext(principal=principal, gateway=self.hub.gateway)

    def reconfigure(self, extra):
        """Accept a live config carrying ``extra`` (a ``[fleet]`` table) before any lease."""
        text = config_text(extra=extra)
        orch_store.open_store(self.hub.gateway, self.ws).write(
            self.owner,
            BOARD,
            text,
            connections=[],
            sha256=hashlib.sha256(text.encode()).hexdigest(),
        )

    def pat(self, name, trust="trusted"):
        token = self.hub.token(
            name,
            profile="full",
            scopes=["memory:read", "memory:write"],
            read_wings=["shared"],
            write_wings=["shared"],
            trust=trust,
        )
        return self.hub.gateway.authenticate(f"Bearer {token}")

    def machine(self, requesters):
        registry.ensure(self.hub.db)
        with self.hub.db._lock, self.hub.db._tx() as conn:
            conn.execute(
                "INSERT INTO runner_machines(id, workspace_id, label, machine_connection_id, "
                "worker_connection_id, projects, topics, wings, requesters, binding_state, "
                "enrolled_by, updated_by, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,'owner_attested',?,?,?,?)",
                (
                    MACHINE,
                    self.ws,
                    "runner-one",
                    "conn_machine_example",
                    "conn_worker_example",
                    '["demo"]',
                    '["worker"]',
                    '["shared"]',
                    json.dumps(requesters),
                    self.hub.admin,
                    self.hub.admin,
                    T0,
                    T0,
                ),
            )

    def card(self, title="Example card", status="todo"):
        return self.owner_board.task_add(title, board=BOARD, status=status)["number"]

    def runner_db(self):
        conn = sqlite3.connect(
            str(self.hub.gateway.workspace_dir(self.ws)) + "/runner.sqlite3", isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        return conn

    def boards_db(self):
        return BoardStore(self.hub.gateway.workspace_dir(self.ws)).connection()

    def resume(self, principal, role=ROLE, now=T0, **kw):
        return durable.resume(self.ctx(principal), BOARD, role, now=now, **kw)


@pytest.fixture
def env(hub):
    return Env(hub)


def code_of(excinfo):
    return getattr(excinfo.value, "code", None)


def lease(env, principal=None, role=ROLE, now=T0):
    claim = env.resume(principal or env.a, role=role, now=now)
    return claim["epoch"], claim["token"]


@pytest.mark.parametrize("card_bound", [False, True])
@pytest.mark.parametrize("result", ["done", "failed", "next", "pass", "fail", "error", "budget"])
def test_fleet_result_destination(env, card_bound, result):
    """A result cannot disappear: its owning card or explicit park is persisted."""
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    card = env.card() if card_bound else None
    if result in ("pass", "fail", "error", "budget"):
        durable.gate_open(
            ctx,
            BOARD,
            ROLE,
            epoch,
            token,
            "result",
            repo=REPO,
            pr=7,
            head=HEAD,
            card=card,
            now=at(1),
        )
        step = durable.gate_record(ctx, BOARD, ROLE, epoch, token, "result", result, now=at(2))[
            "step"
        ]
    else:
        step = durable.journal_intent(
            ctx,
            BOARD,
            ROLE,
            epoch,
            token,
            "result",
            "next" if result == "next" else "thread",
            card=card,
            detail="Inspect the result.",
            now=at(1),
        )
        if result != "next":
            step = durable.journal_record(
                ctx, BOARD, ROLE, epoch, token, "result", result, now=at(2)
            )
    expected = (
        {"kind": "card", "board": BOARD, "card": card}
        if card_bound
        else {"kind": "parked", "reason": "no_owning_card"}
    )
    assert step.get("destination") == expected, "finished result has no recorded destination"
    # A different principal, the owner, must be able to read the recorded route.
    checkpoint = durable.checkpoint(env.ctx(env.owner), BOARD, ROLE, now=at(3))
    if result != "next":
        assert checkpoint["finished"][0]["destination"] == expected
    else:
        assert checkpoint["next_step"]["destination"] == expected
    if not card_bound:
        assert [step["step_key"] for step in checkpoint["parked"]] == ["result"]
        assert checkpoint["parked"][0]["destination"] == expected
    with env.runner_db() as conn:
        row = conn.execute(
            "SELECT destination_kind, destination_reason FROM orch_steps WHERE step_key='result'"
        ).fetchone()
    assert row["destination_kind"] == expected["kind"]
    assert row["destination_reason"] == expected.get("reason")


@pytest.mark.parametrize("job_state", ["started", "failed", "cancelled"])
def test_fleet_runner_result_destination(env, job_state):
    epoch, token = lease(env)
    card = env.card()
    job = "job_ffffffffffffffff"
    _orch_job(env, job=job, card=card, role=ROLE, step_key="run", state=job_state)
    _start_step(env, epoch=epoch, token=token, step_key="run", card=card, job=job)
    checkpoint = durable.checkpoint(env.ctx(env.owner), BOARD, ROLE, now=at(6))
    step = checkpoint["finished"][0]
    assert step.get("destination") == {"kind": "card", "board": BOARD, "card": card}
    # Checkpoint-derived completion does not need a resume write to have a destination.
    with env.runner_db() as conn:
        row = conn.execute("SELECT state, destination_kind FROM orch_steps").fetchone()
    assert row["state"] == "waiting" and row["destination_kind"] == "card"


def test_fleet_destination_migrates_old_steps_without_changing_history():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        # The exact earlier table definition, with only the two new columns omitted.
        old_schema = durable.SCHEMA.replace(
            " destination_kind TEXT CHECK (destination_kind IN ('card','parked')),\n", ""
        ).replace(" destination_reason TEXT,\n", "")
        conn.executescript(old_schema)
        assert "destination_kind" not in {
            row[1] for row in conn.execute("PRAGMA table_info(orch_steps)")
        }
        for key, card in (("unbound", None), ("bound", 7)):
            conn.execute(
                "INSERT INTO orch_steps(board,role,step_key,kind,card,state,detail,created_epoch,"
                "epoch,created_at,updated_at) VALUES (?,?,?,'thread',?,'done','verbatim',1,1,?,?)",
                (BOARD, ROLE, key, card, T0, T0),
            )
        before = [dict(row) for row in conn.execute("SELECT * FROM orch_steps ORDER BY id")]
        durable.ensure_schema(conn)
        durable.ensure_schema(conn)
        after = [dict(row) for row in conn.execute("SELECT * FROM orch_steps ORDER BY id")]
        assert [{key: row[key] for key in before[0]} for row in after] == before
        assert [row["destination_kind"] for row in after] == ["parked", "card"]
        assert [row["destination_reason"] for row in after] == ["no_owning_card", None]
        assert conn.execute("SELECT COUNT(*) FROM orch_journal").fetchone()[0] == 0
    finally:
        conn.close()


# ----------------------------------------------------------- fleet rules (b), (c)


def _gate(env, principal, key, head, verdict, *, role=ROLE, claim, pr=7, card=None, at_=1):
    """Open and record one gate; ``claim`` is the ``(epoch, token)`` of ``role``'s lease."""
    ctx, (epoch, token) = env.ctx(principal), claim
    durable.gate_open(
        ctx, BOARD, role, epoch, token, key, repo=REPO, pr=pr, head=head, card=card, now=at(at_)
    )
    return durable.gate_record(ctx, BOARD, role, epoch, token, key, verdict, now=at(at_ + 1))


def _heads(count):
    return [format(index + 10, "x") * 40 for index in range(count)]


def test_fleet_repair_failures_join_one_repair_and_the_bound_is_honoured(env):
    """Rule (b): failed gates of one pull request share one repair with one owner; at the
    stated bound it is parked and no further gate attempt starts."""
    owner, other = lease(env, role=ROLE), lease(env, env.b, role=OTHER_ROLE)
    h1, h2, h3, h4 = _heads(4)
    first = _gate(env, env.a, "g1", h1, "fail", claim=owner)
    repair = first.get("repair")
    assert repair is not None, "a failed gate opened no repair"
    assert (repair["owner_role"], repair["failures"], repair["state"]) == (ROLE, 1, "open")
    assert repair["bound"] == 3
    joined = _gate(env, env.b, "g2", h2, "fail", role=OTHER_ROLE, claim=other, at_=3)
    assert (joined["repair"] or {}).get("id") == repair["id"], "a second repair was started"
    assert (joined["repair"]["owner_role"], joined["repair"]["failures"]) == (ROLE, 2)
    last = _gate(env, env.a, "g3", h3, "fail", claim=owner, at_=5)["repair"]
    assert (last["failures"], last["state"]) == (3, "exhausted")
    assert last["destination"] == {"kind": "parked", "reason": "repair_exhausted"}
    with pytest.raises(ToolError) as exc:
        durable.gate_open(
            env.ctx(env.a), BOARD, ROLE, *owner, "g4", repo=REPO, pr=7, head=h4, now=at(7)
        )
    assert code_of(exc) == "repair_exhausted"
    with env.runner_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orch_repairs").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM orch_steps WHERE step_key='g4'").fetchone()[0] == 0
        )
    view = durable.checkpoint(env.ctx(env.owner), BOARD, ROLE, now=at(8))
    assert [(r["id"], r["state"]) for r in view["repairs"]] == [(repair["id"], "exhausted")]


def test_fleet_repair_is_per_pull_request_and_a_pass_resolves_it(env):
    claim = lease(env)
    h1, h2, h3 = _heads(3)
    _gate(env, env.a, "g1", h1, "fail", claim=claim, pr=7)
    elsewhere = _gate(env, env.a, "g2", h2, "fail", claim=claim, pr=8, at_=3)
    assert elsewhere["repair"]["failures"] == 1, "another pull request shares the repair"
    passed = _gate(env, env.a, "g3", h3, "pass", claim=claim, pr=7, at_=5)["repair"]
    assert passed["state"] == "resolved"
    again = _gate(env, env.a, "g4", "e" * 40, "fail", claim=claim, pr=7, at_=7)["repair"]
    assert (again["failures"], again["state"]) == (1, "open")
    assert again["id"] != passed["id"]


@pytest.mark.parametrize("bound", [1, 2, 4])
def test_fleet_repair_bound_is_a_board_override(env, bound):
    env.reconfigure(f"[fleet]\nrepair_attempts = {bound}")
    claim = lease(env)
    for index, head in enumerate(_heads(bound)):
        repair = _gate(env, env.a, f"g{index}", head, "fail", claim=claim, at_=1 + 2 * index)[
            "repair"
        ]
        assert repair["bound"] == bound
    assert repair["state"] == "exhausted"
    with pytest.raises(ToolError) as exc:
        durable.gate_open(
            env.ctx(env.a), BOARD, ROLE, *claim, "next", repo=REPO, pr=7, head="f" * 40, now=at(50)
        )
    assert code_of(exc) == "repair_exhausted"


def _owned_card(env, *, key="run", job="job_aaaaaaaaaaaaaaaa"):
    """A card whose latest unvoided runner start belongs to the ``main`` role's lease."""
    claim = lease(env)
    card = env.card()
    _orch_job(env, job=job, card=card, role=ROLE, step_key=key)
    _start_step(env, epoch=claim[0], token=claim[1], step_key=key, card=card, job=job)
    return claim, card


def test_fleet_failed_gate_finding_reaches_the_owning_session(env):
    """Rule (c): the finding goes to the role that owns the card, not to the reporter."""
    owner, card = _owned_card(env)
    reporter = lease(env, env.b, role=OTHER_ROLE, now=at(2))
    result = _gate(
        env, env.b, "g1", HEAD, "fail", role=OTHER_ROLE, claim=reporter, card=card, at_=3
    )
    finding = result.get("finding")
    assert finding is not None, "a failed gate produced no finding"
    assert (finding["owner_role"], finding["state"]) == (ROLE, "routed")
    assert finding["destination"] == {"kind": "session", "board": BOARD, "role": ROLE}
    ctx = env.ctx(env.owner)
    assert [f["id"] for f in durable.checkpoint(ctx, BOARD, ROLE, now=at(4))["inbox_findings"]] == [
        finding["id"]
    ]
    assert durable.checkpoint(ctx, BOARD, OTHER_ROLE, now=at(4))["inbox_findings"] == []
    with pytest.raises(ToolError) as exc:
        durable.finding_ack(env.ctx(env.b), BOARD, OTHER_ROLE, *reporter, finding["id"], now=at(5))
    assert code_of(exc) == "not_owner"
    acked = durable.finding_ack(env.ctx(env.a), BOARD, ROLE, *owner, finding["id"], now=at(6))
    assert acked["finding"]["state"] == "acked"
    assert durable.checkpoint(ctx, BOARD, ROLE, now=at(7))["inbox_findings"] == []


def test_fleet_review_finding_is_routed_and_retries_are_idempotent(env):
    owner, card = _owned_card(env)
    reporter = lease(env, env.b, role=OTHER_ROLE, now=at(2))
    args = (env.ctx(env.b), BOARD, OTHER_ROLE, *reporter, "r1")
    first = durable.finding_report(*args, card=card, detail="Missing test.", now=at(3))["finding"]
    assert (first["owner_role"], first["state"], first["source"]) == (ROLE, "routed", "review")
    assert durable.finding_report(*args, card=card, detail="Missing test.", now=at(4)) == {
        "finding": first
    }
    with pytest.raises(ToolError) as exc:
        durable.finding_report(*args, card=card, detail="Different.", now=at(5))
    assert code_of(exc) == "step_exists"


@pytest.mark.parametrize("why", ["no_owning_card", "no_owning_session", "owner_session_gone"])
def test_fleet_finding_is_parked_with_a_reason_when_no_session_can_take_it(env, why):
    claim = lease(env, env.b, role=OTHER_ROLE)
    if why == "no_owning_card":
        finding = _gate(env, env.b, "g1", HEAD, "fail", role=OTHER_ROLE, claim=claim)["finding"]
    else:
        card = env.card()
        if why == "owner_session_gone":
            owner, card = _owned_card(env)
            durable.release(env.ctx(env.a), BOARD, ROLE, *owner, now=at(2))
        finding = durable.finding_report(
            env.ctx(env.b), BOARD, OTHER_ROLE, *claim, "r1", card=card, detail="x", now=at(3)
        )["finding"]
    assert (finding["state"], finding["reason"]) == ("parked", why)
    assert finding["destination"] == {"kind": "parked", "reason": why}
    view = durable.checkpoint(env.ctx(env.owner), BOARD, OTHER_ROLE, now=at(9))
    assert [f["id"] for f in view["findings"]] == [finding["id"]]


def test_fleet_finding_routing_is_a_board_override(env):
    env.reconfigure("[fleet]\nroute_findings = false")
    owner, card = _owned_card(env)
    reporter = lease(env, env.b, role=OTHER_ROLE, now=at(2))
    result = durable.finding_report(
        env.ctx(env.b), BOARD, OTHER_ROLE, *reporter, "r1", card=card, detail="x", now=at(3)
    )
    assert (result["finding"]["state"], result["finding"]["reason"]) == ("parked", "routing_off")


def test_fleet_fresh_reason_column_is_added_without_changing_steps():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        # A database that only has the destination columns: the schema without fresh_reason.
        conn.executescript(durable.SCHEMA.replace(" fresh_reason TEXT,\n", ""))
        conn.execute(
            "INSERT INTO orch_steps(board,role,step_key,kind,card,state,detail,created_epoch,"
            "epoch,created_at,updated_at) VALUES (?,?,?,'thread',7,'done','verbatim',1,1,?,?)",
            (BOARD, ROLE, "old", T0, T0),
        )
        assert "fresh_reason" not in {
            row[1] for row in conn.execute("PRAGMA table_info(orch_steps)")
        }
        before = dict(conn.execute("SELECT * FROM orch_steps").fetchone())
        durable.ensure_schema(conn)
        durable.ensure_schema(conn)
        after = dict(conn.execute("SELECT * FROM orch_steps").fetchone())
        assert after.pop("fresh_reason") is None
        for row in (before, after):  # the destination backfill is rule (a)'s migration
            row.pop("destination_kind"), row.pop("destination_reason")
        assert after == before
    finally:
        conn.close()


# ---------------------------------------------------------------------- R7


def test_r7_two_concurrent_claims_one_epoch_other_held(env):
    barrier = threading.Barrier(2)
    results, errors = {}, {}

    def claim(name, principal):
        barrier.wait()
        try:
            results[name] = env.resume(principal)
        except ToolError as exc:
            errors[name] = exc

    threads = [
        threading.Thread(target=claim, args=(name, p)) for name, p in (("a", env.a), ("b", env.b))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(results) == 1 and len(errors) == 1, (results, errors)
    (winner,) = results.values()
    (loser,) = errors.values()
    assert winner["epoch"] == 1
    assert loser.code == "held" and loser.details["epoch"] == 1
    with env.runner_db() as conn:
        rows = conn.execute("SELECT epoch FROM orch_leases WHERE board=?", (BOARD,)).fetchall()
    assert [row["epoch"] for row in rows] == [1]


def test_r7_live_lease_refuses_any_other_claim_until_release_or_expiry(env):
    epoch, token = lease(env, env.a)
    for principal in (env.b, env.a):
        with pytest.raises(ToolError) as exc:
            env.resume(principal, now=at(60))
        assert code_of(exc) == "held"
        assert exc.value.details["holder"] == "conn:" + env.a.connection_id
    durable.release(env.ctx(env.a), BOARD, ROLE, epoch, token, now=at(61))
    claim = env.resume(env.b, now=at(62))
    assert claim["epoch"] == 2
    # A different role is a different lease.
    assert env.resume(env.a, role=OTHER_ROLE, now=at(63))["epoch"] == 1


def test_r7_token_is_stored_hashed_and_bound_to_incarnation(env):
    epoch, token = lease(env)
    with env.runner_db() as conn:
        row = conn.execute("SELECT token_hash FROM orch_leases").fetchone()
        incarnation = conn.execute("SELECT incarnation FROM orch_state").fetchone()[0]
    assert token not in row["token_hash"]
    assert row["token_hash"] == hashlib.sha256((token + incarnation).encode()).hexdigest()
    with env.runner_db() as conn:
        conn.execute("UPDATE orch_state SET incarnation=?", ("0" * 32,))
    with pytest.raises(ToolError) as exc:
        durable.heartbeat(env.ctx(env.a), BOARD, ROLE, epoch, token, now=at(10))
    assert code_of(exc) == "fenced"


def test_owner_force_release_lets_a_successor_claim(env):
    lease(env, env.a)
    with pytest.raises(ToolError) as exc:
        durable.force_release(env.ctx(env.b), BOARD, ROLE, now=at(5))
    assert code_of(exc) == "owner_only"
    assert durable.force_release(env.ctx(env.owner), BOARD, ROLE, now=at(5))["released"]
    assert env.resume(env.b, now=at(6))["epoch"] == 2
    with pytest.raises(ToolError) as exc:
        env.resume(env.a, now=at(7), force=True)
    assert code_of(exc) == "owner_only"
    assert env.resume(env.owner, now=at(8), force=True)["epoch"] == 3


# ---------------------------------------------------------------------- R8


@pytest.mark.parametrize("who", ["seat", "unlisted"])
def test_r8_lease_claim_refused_for_connect_seat_and_unlisted_pat(env, who):
    with pytest.raises(ToolError) as exc:
        env.resume(getattr(env, who))
    assert code_of(exc) == "not_allowed" and exc.value.status == 403
    with env.runner_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orch_leases").fetchone()[0] == 0


def test_r8_listed_trusted_pat_and_person_owner_may_claim(env):
    assert env.resume(env.a)["epoch"] == 1
    assert env.resume(env.owner, role=OTHER_ROLE)["epoch"] == 1


def test_r8_no_lease_without_runner_config_or_for_unknown_role(env):
    with pytest.raises(ToolError) as exc:
        durable.resume(env.ctx(env.a), "plain", ROLE, now=T0)
    assert code_of(exc) == "not_configured"
    with pytest.raises(ToolError) as exc:
        env.resume(env.a, role="reviewer")
    assert code_of(exc) == "unknown_role"


def test_r8_journal_writes_recheck_requester_authority(env):
    epoch, token = lease(env, env.a)
    with env.hub.db._lock, env.hub.db._tx() as conn:
        conn.execute("UPDATE runner_machines SET requesters='[]'")
    with pytest.raises(ToolError) as exc:
        durable.journal_intent(env.ctx(env.a), BOARD, ROLE, epoch, token, "m1", "merge", now=at(1))
    assert code_of(exc) == "not_allowed"


def test_resume_refused_while_restore_flag_is_set(env):
    with env.runner_db() as conn:
        conn.execute("UPDATE orch_state SET restore_flag=1, restore_after=?", (T0,))
    with pytest.raises(ToolError) as exc:
        env.resume(env.a)
    assert code_of(exc) == "restore_pending"


def test_restore_drops_leases_so_resume_after_the_ack_claims_a_new_epoch(env):
    """A converted restore marker releases every lease in the flag's transaction: after
    the ack a successor claims epoch+1 at once (not 'held' until the old expiry) and
    the old token stays fenced."""
    ctx = env.ctx(env.a)
    epoch, token = lease(env, env.a)
    directory = env.hub.gateway.workspace_dir(env.ws)
    marker = {"version": 1, "backup_created_at": T0, "id": "d" * 32}
    with open(f"{directory}/{orch_store.RESTORE_MARKER}", "w") as handle:
        json.dump(marker, handle)
    st = orch_store.open_store(env.hub.gateway, env.ws)
    with env.runner_db() as conn:
        row = conn.execute("SELECT token_hash, released_by FROM orch_leases").fetchone()
    assert (row["token_hash"], row["released_by"]) == (None, "restore")
    st.restore_ack(env.owner, st.restore_state().ack_text)
    claim = env.resume(env.b, now=at(60))
    assert claim["epoch"] == epoch + 1
    with pytest.raises(ToolError) as exc:
        durable.heartbeat(ctx, BOARD, ROLE, epoch, token, now=at(61))
    assert code_of(exc) == "fenced"


# ---------------------------------------------------------------------- R23


def test_r23_holder_past_ttl_is_fenced_and_resume_gets_new_epoch_without_redo(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env, env.a)
    card = env.card()
    durable.journal_intent(
        ctx, BOARD, ROLE, epoch, token, "merge-1", "merge", card=card, now=at(10)
    )
    durable.journal_record(
        ctx,
        BOARD,
        ROLE,
        epoch,
        token,
        "merge-1",
        "done",
        evidence={"type": "holder_attested", "note": "merged"},
        now=at(20),
    )
    # Fenced writes renew: still live 1700 s after the last write, past the first TTL.
    durable.heartbeat(ctx, BOARD, ROLE, epoch, token, now=at(1700))
    durable.heartbeat(ctx, BOARD, ROLE, epoch, token, now=at(3000))
    late = at(3000 + durable.TTL_SECONDS)
    # A fenced start (check_lease inside its transaction) after the TTL is fenced.
    st = durable._open(ctx)
    with pytest.raises(ToolError) as exc, st.transaction() as conn:
        durable.check_lease(conn, board=BOARD, role=ROLE, epoch=epoch, token=token, now=late)
    assert code_of(exc) == "fenced"
    for call in (
        lambda: durable.heartbeat(ctx, BOARD, ROLE, epoch, token, now=late),
        lambda: durable.journal_intent(ctx, BOARD, ROLE, epoch, token, "m2", "merge", now=late),
    ):
        with pytest.raises(ToolError) as exc:
            call()
        assert code_of(exc) == "fenced"
    claim = env.resume(env.a, now=late)
    assert claim["epoch"] == epoch + 1
    finished = {s["step_key"]: s for s in claim["checkpoint"]["finished"]}
    assert finished["merge-1"]["state"] == "done"
    # Same step under the new epoch: the stored step comes back, nothing is redone.
    again = durable.journal_intent(
        ctx, BOARD, ROLE, claim["epoch"], claim["token"], "merge-1", "merge", card=card, now=late
    )
    assert again["state"] == "done" and again["epoch"] == epoch
    # The old epoch stays fenced even with its own token.
    with pytest.raises(ToolError) as exc:
        durable.heartbeat(ctx, BOARD, ROLE, epoch, token, now=late)
    assert code_of(exc) == "fenced"


def test_r23_wrong_token_or_epoch_is_fenced(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    for bad_epoch, bad_token in ((epoch, token + "x"), (epoch + 1, token), (True, token)):
        with pytest.raises(ToolError) as exc:
            durable.heartbeat(ctx, BOARD, ROLE, bad_epoch, bad_token, now=at(1))
        assert code_of(exc) == "fenced"
        assert token not in str(exc.value)


def test_new_epoch_marks_open_external_steps_in_doubt_and_never_reissues(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    durable.journal_intent(ctx, BOARD, ROLE, epoch, token, "merge-1", "merge", now=at(1))
    durable.journal_intent(ctx, BOARD, ROLE, epoch, token, "wait-1", "hold", now=at(2))
    durable.journal_intent(
        ctx, BOARD, ROLE, epoch, token, "n1", "next", detail="Check PR 7.", now=at(3)
    )
    claim = env.resume(env.b, now=at(3 + durable.TTL_SECONDS + 10))
    cp = claim["checkpoint"]
    assert [s["step_key"] for s in cp["in_doubt"]] == ["merge-1"]
    assert [s["step_key"] for s in cp["holds"]] == ["wait-1"]
    assert cp["next"] == "Check PR 7."
    e2, t2 = claim["epoch"], claim["token"]
    again = durable.journal_intent(ctx, BOARD, ROLE, e2, t2, "merge-1", "merge", now=at(1900))
    assert again["state"] == "in_doubt"
    with pytest.raises(ToolError) as exc:
        durable.journal_record(ctx, BOARD, ROLE, e2, t2, "merge-1", "started", now=at(1901))
    assert code_of(exc) == "transition"
    step = durable.journal_record(ctx, BOARD, ROLE, e2, t2, "merge-1", "needs_person", now=at(1902))
    assert step["state"] == "needs_person"
    with pytest.raises(ToolError) as exc:
        durable.journal_record(
            ctx,
            BOARD,
            ROLE,
            e2,
            t2,
            "merge-1",
            "done",
            evidence={"type": "holder_attested", "note": "I think it merged"},
            now=at(1903),
        )
    assert code_of(exc) == "evidence_required"


def test_step_key_reuse_with_other_kind_or_card_is_refused(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    card = env.card()
    durable.journal_intent(ctx, BOARD, ROLE, epoch, token, "s1", "merge", now=at(1))
    for kind, kw in (("thread", {}), ("merge", {"card": card})):
        with pytest.raises(ToolError) as exc:
            durable.journal_intent(ctx, BOARD, ROLE, epoch, token, "s1", kind, now=at(2), **kw)
        assert code_of(exc) == "step_exists" and exc.value.details["step"]["kind"] == "merge"
    for bad in ("", "x" * 81, "a b", None):
        with pytest.raises(ToolError) as exc:
            durable.journal_intent(ctx, BOARD, ROLE, epoch, token, bad, "merge", now=at(3))
        assert code_of(exc) == "invalid_step_key"


# ---------------------------------------------------------------------- R15


def test_r15_gate_fail_is_terminal_for_the_head_across_roles_until_owner_voids(env):
    ctx_a, ctx_b = env.ctx(env.a), env.ctx(env.b)
    ea, ta = lease(env, env.a, ROLE)
    eb, tb = lease(env, env.b, OTHER_ROLE)
    gate = dict(repo=REPO, pr=7, head=HEAD)
    opened = durable.gate_open(ctx_a, BOARD, ROLE, ea, ta, "gate-1", now=at(1), **gate)
    assert opened["gate"]["state"] == "open" and opened["step"]["state"] == "intent"
    # Retry of the same gate step returns it.
    assert durable.gate_open(ctx_a, BOARD, ROLE, ea, ta, "gate-1", now=at(2), **gate)["step"]
    result = durable.gate_record(ctx_a, BOARD, ROLE, ea, ta, "gate-1", "fail", now=at(3))
    assert result["gate"]["state"] == "gate_fail"
    assert result["step"]["state"] == "failed" and result["step"]["code"] == "gate_fail"
    assert result["gate"]["attested_by"] == "conn:" + env.a.connection_id
    # The verdict is final.
    with pytest.raises(ToolError) as exc:
        durable.gate_record(ctx_a, BOARD, ROLE, ea, ta, "gate-1", "pass", now=at(4))
    assert code_of(exc) == "gate_decided"
    # A second gate step on the same head: same role or any other role.
    for ctx, role, epoch, token in ((ctx_a, ROLE, ea, ta), (ctx_b, OTHER_ROLE, eb, tb)):
        with pytest.raises(ToolError) as exc:
            durable.gate_open(ctx, BOARD, role, epoch, token, "gate-2", now=at(5), **gate)
        assert code_of(exc) == "gate_exists"
        assert exc.value.details["gate"]["state"] == "gate_fail"
    # A new head is a new gate.
    durable.gate_open(
        ctx_b, BOARD, OTHER_ROLE, eb, tb, "gate-3", now=at(6), **{**gate, "head": "b" * 40}
    )
    # Only the owner voids; then the head may be gated again.
    with pytest.raises(ToolError) as exc:
        durable.void_step(ctx_a, BOARD, ROLE, "gate-1", now=at(7))
    assert code_of(exc) == "owner_only"
    voided = durable.void_step(env.ctx(env.owner), BOARD, ROLE, "gate-1", now=at(7))
    assert voided["voided"]
    again = durable.gate_open(ctx_b, BOARD, OTHER_ROLE, eb, tb, "gate-4", now=at(8), **gate)
    assert again["gate"]["state"] == "open"


@pytest.mark.parametrize("verdict", ["pass", "error", "budget"])
def test_r15_one_attempt_per_head_whatever_the_verdict(env, verdict):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    gate = dict(repo=REPO, pr=9, head=HEAD)
    durable.gate_open(ctx, BOARD, ROLE, epoch, token, "g1", now=at(1), **gate)
    result = durable.gate_record(ctx, BOARD, ROLE, epoch, token, "g1", verdict, now=at(2))
    assert result["step"]["state"] == ("done" if verdict == "pass" else "failed")
    with pytest.raises(ToolError) as exc:
        durable.gate_open(ctx, BOARD, ROLE, epoch, token, "g2", now=at(3), **gate)
    assert code_of(exc) == "gate_exists"


def test_r15_open_gate_blocks_a_second_attempt_and_goes_in_doubt_on_new_epoch(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    gate = dict(repo=REPO, pr=3, head=HEAD)
    durable.gate_open(ctx, BOARD, ROLE, epoch, token, "g1", now=at(1), **gate)
    claim = env.resume(env.b, now=at(1 + durable.TTL_SECONDS))
    assert [s["step_key"] for s in claim["checkpoint"]["in_doubt"]] == ["g1"]
    e2, t2 = claim["epoch"], claim["token"]
    with pytest.raises(ToolError) as exc:
        durable.gate_open(env.ctx(env.b), BOARD, ROLE, e2, t2, "g2", now=at(1900), **gate)
    assert code_of(exc) == "gate_exists"
    # The truth check found the verdict lines: record it on the in_doubt step.
    done = durable.gate_record(env.ctx(env.b), BOARD, ROLE, e2, t2, "g1", "gate_pass", now=at(1901))
    assert done["step"]["state"] == "done"
    with pytest.raises(ToolError) as exc:
        durable.journal_record(env.ctx(env.b), BOARD, ROLE, e2, t2, "g1", "failed", now=at(1902))
    assert code_of(exc) == "use_gate_record"


# --------------------------------------------------------------- R16 / R16b


def test_r16_card_state_evidence_needs_the_card_done(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    card = env.card()
    durable.journal_intent(ctx, BOARD, ROLE, epoch, token, "c1", "card", card=card, now=at(1))
    evidence = {"type": "card_state"}
    with pytest.raises(ToolError) as exc:
        durable.journal_record(
            ctx, BOARD, ROLE, epoch, token, "c1", "done", evidence=evidence, now=at(2)
        )
    assert code_of(exc) == "card_not_done" and exc.value.details["card_status"] == "todo"
    with pytest.raises(ToolError) as exc:
        durable.journal_record(
            ctx,
            BOARD,
            ROLE,
            epoch,
            token,
            "c1",
            "done",
            evidence={"type": "holder_attested", "note": "done, trust me"},
            now=at(2),
        )
    assert code_of(exc) == "evidence_required"
    with env.boards_db() as conn:
        conn.execute("UPDATE tasks SET status='done', done_at=? WHERE id=?", (at(2), card))
    step = durable.journal_record(
        ctx, BOARD, ROLE, epoch, token, "c1", "done", evidence=evidence, now=at(3)
    )
    assert step["state"] == "done"
    assert step["evidence"]["type"] == "card_state" and step["evidence"]["status"] == "done"


def test_r16_card_must_be_on_the_board(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    other = env.owner_board.task_add("Elsewhere", board="plain")["number"]
    with pytest.raises(ToolError) as exc:
        durable.journal_intent(ctx, BOARD, ROLE, epoch, token, "c1", "card", card=other, now=at(1))
    assert code_of(exc) == "card_not_found"


def _orch_job(env, *, job, card, role, step_key, state="started", board=BOARD, request_key=None):
    """A runner job as fenced_start would leave it (the runner migration adds step_key)."""
    with env.runner_db() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runner_jobs)")}
        if "step_key" not in columns:
            conn.execute("ALTER TABLE runner_jobs ADD COLUMN step_key TEXT")
        conn.execute(
            "INSERT INTO runner_jobs(id,task_number,board_slug,machine_id,project,topic,"
            "grant_version,runtime_mode,requested_by,request_key,request_hash,state,started_at,"
            "created_at,updated_at,step_key) VALUES (?,?,?,?,?,?,1,'approval-required',?,?,?,?,?,?,?,?)",
            (
                job,
                card,
                board,
                MACHINE,
                "demo",
                "worker",
                "conn:" + env.a.connection_id,
                request_key or f"orch:{role}:{step_key}",
                "h",
                state,
                at(5) if state == "started" else None,
                at(1),
                at(1),
                step_key,
            ),
        )


def _start_step(env, *, epoch, token, step_key, card, job):
    st = durable._open(env.ctx(env.a))
    with st.transaction() as conn:
        durable.check_lease(conn, board=BOARD, role=ROLE, epoch=epoch, token=token, now=at(1))
        durable.insert_step_in(
            conn,
            board=BOARD,
            role=ROLE,
            step_key=step_key,
            card=card,
            request_hash="h-" + step_key,
            epoch=epoch,
            actor="conn:" + env.a.connection_id,
            now=at(1),
            job_id=job,
        )


def test_r16b_runner_job_evidence_of_another_steps_job_is_refused(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    card = env.card()
    _orch_job(env, job="job_aaaaaaaaaaaaaaaa", card=card, role=ROLE, step_key="s1")
    _orch_job(env, job="job_bbbbbbbbbbbbbbbb", card=card, role=ROLE, step_key="s2", state="failed")
    _start_step(env, epoch=epoch, token=token, step_key="s1", card=card, job="job_aaaaaaaaaaaaaaaa")
    _start_step(env, epoch=epoch, token=token, step_key="s2", card=card, job="job_bbbbbbbbbbbbbbbb")
    with pytest.raises(ToolError) as exc:
        durable.journal_record(
            ctx,
            BOARD,
            ROLE,
            epoch,
            token,
            "s2",
            "done",
            evidence={"type": "runner_job", "job": "job_aaaaaaaaaaaaaaaa"},
            now=at(6),
        )
    assert code_of(exc) == "evidence_unbound"
    step = durable.journal_record(
        ctx,
        BOARD,
        ROLE,
        epoch,
        token,
        "s1",
        "done",
        evidence={"type": "runner_job", "job": "job_aaaaaaaaaaaaaaaa"},
        now=at(6),
    )
    assert step["state"] == "done" and step["evidence"]["job"] == "job_aaaaaaaaaaaaaaaa"
    # A runner start is done only with its runner job.
    with pytest.raises(ToolError) as exc:
        durable.journal_record(
            ctx,
            BOARD,
            ROLE,
            epoch,
            token,
            "s2",
            "done",
            evidence={"type": "holder_attested", "note": "it started"},
            now=at(7),
        )
    assert code_of(exc) == "evidence_required"


@pytest.mark.parametrize(
    "field", ["board", "card", "step_key", "request_key", "not_started", "unknown"]
)
def test_r16b_runner_job_binding_checks_every_field(env, field):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    card, other_card = env.card(), env.card("Other")
    _start_step(env, epoch=epoch, token=token, step_key="s1", card=card, job=None)
    job = dict(job="job_cccccccccccccccc", card=card, role=ROLE, step_key="s1")
    if field == "board":
        job["board"] = "plain"
    elif field == "card":
        job["card"] = other_card
    elif field == "step_key":
        job["step_key"] = "s9"
        job["request_key"] = f"orch:{ROLE}:s1"
    elif field == "request_key":
        job["request_key"] = "orch:builder:s1"
    elif field == "not_started":
        job["state"] = "queued"
    if field != "unknown":
        _orch_job(env, **job)
    with pytest.raises(ToolError) as exc:
        durable.journal_record(
            ctx,
            BOARD,
            ROLE,
            epoch,
            token,
            "s1",
            "done",
            evidence={"type": "runner_job", "job": "job_cccccccccccccccc"},
            now=at(6),
        )
    assert code_of(exc) == ("evidence_mismatch" if field == "not_started" else "evidence_unbound")


def test_runner_start_steps_follow_their_job_on_resume(env):
    epoch, token = lease(env)
    card = env.card()
    _orch_job(
        env, job="job_dddddddddddddddd", card=card, role=ROLE, step_key="s1", state="uncertain"
    )
    _start_step(env, epoch=epoch, token=token, step_key="s1", card=card, job="job_dddddddddddddddd")
    cp = durable.checkpoint(env.ctx(env.a), BOARD, ROLE, now=at(2))
    assert [s["step_key"] for s in cp["in_doubt"]] == ["s1"]
    assert cp["jobs"][0]["id"] == "job_dddddddddddddddd"
    with env.runner_db() as conn:
        assert conn.execute("SELECT state FROM orch_steps").fetchone()[0] == "waiting"
        conn.execute("UPDATE runner_jobs SET state='started', started_at=?", (at(3),))
    claim = env.resume(env.b, now=at(10 + durable.TTL_SECONDS))
    assert [s["step_key"] for s in claim["checkpoint"]["finished"]] == ["s1"]
    with env.runner_db() as conn:
        assert conn.execute("SELECT state FROM orch_steps").fetchone()[0] == "done"
    assert durable.job_step_state("cancelled") == "failed"


def test_runner_start_fails_only_with_its_failed_job_as_evidence(env):
    """A holder cannot mark a runner start failed while its job may still run: failed
    needs runner_job evidence of a failed, cancelled or uncertain job."""
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    card = env.card()
    job = "job_eeeeeeeeeeeeeeee"
    _orch_job(env, job=job, card=card, role=ROLE, step_key="s1", state="queued")
    _start_step(env, epoch=epoch, token=token, step_key="s1", card=card, job=job)
    for evidence in (None, {"type": "holder_attested", "note": "gave up"}):
        with pytest.raises(ToolError) as exc:
            durable.journal_record(
                ctx, BOARD, ROLE, epoch, token, "s1", "failed", evidence=evidence, now=at(6)
            )
        assert code_of(exc) == "evidence_required"
    with pytest.raises(ToolError) as exc:
        durable.journal_record(
            ctx,
            BOARD,
            ROLE,
            epoch,
            token,
            "s1",
            "failed",
            evidence={"type": "runner_job", "job": job},
            now=at(7),
        )
    assert code_of(exc) == "evidence_mismatch"
    with env.runner_db() as conn:
        conn.execute("UPDATE runner_jobs SET state='cancelled'")
    step = durable.journal_record(
        ctx,
        BOARD,
        ROLE,
        epoch,
        token,
        "s1",
        "failed",
        evidence={"type": "runner_job", "job": job},
        now=at(8),
    )
    assert step["state"] == "failed" and step["evidence"]["job_state"] == "cancelled"


def test_runner_job_evidence_id_is_bounded(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    card = env.card()
    _start_step(env, epoch=epoch, token=token, step_key="s1", card=card, job=None)
    with pytest.raises(ToolError) as exc:
        durable.journal_record(
            ctx,
            BOARD,
            ROLE,
            epoch,
            token,
            "s1",
            "done",
            evidence={"type": "runner_job", "job": "j" * 65},  # MAX_JOB_ID is 64
            now=at(6),
        )
    assert code_of(exc) == "invalid_evidence" and exc.value.status == 400


# ---------------------------------------------------------------------- R19


def test_r19_resume_classifies_cards_read_only_and_heartbeats_own_held_only(env):
    a_board = BoardService(env.ctx(env.a))
    b_board = BoardService(env.ctx(env.b))
    held, expired, others, todo = (
        env.card("Held"),
        env.card("Expired"),
        env.card("Others"),
        env.card("Todo"),
    )
    a_board.task_take_next(board=BOARD, task=held)
    a_board.task_take_next(board=BOARD, task=expired)
    b_board.task_take_next(board=BOARD, task=others)
    with env.boards_db() as conn:
        conn.execute("UPDATE tasks SET claim_until='2001-01-01T00:00:00Z' WHERE id=?", (expired,))
        conn.execute("UPDATE tasks SET heartbeat_at=NULL WHERE id=?", (held,))
        before = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tasks")}
    claim = durable.resume(env.ctx(env.a), BOARD, ROLE)
    cards = claim["checkpoint"]["cards"]
    assert [c["card"] for c in cards["held"]] == [held]
    assert cards["held"][0]["heartbeat"] == "renewed"
    assert [c["card"] for c in cards["expired"]] == [expired]
    assert [c["card"] for c in cards["not_held_by_caller"]] == [others]
    with env.boards_db() as conn:
        after = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tasks")}
    assert after[held]["heartbeat_at"] is not None
    for number in (expired, others, todo):
        assert after[number]["assignee"] == before[number]["assignee"]
        assert after[number]["status"] == before[number]["status"]
        assert after[number]["heartbeat_at"] == before[number]["heartbeat_at"]
    assert after[others]["version"] == before[others]["version"]
    assert after[expired]["claim_until"] == before[expired]["claim_until"]
    # The read-only checkpoint never heartbeats.
    with env.boards_db() as conn:
        conn.execute("UPDATE tasks SET heartbeat_at=NULL WHERE id=?", (held,))
    cp = durable.checkpoint(env.ctx(env.a), BOARD, ROLE)
    assert [c["card"] for c in cp["cards"]["held"]] == [held]
    with env.boards_db() as conn:
        assert (
            conn.execute("SELECT heartbeat_at FROM tasks WHERE id=?", (held,)).fetchone()[0] is None
        )


def test_r19_checkpoint_needs_wing_read_access(env):
    outsider = env.hub.gateway.authenticate(
        "Bearer "
        + env.hub.token(
            "outsider",
            profile="full",
            scopes=["memory:read"],
            read_wings=["other"],
            trust="trusted",
        )
    )
    with pytest.raises(ToolError) as exc:
        durable.checkpoint(env.ctx(outsider), BOARD, ROLE, now=T0)
    assert code_of(exc) == "board_not_found"


# ------------------------------------------------- unstarted todo (A10/A12/A17/A18)


def _event(env, card, at_, *, kind="move", from_status="backlog", to_status="todo", choice=None):
    with env.boards_db() as conn:
        conn.execute(
            "INSERT INTO task_events(task_id,kind,from_status,to_status,choice,stamp_user,"
            "stamp_trust,created_at) VALUES (?,?,?,?,?,'user:example','trusted',?)",
            (card, kind, from_status, to_status, choice, at_),
        )


def _job(env, card, state, *, created, started=None, job=None):
    jobs.open_store(env.hub.gateway, env.ws)
    with env.runner_db() as conn:
        conn.execute(
            "INSERT INTO runner_jobs(id,task_number,board_slug,machine_id,project,topic,"
            "grant_version,runtime_mode,requested_by,state,started_at,created_at,updated_at) "
            "VALUES (?,?,?,?,'demo','worker',1,'approval-required','user:example',?,?,?,?)",
            (
                job or f"job_{card:016x}{state}",
                card,
                BOARD,
                MACHINE,
                state,
                started,
                created,
                created,
            ),
        )


def _unstarted(env):
    cp = durable.checkpoint(env.ctx(env.a), BOARD, ROLE, now=at(10_000))
    return {c["card"] for c in cp["unstarted"]}


def test_unstarted_lists_todo_cards_without_a_start(env):
    plain, active, cancelled, failed, started, backlog = (env.card(str(i)) for i in range(6))
    env.owner_board.task_update(backlog, status="backlog")
    _job(env, active, "queued", created=at(100))
    _job(env, cancelled, "cancelled", created=at(100))
    _job(env, failed, "failed", created=at(100))
    _job(env, started, "started", created=at(100), started=at(101))
    assert _unstarted(env) == {plain, cancelled, failed}


def test_unstarted_lists_a_card_whose_started_job_predates_its_return_to_todo(env):
    card = env.card()
    _job(env, card, "started", created=at(100), started=at(101))
    assert card not in _unstarted(env)
    _event(env, card, at(200), from_status="todo", to_status="doing")
    _event(env, card, at(300), from_status="doing", to_status="todo")
    assert card in _unstarted(env)


def test_unstarted_compares_launch_time_not_creation_time(env):
    # A18 N1: queued job, card leaves and re-enters todo, then the job launches.
    card = env.card()
    _event(env, card, at(50), from_status="backlog", to_status="todo")
    _event(env, card, at(200), from_status="todo", to_status="backlog")
    _event(env, card, at(300), from_status="backlog", to_status="todo")
    _job(env, card, "started", created=at(100), started=at(400))
    assert card not in _unstarted(env)


def test_unstarted_boundary_is_the_firing_event_not_any_todo_row(env):
    # A18 N2: a started job, then an edit on the todo card does not re-list it.
    card = env.card()
    _event(env, card, at(50), from_status="backlog", to_status="todo")
    _job(env, card, "started", created=at(100), started=at(101))
    _event(env, card, at(200), kind="edit", from_status="todo", to_status="todo")
    assert card not in _unstarted(env)
    # A plan approval is a firing event.
    _event(
        env, card, at(300), kind="edit", from_status="todo", to_status="todo", choice="approve plan"
    )
    assert card in _unstarted(env)


@pytest.mark.parametrize("event_first", [True, False], ids=["event-then-start", "start-then-event"])
def test_unstarted_lists_a_same_second_tie_in_either_order(env, event_first):
    # Both stores stamp whole seconds: a start and a firing event in one second cannot be
    # ordered, so the tie stays listed (conservative), whichever was written first.
    card = env.card()
    if event_first:
        _event(env, card, at(300), from_status="backlog", to_status="todo")
        _job(env, card, "started", created=at(250), started=at(300))
    else:
        _job(env, card, "started", created=at(250), started=at(300))
        _event(env, card, at(300), from_status="backlog", to_status="todo")
    assert card in _unstarted(env)


def test_unstarted_different_seconds_order_start_and_event(env):
    # A start one second after the firing event hides the card; one second before lists it.
    after, before = env.card(), env.card()
    _event(env, after, at(300), from_status="backlog", to_status="todo")
    _job(env, after, "started", created=at(250), started=at(301))
    _event(env, before, at(300), from_status="backlog", to_status="todo")
    _job(env, before, "started", created=at(250), started=at(299))
    listed = _unstarted(env)
    assert after not in listed
    assert before in listed


def test_unstarted_cancelled_plan_pending_job_never_hides(env):
    # A17: a cancelled plan_pending job after the todo entry keeps the card listed.
    card = env.card()
    _event(env, card, at(50), from_status="backlog", to_status="todo")
    _job(env, card, "cancelled", created=at(100))
    with env.runner_db() as conn:
        conn.execute(
            "UPDATE runner_jobs SET outcome_code='plan_pending', started_at=? ", (at(101),)
        )
    assert card in _unstarted(env)


def test_checkpoint_lists_misses_when_the_table_exists(env):
    lease(env)
    with env.runner_db() as conn:
        conn.execute(
            "CREATE TABLE orch_misses (board TEXT, event_id INTEGER, card INTEGER, code TEXT)"
        )
        conn.execute("INSERT INTO orch_misses VALUES (?,?,?,?)", (BOARD, 7, 1, "assigned"))
        conn.execute("INSERT INTO orch_misses VALUES (?,?,?,?)", ("plain", 8, 2, "busy"))
    cp = durable.checkpoint(env.ctx(env.a), BOARD, ROLE, now=at(1))
    assert cp["misses"] == [{"board": BOARD, "event_id": 7, "card": 1, "code": "assigned"}]
    assert cp["lease"]["epoch"] == 1 and cp["lease"]["live"] is True


def test_journal_is_append_only_and_step_identity_immutable(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    durable.journal_intent(ctx, BOARD, ROLE, epoch, token, "s1", "merge", now=at(1))
    with env.runner_db() as conn:
        for statement in (
            "UPDATE orch_journal SET event='x'",
            "DELETE FROM orch_journal",
            "UPDATE orch_steps SET card=5",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(statement)
        events = [r[0] for r in conn.execute("SELECT event FROM orch_journal ORDER BY id")]
    assert events == ["claim", "intent"]


def test_inputs_are_validated_before_any_write(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    calls = {
        "use_orch_card_start": lambda: durable.journal_intent(
            ctx, BOARD, ROLE, epoch, token, "s", "runner_start", now=at(1)
        ),
        "use_gate_open": lambda: durable.journal_intent(
            ctx, BOARD, ROLE, epoch, token, "s", "gate", now=at(1)
        ),
        "invalid_kind": lambda: durable.journal_intent(
            ctx, BOARD, ROLE, epoch, token, "s", "deploy", now=at(1)
        ),
        "invalid_card": lambda: durable.journal_intent(
            ctx, BOARD, ROLE, epoch, token, "s", "card", now=at(1)
        ),
        "invalid_detail": lambda: durable.journal_intent(
            ctx, BOARD, ROLE, epoch, token, "s", "next", detail="x" * 2001, now=at(1)
        ),
        "invalid_state": lambda: durable.journal_record(
            ctx, BOARD, ROLE, epoch, token, "s", "intent", now=at(1)
        ),
        "invalid_code": lambda: durable.journal_record(
            ctx, BOARD, ROLE, epoch, token, "s", "failed", code="Bad Code", now=at(1)
        ),
        "invalid_evidence": lambda: durable.journal_record(
            ctx,
            BOARD,
            ROLE,
            epoch,
            token,
            "s",
            "done",
            evidence={"type": "runner_job", "job": "j", "x": 1},
            now=at(1),
        ),
        "invalid_repo": lambda: durable.gate_open(
            ctx, BOARD, ROLE, epoch, token, "g", repo="no-slash", pr=1, head=HEAD, now=at(1)
        ),
        "invalid_pr": lambda: durable.gate_open(
            ctx, BOARD, ROLE, epoch, token, "g", repo=REPO, pr=0, head=HEAD, now=at(1)
        ),
        "invalid_head": lambda: durable.gate_open(
            ctx, BOARD, ROLE, epoch, token, "g", repo=REPO, pr=1, head="abc123", now=at(1)
        ),
        "invalid_verdict": lambda: durable.gate_record(
            ctx, BOARD, ROLE, epoch, token, "g", "maybe", now=at(1)
        ),
        "step_not_found": lambda: durable.journal_record(
            ctx, BOARD, ROLE, epoch, token, "nope", "done", now=at(1)
        ),
    }
    for code, call in calls.items():
        with pytest.raises(ToolError) as exc:
            call()
        assert code_of(exc) == code, code
    with env.runner_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orch_steps").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orch_gates").fetchone()[0] == 0


def test_retries_return_the_stored_result(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    durable.journal_intent(ctx, BOARD, ROLE, epoch, token, "s1", "merge", now=at(1))
    note = {"type": "holder_attested", "note": "merged at head"}
    first = durable.journal_record(
        ctx, BOARD, ROLE, epoch, token, "s1", "done", evidence=note, now=at(2)
    )
    again = durable.journal_record(
        ctx, BOARD, ROLE, epoch, token, "s1", "done", evidence=note, now=at(3)
    )
    assert again == first
    with pytest.raises(ToolError) as exc:
        durable.journal_record(ctx, BOARD, ROLE, epoch, token, "s1", "done", now=at(4))
    assert code_of(exc) == "transition"
    gate = dict(repo=REPO, pr=1, head=HEAD)
    durable.gate_open(ctx, BOARD, ROLE, epoch, token, "g1", now=at(5), **gate)
    first = durable.gate_record(ctx, BOARD, ROLE, epoch, token, "g1", "fail", now=at(6))
    assert (
        durable.gate_record(ctx, BOARD, ROLE, epoch, token, "g1", "gate_fail", now=at(7)) == first
    )


def test_connection_helpers_for_fenced_start(env):
    epoch, token = lease(env)
    card = env.card()
    st = durable._open(env.ctx(env.a))
    with st.transaction() as conn:
        assert durable.lease_in(conn, BOARD, ROLE)["epoch"] == epoch
        assert "token_hash" not in durable.lease_in(conn, BOARD, ROLE)
        assert durable.lease_in(conn, BOARD, OTHER_ROLE) is None
        durable.check_lease(conn, board=BOARD, role=ROLE, epoch=epoch, token=token, now=at(1))
        assert durable.renew_in(conn, board=BOARD, role=ROLE, epoch=epoch, now=at(100)) == at(
            100 + durable.TTL_SECONDS
        )
        assert durable.step_in(conn, board=BOARD, role=ROLE, step_key="s1") is None
        step = durable.insert_step_in(
            conn,
            board=BOARD,
            role=ROLE,
            step_key="s1",
            card=card,
            request_hash="h1",
            epoch=epoch,
            actor="conn:x",
            now=at(1),
        )
        assert (step["kind"], step["state"], step["job_id"]) == ("runner_start", "waiting", None)
        with pytest.raises(sqlite3.IntegrityError):
            durable.insert_step_in(
                conn,
                board=BOARD,
                role=ROLE,
                step_key="s1",
                card=card,
                request_hash="h2",
                epoch=epoch,
                actor="conn:x",
                now=at(1),
            )
        linked = durable.link_job_in(
            conn, board=BOARD, role=ROLE, step_key="s1", job_id="job_1", now=at(2)
        )
        assert linked["job_id"] == "job_1"
        with pytest.raises(ToolError) as exc:
            durable.link_job_in(
                conn, board=BOARD, role=ROLE, step_key="s1", job_id="job_2", now=at(2)
            )
        assert code_of(exc) == "step_exists"
        assert durable.drop_leases_in(conn, now=at(3)) == 1
        with pytest.raises(ToolError) as exc:
            durable.check_lease(conn, board=BOARD, role=ROLE, epoch=epoch, token=token, now=at(4))
        assert code_of(exc) == "fenced"
