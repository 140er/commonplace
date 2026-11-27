"""Benchmark-gated promotion of an experiment config (slice 1: the comparison core).

A benchmark compares one accepted ``exp:<name>`` candidate with the live version over the
same replayed tasks (outcome, cost, tokens, latency per task). Nothing here replays or
calls a model: callers record results they obtained elsewhere.

``decide`` is deterministic: promote only if the candidate's success rate is at least
live's, no task that passed on live fails on the candidate, and at least one metric is
strictly better (success rate up, or total cost, tokens or latency down by more than the
configured margin). ``Benchmarks.promote`` re-runs it on the stored results, re-checks the
protected keys, and writes the candidate body as a new live version (the owner's accept,
with the numbers in hand); ``rollback`` writes the previous live body back as another new
version, so history stays append-only and the restored sha256 is the original's.

``protected_violations`` refuses any candidate that touches approval-gate, safety or
evaluation-rule keys, independently of the experiment allow-list in ``schema``.
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from . import schema
from .store import (
    LIVE,
    AcceptResult,
    OrchError,
    Store,
    _checked_config,
    accepted_in,
    require_owner,
)

__all__ = [
    "Benchmarks",
    "BenchmarkRecord",
    "Decision",
    "Promotion",
    "PromotionResult",
    "TaskResult",
    "Thresholds",
    "decide",
    "protected_violations",
]

METRICS = ("cost_micro_usd", "tokens", "latency_ms")
TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#-]{0,119}")
# Config tables an experiment never touches: gates, limits, runtime mode, merge policy.
PROTECTED_TOP_LEVEL = frozenset(
    {"schema", "project", "limits", "runner", "triggers", "fleet", "factory", "mcps"}
)
PROTECTED_ROLE_FIELDS = frozenset({"kind", "area", "parent", "harness", "mcps", "connections"})
# A workflow step that names one of these is a gate, review or safety step.
GUARD_STEP = re.compile(r"gate|review|approv|verif|safety", re.IGNORECASE)


# ----------------------------------------------------------------- decision


@dataclass(frozen=True)
class TaskResult:
    """One replayed task. Unreported tokens/currency remain unknown (None)."""

    task: str
    passed: bool
    cost_micro_usd: int | None
    tokens: int | None
    latency_ms: int

    def __post_init__(self):
        if not isinstance(self.task, str) or not TASK.fullmatch(self.task):
            raise ValueError("task must be a short id")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a bool")
        for name in METRICS:
            value = getattr(self, name)
            if value is None and name != "latency_ms":
                continue
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class Thresholds:
    """Decision thresholds. They can only tighten the rule: ``min_tasks`` >= 1,
    ``min_metric_gain`` is the relative drop a cost, token or latency total needs to count
    as better (0 = any strict drop), ``budget_micro_usd`` caps the candidate's total cost."""

    min_tasks: int = 5
    min_metric_gain: float = 0.0
    budget_micro_usd: int | None = None

    def __post_init__(self):
        if type(self.min_tasks) is not int or self.min_tasks < 1:
            raise ValueError("min_tasks must be an integer >= 1")
        gain = self.min_metric_gain
        if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not 0 <= gain < 1:
            raise ValueError("min_metric_gain must be at least 0 and below 1")
        budget = self.budget_micro_usd
        if budget is not None and (type(budget) is not int or budget < 0):
            raise ValueError("budget_micro_usd must be a non-negative integer or null")

    @classmethod
    def from_mapping(cls, data: Mapping | None) -> Thresholds:
        data = dict(data or {})
        unknown = sorted(set(data) - {"min_tasks", "min_metric_gain", "budget_micro_usd"})
        if unknown:
            raise ValueError(f"unknown threshold {unknown[0]}")
        return cls(**data)

    def to_dict(self) -> dict:
        return {
            "min_tasks": self.min_tasks,
            "min_metric_gain": self.min_metric_gain,
            "budget_micro_usd": self.budget_micro_usd,
        }


@dataclass(frozen=True)
class Decision:
    promote: bool
    reasons: tuple[str, ...] = ()
    summary: dict = field(default_factory=dict)


