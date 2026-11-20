"""Collection boundaries and current authority, using disposable synthetic data."""

import collections
import json
import sqlite3

import pytest
from activity_fixtures import AT, END, START, source
from test_compute_activity_api import activity_hub, enrolled, request  # noqa: F401

from commonplace.compute import activity_math as timing
from commonplace.compute.activity_collector import Collector, Limits, collect

PREFIX = "orchestration_v2_projection_"


def test_current_usage_and_context_survive_historical_turn_prefix(tmp_path):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        db.execute(f"UPDATE {PREFIX}provider_turns SET ordinal=ordinal+10")
        for i in range(3):
            db.execute(
                f"INSERT INTO {PREFIX}provider_turns VALUES (?,?,?,?,?,?,?)",
                (
                    f"old-{i}",
                    "main",
                    i,
                    timing.iso(START - 100),
                    timing.iso(START - 90),
                    "ar1",
                    "{}",
                ),
            )
    snapshot = collect(path, START, END, limits=Limits(turns=2))
    assert snapshot["metrics"]["tokens_input"]["value"] == 100
    assert snapshot["metrics"]["tokens_cached"]["value"] == 80
    assert snapshot["metrics"]["tokens_output"]["value"] == 20
    assert snapshot["metrics"]["turns_with_context"]["value"] == 2
    assert snapshot["coverage"]["turns_seen"] == 2
    assert not snapshot["coverage"]["partial"]


def test_auxiliary_scans_and_retained_samples_obey_the_budget(tmp_path, monkeypatch):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        for i in range(40):
            db.execute(
                f"INSERT INTO {PREFIX}runtime_requests VALUES (?,?,?,?,?,?)",
                (
                    "main",
                    "user_input",
                    "resolved",
                    timing.iso(AT + 5),
                    timing.iso(AT + 6),
                    '{"answers":{}}',
                ),
            )
            db.execute(
                f"INSERT INTO {PREFIX}run_attempts VALUES (?,?,?,?)",
                (
                    f"retry-{i}",
                    "r1",
                    "completed",
                    json.dumps(
                        {
                            "reason": "retry",
                            "startedAt": timing.iso(AT + 5),
                            "completedAt": timing.iso(AT + 6),
                        }
                    ),
                ),
            )
            db.execute(
                f"INSERT INTO {PREFIX}context_transfers VALUES (?,?,?,?,?,?,?)",
                (
                    "main",
                    "child",
                    "r3",
                    "provider_handoff",
                    timing.iso(AT + 10),
                    "resolved_native",
                    '{"sourcePoint":{"runId":"r1"}}',
                ),
            )
    counts: collections.Counter[str] = collections.Counter()
    original_rows, original_finish = Collector.rows, Collector.finish
    retained = {}

    def rows(self, sql, args=()):
        table = next(
            (
                name
                for name in ("runtime_requests", "run_attempts", "context_transfers")
                if PREFIX + name in sql and "provider_turns" not in sql
            ),
            None,
        )
        for row in original_rows(self, sql, args):
            if table:
                counts[table] += 1
            yield row

    def finish(self):
        retained["wait_intervals"] = sum(len(agent["wait"]) for agent in self.agents.values())
        retained["wait_samples"] = len(self.sinks.get("wait", {}).get("samples", []))
        retained["retry_samples"] = len(self.sinks.get("recorded_retry", {}).get("samples", []))
        return original_finish(self)

    monkeypatch.setattr(Collector, "rows", rows)
    monkeypatch.setattr(Collector, "finish", finish)
    limits = Limits(metadata=4, runs=10, items=10, turns=10, log_lines=10)
    snapshot = collect(path, START, END, limits=limits)
    assert max(counts.values()) <= limits.metadata
    assert max(retained.values()) <= limits.metadata
    assert snapshot["coverage"]["partial"]
    for name in ("asks", "retries", "transfers"):
        assert snapshot["coverage"]["scans"][name] == {"scanned": 4, "limit": 4, "truncated": True}


