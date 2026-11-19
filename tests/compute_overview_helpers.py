"""Shared fixtures for the Compute overview suites: a synthetic fleet and a fake lifecycle executor.

Nothing here reaches a machine. The executor drives ``workspace_lifecycle.claim`` and ``report``
the way a real machine would, so every state-machine path is exercised without a container.
"""

import json
from datetime import datetime, timedelta, timezone

from commonplace import control_db
from commonplace.compute import workspace_lifecycle as lifecycle
from commonplace.runner import jobs, registry

GRANT = {
    "label": "build-runner",
    "projects": ["demo"],
    "topics": ["worker"],
    "wings": ["project_compute"],
}
ADDRESS = "192.0.2.10"


def workspace_body(**changes):
    return {
        "name": "Build box",
        "image_source": "custom",
        "image": "ghcr.io/acme/tools:1.2",
        **changes,
    }


class FakeExecutor:
    """What a machine does: claim the next request, then report phases for it."""

    def __init__(self, env, *, register=True):
        self.env = env
        self.name = "fake-executor"
        if register:
            env.gateway._compute_workspace_executor = self

    def _now(self):
        return self.env.store._now()

    def claim(self):
        with self.env.store._journal() as conn:
            return lifecycle.claim(conn, self.name, self._now())

    def report(self, claim, phase, **kwargs):
        with self.env.store._journal() as conn:
            lifecycle.report(
                conn, claim["workspace_id"], claim["claim_token"], phase, self._now(), **kwargs
            )

    def run(self, claim, *phases, revision=None):
        """Report each phase in order; ``running`` carries the revision the claim saw."""
        for phase in phases:
            extra = (
                {"applied_revision": revision or claim["revision"]} if phase == "running" else {}
            )
            if phase == "failed":
                extra = {"reason": "start_failed"}
            self.report(claim, phase, **extra)


def iso(epoch):
    return control_db._iso(datetime.fromtimestamp(epoch, timezone.utc))


class Fleet:
    """Runner machines of the compute fixture: enroll, make them seen, run jobs, report resources."""

    def __init__(self, env):
        self.env = env
        registry.ensure(env.db)

    def enroll(self, label="build-runner", attest=True, **grant):
        made = registry.enroll(self.env.db, self.env.principal, {**GRANT, "label": label, **grant})
        if attest:
            row = registry.get_machine(self.env.db, self.env.workspace, made["machine_id"])
            registry.attest(
                self.env.db,
                self.env.principal,
                made["machine_id"],
                {
                    "topics": json.loads(row["topics"]),
                    "provider_instance_id": "worker",
                    "worker_connection_id": made["worker_connection_id"],
                },
            )
        return made

    def seen(self, made, seconds_ago, *, worker=False):
        """Say the machine's own credential was last used ``seconds_ago`` seconds before the fixture clock."""
        at = iso(self.env.clock.now - seconds_ago)
        key = "worker_connection_id" if worker else "machine_connection_id"
        with self.env.db._lock:
            self.env.db._conn.execute(
                "UPDATE connections SET last_used_at=? WHERE id=?", (at, made[key])
            )

    def store(self):
        return jobs.open_store(self.env.gateway, self.env.workspace)

    def job(self, made, state="started", *, minutes_ago=1, project="demo", task=1, **columns):
        """One run row for the machine, ``minutes_ago`` old."""
        at = iso(self.env.clock.now - minutes_ago * 60)
        values = {
            "id": f"job_{task}_{state}",
            "task_number": task,
            "board_slug": "compute",
            "machine_id": made["machine_id"],
            "project": project,
            "topic": "worker",
            "grant_version": 1,
            "runtime_mode": "approval-required",
            "requested_by": "user:synthetic",
            "state": state,
            "created_at": at,
            "updated_at": at,
            "starting_at": at if state != "queued" else None,
            "started_at": at if state == "started" else None,
            **columns,
        }
        with self.store().transaction() as conn:
            conn.execute(
                "INSERT INTO runner_jobs ("
                + ",".join(values)
                + ") VALUES ("
                + ",".join("?" for _ in values)
                + ")",
                tuple(values.values()),
            )
        return values["id"]

    def resources(self, made, *, seconds_ago=30, memory_mb=6144, cpu_free=3.5):
        from commonplace.runner import capacity, headroom  # noqa: F401

        with self.store().transaction() as conn:
            conn.execute(capacity.SCHEMA)
            conn.execute(
                "INSERT OR REPLACE INTO runner_resources VALUES (?,?,?)",
                (
                    made["machine_id"],
                    iso(self.env.clock.now - seconds_ago),
                    json.dumps(
                        {
                            "memory_available_mb": memory_mb,
                            "cpu_free": cpu_free,
                            "memory_floor_mb": 1024,
                        }
                    ),
                ),
            )

    def spend(
        self,
        made,
        *,
        micros=1_500_000,
        currency="USD",
        job="spend_1",
        minutes_ago=60,
        unknown=False,
    ):
        """One finished run in the ledger with a cost event (or an unknown cost)."""
        from commonplace.runner import costs

        at = iso(self.env.clock.now - minutes_ago * 60)
        with self.store().transaction() as conn:
            conn.execute(
                "INSERT INTO run_ledger(job_id,task_number,board_slug,project,topic,machine_id,requested_by,"
                "status,started_at,ended_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job,
                    9,
                    "compute",
                    "demo",
                    "worker",
                    made["machine_id"],
                    "user:synthetic",
                    "completed",
                    at,
                    at,
                    at,
                    at,
                ),
            )
            costs.record(
                conn,
                job,
                now=at,
                **({"unknown": True} if unknown else {"cost_micros": micros, "currency": currency}),
            )


def revoke_machine(env, made):
    registry.revoke(env.db, env.principal, made["machine_id"])


def after(env, seconds):
    env.clock.now += seconds


def parse_time(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


__all__ = [
    "ADDRESS",
    "FakeExecutor",
    "Fleet",
    "after",
    "iso",
    "revoke_machine",
    "timedelta",
    "workspace_body",
]