def _by_task(rows: Iterable[TaskResult]) -> dict[str, TaskResult]:
    out: dict[str, TaskResult] = {}
    for row in rows:
        if row.task in out:
            raise ValueError(f"duplicate task {row.task}")
        out[row.task] = row
    return out


def _side(rows: Iterable[TaskResult]) -> dict:
    rows = list(rows)
    return {"passed": sum(r.passed for r in rows)} | {
        name: (
            sum(getattr(r, name) for r in rows)
            if all(getattr(r, name) is not None for r in rows)
            else None
        )
        for name in METRICS
    }


def decide(
    live: Sequence[TaskResult],
    candidate: Sequence[TaskResult],
    thresholds: Thresholds | None = None,
) -> Decision:
    """Pure, order-independent verdict; ``reasons`` is empty exactly when it promotes."""
    thresholds = thresholds or Thresholds()
    live_by, cand_by = _by_task(live), _by_task(candidate)
    if set(live_by) != set(cand_by):
        return Decision(False, ("task_mismatch",))
    tasks = sorted(live_by)
    summary = {"tasks": len(tasks), "live": _side(live_by.values())}
    summary["candidate"] = _side(cand_by.values())
    reasons: list[str] = []
    if len(tasks) < thresholds.min_tasks:
        reasons.append("too_few_tasks")
    lv, cd = summary["live"], summary["candidate"]
    if cd["passed"] < lv["passed"]:
        reasons.append("success_rate_lower")
    regressions = [t for t in tasks if live_by[t].passed and not cand_by[t].passed]
    reasons += [f"regression:{t}" for t in regressions]
    budget = thresholds.budget_micro_usd
    if budget is not None:
        if cd["cost_micro_usd"] is None:
            reasons.append("cost_unknown")
        elif cd["cost_micro_usd"] > budget:
            reasons.append("over_budget")
    better = ["success_rate"] if cd["passed"] > lv["passed"] else []
    keep = 1 - Fraction(str(thresholds.min_metric_gain))
    better += [
        m for m in METRICS if cd[m] is not None and lv[m] is not None and cd[m] < lv[m] * keep
    ]
    summary["better"] = sorted(better)
    if not better and not regressions and cd["passed"] >= lv["passed"]:
        reasons.append("no_strict_gain")
    return Decision(not reasons, tuple(reasons), summary)


# ---------------------------------------------------------- protected keys


def _guard_steps(config: schema.Config, workflow: str | None) -> set[str]:
    wf = config.workflows.get(workflow) if workflow else None
    return {s for s in (wf.steps if wf else ()) if GUARD_STEP.search(s)}


def protected_violations(live: schema.Config, candidate: schema.Config) -> list[str]:
    """Dotted paths where ``candidate`` differs from ``live`` in an approval-gate, safety or
    evaluation-rule key; empty when it touches none.

    Protected: every table other than roles and workflows (limits, runtime mode, triggers,
    fleet and factory rules, mcps), a role's structure (kind, area, parent, harness, mcps,
    connections, existence), every field of a reviewer or gate role, and any workflow step
    that names a gate, review, approval or verification step (it may not be dropped, nor a
    role pointed at a workflow that lacks it)."""
    found: list[str] = []
    for change in schema.diff(live, candidate):
        parts = change.path.split(".")
        if parts[0] in PROTECTED_TOP_LEVEL:
            found.append(change.path)
        elif parts[0] == "roles":
            role = live.roles.get(parts[1]) if len(parts) > 1 else None
            if (
                len(parts) != 3
                or parts[2] in PROTECTED_ROLE_FIELDS
                or parts[1] == "gate"
                or (role is not None and role.kind == "reviewer")
            ):
                found.append(change.path)
    for name in live.workflows:
        steps = candidate.workflows.get(name)
        if not _guard_steps(live, name) <= set(steps.steps if steps else ()):
            found.append(f"workflows.{name}.steps")
    for name, role in candidate.roles.items():
        before = live.roles.get(name)
        if (
            before is not None
            and role.workflow != before.workflow
            and not _guard_steps(live, before.workflow) <= _guard_steps(candidate, role.workflow)
        ):
            found.append(f"roles.{name}.workflow")
    return sorted(dict.fromkeys(found))