def test_pr_link_overflow_cannot_be_a_complete_merged_zero(tmp_path):
    path = source(tmp_path / "source.sqlite")
    links = [
        {
            "host": "github.example.test",
            "repository": "sample/project",
            "number": i + 1,
            "snapshot": {"state": "open" if i < 512 else "merged", "syncedAt": timing.iso(AT + 50)},
        }
        for i in range(513)
    ]
    with sqlite3.connect(path) as db:
        db.execute(
            f"UPDATE {PREFIX}threads SET payload_json=json_set(payload_json,'$.pullRequests',json(?)) WHERE thread_id='main'",
            (json.dumps(links),),
        )
        db.execute(
            f"UPDATE {PREFIX}threads SET payload_json=json_set(payload_json,'$.pullRequests',json('[]')) WHERE thread_id='child'"
        )
    snapshot = collect(path, START, END)
    assert snapshot["coverage"]["partial"]
    assert snapshot["metrics"]["prs_merged"] == {"value": None, "reason": "partial_scan"}
    assert snapshot["coverage"]["scans"]["pr_links"]["truncated"]


def test_auxiliary_budgets_ignore_old_foreign_and_ineligible_rows(tmp_path, monkeypatch):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        for i in range(40):
            for tid, at, kind in (
                ("main", START - 100, "user_input"),
                ("foreign", AT, "user_input"),
                ("main", AT, "permission"),
            ):
                db.execute(
                    f"INSERT INTO {PREFIX}runtime_requests VALUES (?,?,?,?,?,?)",
                    (tid, kind, "resolved", timing.iso(at), timing.iso(at + 1), '{"answers":{}}'),
                )
            for label, rid, at, reason in (
                ("old", "r1", START - 100, "retry"),
                ("foreign", "foreign", AT, "retry"),
                ("initial", "r1", AT, "initial"),
            ):
                db.execute(
                    f"INSERT INTO {PREFIX}run_attempts VALUES (?,?,?,?)",
                    (
                        f"{label}-{i}",
                        rid,
                        "completed",
                        json.dumps(
                            {
                                "reason": reason,
                                "startedAt": timing.iso(at),
                                "completedAt": timing.iso(at + 1),
                            }
                        ),
                    ),
                )
            for tid, at, status in (
                ("main", START - 100, "resolved_native"),
                ("foreign", AT, "resolved_native"),
                ("main", AT, "failed"),
            ):
                db.execute(
                    f"INSERT INTO {PREFIX}context_transfers VALUES (?,?,?,?,?,?,?)",
                    (
                        tid,
                        "foreign",
                        "r3",
                        "provider_handoff",
                        timing.iso(at),
                        status,
                        '{"sourcePoint":{"runId":"r1"}}',
                    ),
                )
        db.execute(
            f"INSERT INTO {PREFIX}runtime_requests VALUES (?,?,?,?,?,?)",
            (
                "main",
                "user_input",
                "resolved",
                timing.iso(AT + 5),
                timing.iso(AT + 6),
                '{"answers":{}}',
            ),
        )
        db.execute(
            f"INSERT INTO {PREFIX}run_attempts VALUES (?,?,?,?)",
            (
                "current",
                "r1",
                "completed",
                json.dumps(
                    {
                        "reason": "retry",
                        "startedAt": timing.iso(AT + 5),
                        "completedAt": timing.iso(AT + 6),
                    }
                ),
            ),
        )
        db.execute(
            f"INSERT INTO {PREFIX}context_transfers VALUES (?,?,?,?,?,?,?)",
            (
                "main",
                "child",
                "r3",
                "provider_handoff",
                timing.iso(AT + 10),
                "resolved_native",
                '{"sourcePoint":{"runId":"r1"}}',
            ),
        )
    counts: collections.Counter[str] = collections.Counter()
    original = Collector.rows

    def rows(self, sql, args=()):
        table = next(
            (
                name
                for name in ("runtime_requests", "run_attempts", "context_transfers")
                if PREFIX + name in sql and "provider_turns" not in sql
            ),
            None,
        )
        for row in original(self, sql, args):
            if table:
                counts[table] += 1
            yield row

    monkeypatch.setattr(Collector, "rows", rows)
    snapshot = collect(
        path, START, END, limits=Limits(metadata=4, runs=10, items=10, turns=10, log_lines=10)
    )
    assert counts == {"runtime_requests": 3, "run_attempts": 1, "context_transfers": 1}
    assert snapshot["metrics"]["asks"]["value"] == 3
    assert snapshot["metrics"]["retries"]["value"] == 1
    assert snapshot["metrics"]["handoffs"]["value"] == 1
    assert not snapshot["coverage"]["partial"]


def test_metadata_budget_follows_window_and_still_counts_future_starts_requested_now(tmp_path):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        for i in range(50):
            db.execute(
                f"INSERT INTO {PREFIX}runs VALUES (?,?,?,?,?,?,?)",
                (
                    f"old-{i}",
                    "main",
                    i + 10,
                    "completed",
                    timing.iso(START - 300),
                    timing.iso(START - 200),
                    json.dumps({"startedAt": timing.iso(START - 250)}),
                ),
            )
        db.execute(
            f"INSERT INTO {PREFIX}runs VALUES (?,?,?,?,?,?,?)",
            (
                "later",
                "main",
                100,
                "completed",
                timing.iso(AT),
                timing.iso(END + 120),
                json.dumps({"startedAt": timing.iso(END + 100)}),
            ),
        )
    snapshot = collect(
        path, START, END, limits=Limits(metadata=5, runs=10, items=10, turns=10, log_lines=10)
    )
    assert snapshot["metrics"]["runs_requested"]["value"] == 5
    assert snapshot["metrics"]["runs_started"]["value"] == 3
    assert snapshot["coverage"]["runs_seen"] == 4
    assert snapshot["coverage"]["scans"]["runs"]["scanned"] == 5
    assert not snapshot["coverage"]["partial"]


def test_missing_linked_pr_state_is_not_a_known_zero(tmp_path):
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        db.execute(
            f"UPDATE {PREFIX}threads SET payload_json=json_insert(payload_json,'$.pullRequests[#]',json(?)) WHERE thread_id='main'",
            (
                json.dumps(
                    {"host": "github.example.test", "repository": "sample/project", "number": 18}
                ),
            ),
        )
    snapshot = collect(path, START, END)
    assert snapshot["coverage"]["pr_states"] == 1
    assert snapshot["coverage"]["pr_unique"] == 2
    for name in ("prs_open", "prs_closed", "prs_merged"):
        assert snapshot["metrics"][name] == {"value": None, "reason": "pr_snapshot_missing"}


@pytest.mark.parametrize("change", ["expires_at", "revoked", "owner", "workspace", "kind"])
def test_current_connection_fences_all_owner_activity_surfaces(enrolled, change):  # noqa: F811
    hub, machine, path, snapshot = enrolled
    assert request(hub, "POST", path + "/consent", {"enabled": True})[0] == 200
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 200
    )
    other = None
    if change == "owner":
        other = hub.db.create_user("other@example.test")
        hub.db.add_member(hub.workspace, other, "owner", actor=hub.user)
    elif change == "workspace":
        other = hub.db.create_workspace("Other", hub.user)
    with hub.db._tx():
        cid = machine["machine_connection_id"]
        if change == "expires_at":
            hub.db._conn.execute(
                "UPDATE connections SET expires_at=? WHERE id=?", ("2001-01-01T00:00:00Z", cid)
            )
        elif change == "revoked":
            hub.db._conn.execute(
                "UPDATE connections SET revoked_at=? WHERE id=?", ("2001-01-01T00:00:00Z", cid)
            )
        elif change == "owner":
            hub.db._conn.execute("UPDATE connections SET user_id=? WHERE id=?", (other, cid))
        elif change == "workspace":
            hub.db._conn.execute("UPDATE connections SET workspace_id=? WHERE id=?", (other, cid))
        else:
            hub.db._conn.execute(
                "UPDATE runner_connections SET kind='worker' WHERE connection_id=?", (cid,)
            )
    assert request(hub, path=path)[0] == 404
    assert request(hub, path=path + "/" + snapshot["threads"][0]["id"])[0] == 404
    assert request(hub, "POST", path + "/consent", {"enabled": True})[0] == 404
    assert machine["machine_id"] not in {host["id"] for host in request(hub)[1]["hosts"]}