# ------------------------------------------------------------------ records


@dataclass(frozen=True)
class BenchmarkRecord:
    id: int
    board: str
    channel: str
    candidate_version: int
    candidate_sha256: str
    live_version: int
    live_sha256: str
    thresholds: Thresholds
    promote: bool
    reasons: tuple[str, ...]
    summary: dict
    recorded_by: str
    created_at: str


@dataclass(frozen=True)
class Promotion:
    id: int
    board: str
    kind: str  # "promote" | "rollback"
    benchmark_id: int | None
    of_promotion: int | None
    previous_version: int  # the live version this row superseded
    new_version: int  # the live version it created
    decided_by: str
    created_at: str


@dataclass(frozen=True)
class PromotionResult:
    promotion: Promotion
    accept: AcceptResult


def _record(row) -> BenchmarkRecord:
    return BenchmarkRecord(
        id=row["id"],
        board=row["board"],
        channel=row["channel"],
        candidate_version=row["candidate_version"],
        candidate_sha256=row["candidate_sha256"],
        live_version=row["live_version"],
        live_sha256=row["live_sha256"],
        thresholds=Thresholds.from_mapping(json.loads(row["thresholds"])),
        promote=bool(row["promote"]),
        reasons=tuple(json.loads(row["reasons"])),
        summary=json.loads(row["summary"]),
        recorded_by=row["recorded_by"],
        created_at=row["created_at"],
    )


def _promotion(row) -> Promotion:
    return Promotion(**{name: row[name] for name in Promotion.__dataclass_fields__})


def _results_in(conn, benchmark_id: int, side: str) -> tuple[TaskResult, ...]:
    rows = conn.execute(
        "SELECT task, passed, cost_micro_usd, tokens, latency_ms FROM orch_benchmark_results "
        "WHERE benchmark_id=? AND side=? ORDER BY task",
        (benchmark_id, side),
    ).fetchall()
    return tuple(TaskResult(r[0], bool(r[1]), r[2], r[3], r[4]) for r in rows)


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _protected(live: schema.Config, candidate: schema.Config) -> None:
    paths = protected_violations(live, candidate)
    if paths:
        raise OrchError(
            "protected_key",
            f"the candidate changes protected key {paths[0]} (gates, safety and "
            "evaluation rules are never changed by an experiment)",
            path=paths[0],
            status=409,
        )


class Benchmarks:
    """Benchmark records, promotion and rollback over one orchestrator ``Store``."""

    def __init__(self, store: Store):
        self.store = store
        # Older stores required invented zeroes for unknown usage. Copy every row in
        # one transaction before replacing that constraint; no history is discarded.
        with store.connection() as conn:
            columns = conn.execute("PRAGMA table_info(orch_benchmark_results)").fetchall()
        if not any(c["name"] in ("tokens", "cost_micro_usd") and c["notnull"] for c in columns):
            return
        with store.transaction() as conn:
            columns = conn.execute("PRAGMA table_info(orch_benchmark_results)").fetchall()
            if any(c["name"] in ("tokens", "cost_micro_usd") and c["notnull"] for c in columns):
                conn.execute(
                    "CREATE TABLE orch_benchmark_results_nullable ("
                    "benchmark_id INTEGER NOT NULL, "
                    "side TEXT NOT NULL CHECK(side IN ('live','candidate')), "
                    "task TEXT NOT NULL, passed INTEGER NOT NULL CHECK(passed IN (0,1)), "
                    "cost_micro_usd INTEGER CHECK(cost_micro_usd>=0), "
                    "tokens INTEGER CHECK(tokens>=0), "
                    "latency_ms INTEGER NOT NULL CHECK(latency_ms>=0), "
                    "PRIMARY KEY(benchmark_id,side,task))"
                )
                conn.execute(
                    "INSERT INTO orch_benchmark_results_nullable SELECT * FROM orch_benchmark_results"
                )
                conn.execute("DROP TABLE orch_benchmark_results")
                conn.execute(
                    "ALTER TABLE orch_benchmark_results_nullable RENAME TO orch_benchmark_results"
                )

    def record(
        self,
        principal,
        board: str,
        channel: str,
        *,
        live_results: Iterable[TaskResult],
        candidate_results: Iterable[TaskResult],
        thresholds: Thresholds | None = None,
        expected_versions: tuple[int, int] | None = None,
    ) -> BenchmarkRecord:
        """Store the replay results of the accepted ``channel`` candidate against the
        accepted live version, with the decision ``decide`` makes. Caller has checked wing
        write access. Refusals: ``invalid_channel`` (live), ``no_live``,
        ``unknown_experiment``, ``protected_key``, ``invalid_results``."""
        store = self.store
        thresholds = thresholds or Thresholds()
        if not isinstance(channel, str) or channel == LIVE:
            raise OrchError("invalid_channel", "a benchmark needs an exp:<name> channel")
        live_results, candidate_results = list(live_results), list(candidate_results)
        try:
            decision = decide(live_results, candidate_results, thresholds)
        except ValueError as exc:
            raise OrchError("invalid_results", str(exc), path="results") from None
        with store.transaction() as conn:
            live, cand = self._current(conn, board, channel)
            if expected_versions is not None and (live.version, cand.version) != expected_versions:
                raise OrchError("stale_benchmark", "live or the candidate changed during replay")
            _protected(_checked_config(live), _checked_config(cand))
            cursor = conn.execute(
                "INSERT INTO orch_benchmarks(board, channel, candidate_version, "
                "candidate_sha256, live_version, live_sha256, thresholds, promote, reasons, "
                "summary, recorded_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    board,
                    channel,
                    cand.version,
                    cand.sha256,
                    live.version,
                    live.sha256,
                    _json(thresholds.to_dict()),
                    int(decision.promote),
                    _json(list(decision.reasons)),
                    _json(decision.summary),
                    principal.actor,
                    store._now(),
                ),
            )
            benchmark_id = cursor.lastrowid
            for side, rows in (("live", live_results), ("candidate", candidate_results)):
                conn.executemany(
                    "INSERT INTO orch_benchmark_results(benchmark_id, side, task, passed, "
                    "cost_micro_usd, tokens, latency_ms) VALUES (?,?,?,?,?,?,?)",
                    [
                        (benchmark_id, side, r.task, int(r.passed), r.cost_micro_usd, r.tokens)
                        + (r.latency_ms,)
                        for r in rows
                    ],
                )
            return self._get_in(conn, benchmark_id)

    def get(self, benchmark_id: int) -> BenchmarkRecord:
        with self.store.connection() as conn:
            return self._get_in(conn, benchmark_id)

    def results(self, benchmark_id: int, side: str) -> tuple[TaskResult, ...]:
        with self.store.connection() as conn:
            return _results_in(conn, benchmark_id, side)

    def promotions(self, board: str) -> list[Promotion]:
        """Newest first."""
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM orch_promotions WHERE board=? ORDER BY id DESC", (board,)
            ).fetchall()
        return [_promotion(row) for row in rows]

    def promote(
        self, principal, board: str, benchmark_id: int, *, sha256: str, connections: Iterable[str]
    ) -> PromotionResult:
        """Owner promotes the candidate of a winning benchmark to live; ``sha256`` is the
        candidate text the owner approved. The decision is recomputed from the stored
        results. Refusals: ``owner_only``, ``not_found``, ``not_a_win``, ``sha256_mismatch``,
        ``stale_benchmark`` (live or the candidate changed since), ``protected_key``."""
        require_owner(principal)
        store = self.store
        if not isinstance(sha256, str) or not schema.SHA256_HEX.fullmatch(sha256):
            raise OrchError("sha256_invalid", "must be 64 lowercase hex characters", path="sha256")
        connections = list(connections)
        with store.transaction() as conn:
            entry = self._get_in(conn, benchmark_id)
            if entry.board != board:
                raise OrchError("not_found", "no such benchmark", path="benchmark")
            verdict = decide(
                _results_in(conn, entry.id, "live"),
                _results_in(conn, entry.id, "candidate"),
                entry.thresholds,
            )
            if not verdict.promote:
                raise OrchError("not_a_win", "the stored results do not justify a promotion")
            live, cand = self._current(conn, board, entry.channel)
            if not hmac.compare_digest(cand.sha256, sha256):
                raise OrchError(
                    "sha256_mismatch",
                    "the candidate does not match the approved sha256",
                    path="sha256",
                )
            if (live.version, live.sha256, cand.version, cand.sha256) != (
                entry.live_version,
                entry.live_sha256,
                entry.candidate_version,
                entry.candidate_sha256,
            ):
                raise OrchError(
                    "stale_benchmark", "live or the candidate changed after this benchmark"
                )
            candidate_config = _checked_config(cand)
            _protected(_checked_config(live), candidate_config)
            accepted = self._write_live(
                conn,
                principal,
                board,
                cand.body,
                candidate_config,
                f"bench:{entry.id}",
                connections,
            )
            conn.execute(
                "UPDATE orch_configs SET status='superseded', decided_by=?, decided_at=? "
                "WHERE board=? AND version=? AND status='accepted'",
                (principal.actor, store._now(), board, cand.version),
            )
            promotion = self._log(
                conn, principal, board, "promote", entry.id, None, live.version, accepted
            )
            return PromotionResult(promotion, accepted)

    def rollback(self, principal, board: str, *, connections: Iterable[str]) -> PromotionResult:
        """Owner restores the live version the latest promotion replaced, as a new live
        version with the identical body. Refusals: ``owner_only``,
        ``nothing_to_roll_back``, ``stale_live`` (live is not the promoted version)."""
        require_owner(principal)
        store = self.store
        connections = list(connections)
        with store.transaction() as conn:
            row = conn.execute(
                "SELECT p.*, EXISTS(SELECT 1 FROM orch_promotions r WHERE r.board=p.board "
                "AND r.kind='rollback' AND r.of_promotion=p.id) AS undone "
                "FROM orch_promotions p WHERE p.board=? AND p.kind='promote' "
                "ORDER BY p.id DESC LIMIT 1",
                (board,),
            ).fetchone()
            if row is None or row["undone"]:
                raise OrchError("nothing_to_roll_back", "the latest promotion is already undone")
            promoted = _promotion(row)
            live = accepted_in(conn, board, LIVE)
            if live is None or live.version != promoted.new_version:
                raise OrchError("stale_live", "live changed after that promotion")
            previous = store._get_in(conn, board, promoted.previous_version)
            accepted = self._write_live(
                conn,
                principal,
                board,
                previous.body,
                _checked_config(previous),
                f"rollback:{promoted.id}",
                connections,
            )
            promotion = self._log(
                conn, principal, board, "rollback", None, promoted.id, live.version, accepted
            )
            return PromotionResult(promotion, accepted)

    # internals
    def _current(self, conn, board: str, channel: str):
        live = accepted_in(conn, board, LIVE)
        if live is None:
            raise OrchError("no_live", "a benchmark needs an accepted live config")
        cand = accepted_in(conn, board, channel)
        if cand is None:
            raise OrchError("unknown_experiment", "no accepted config on that experiment channel")
        return live, cand

    def _get_in(self, conn, benchmark_id) -> BenchmarkRecord:
        row = (
            conn.execute("SELECT * FROM orch_benchmarks WHERE id=?", (benchmark_id,)).fetchone()
            if type(benchmark_id) is int
            else None
        )
        if row is None:
            raise OrchError("not_found", "no such benchmark", path="benchmark")
        return _record(row)

    def _write_live(self, conn, principal, board, body, config, source_ref, connections):
        store = self.store
        proposed = store._insert(conn, principal, board, LIVE, body, config, source_ref)
        return store._accept_in(conn, principal, proposed, connections)

    def _log(self, conn, principal, board, kind, benchmark_id, of_promotion, previous, accepted):
        cursor = conn.execute(
            "INSERT INTO orch_promotions(board, kind, benchmark_id, of_promotion, "
            "previous_version, new_version, decided_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                board,
                kind,
                benchmark_id,
                of_promotion,
                previous,
                accepted.version.version,
                principal.actor,
                self.store._now(),
            ),
        )
        return _promotion(
            conn.execute("SELECT * FROM orch_promotions WHERE id=?", (cursor.lastrowid,)).fetchone()
        )
