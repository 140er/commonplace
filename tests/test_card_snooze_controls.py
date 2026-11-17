"""Browser-free controls in both card drawers and boards."""

import json
import subprocess
from pathlib import Path

import pytest

from commonplace import schedule_time as times
from commonplace.orch import durable
from commonplace.runner.jobs import JobStore
from tests._console_helpers import console_hub_fixture, service  # noqa: F401
from tests.test_card_snooze import fake_clock  # noqa: F401


def test_card_snooze_controls():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("card_snooze_controls.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "choice,now,zone,expected",
    [
        ("tomorrow", "2030-03-30T08:00Z", "Europe/Amsterdam", "2030-03-31T07:00:00Z"),
        ("tomorrow", "2030-10-26T07:00Z", "Europe/Amsterdam", "2030-10-27T08:00:00Z"),
        ("monday", "2030-03-30T08:00Z", "Europe/Amsterdam", "2030-04-01T07:00:00Z"),
        ("tomorrow", "2030-12-31T23:30Z", "America/New_York", "2031-01-01T14:00:00Z"),
        ("later", "2030-03-31T00:30Z", "Europe/Amsterdam", "2030-03-31T03:30:00Z"),
    ],
)
def test_shortcut_payload_reaches_the_existing_service(
    console_hub,
    fake_clock,  # noqa: F811
    choice,
    now,
    zone,
    expected,
):
    h = console_hub
    fake_clock.t = times.at(now)
    card = service(h.hub, h.person(h.hub.admin)).task_add("Return later", board="work")
    script = (
        "import {snoozeChoice} from './commonplace/web/static/card-controls.js';"
        f"console.log(JSON.stringify(snoozeChoice({json.dumps(choice)},"
        f"new Date({json.dumps(now)}),{json.dumps(zone)})));"
    )
    payload = json.loads(
        subprocess.check_output(["node", "--input-type=module", "-e", script], timeout=10)
    )
    status, result, _ = h.post(h.owner, f"/api/card-snooze/{card['number']}", payload)
    assert status == 200 and result["snoozed"]["until"] == expected


def test_snoozed_proposal_leaves_needs_queue_and_wake_restores_it(console_hub):
    h = console_hub
    owner = service(h.hub, h.person(h.hub.admin))
    n = owner.task_add("Consider later", board="work", status="backlog")["number"]
    with owner.store.transaction() as conn:
        conn.execute("UPDATE tasks SET proposal_status='pending' WHERE id=?", (n,))

    def queued():
        return {c["number"] for c in h.get(h.owner, "/api/needs-me")[1]["backlog_from_agents"]}

    assert n in queued()
    assert h.post(h.owner, f"/api/card-snooze/{n}", {"until": "2030-01-08T09:00Z"})[0] == 200
    assert n not in queued()
    assert h.post(h.owner, f"/api/card-snooze/{n}/cancel", {})[0] == 200
    assert n in queued()


def test_existing_dispatch_list_uses_the_same_snooze_and_wake(console_hub):
    h = console_hub
    owner = service(h.hub, h.person(h.hub.admin))
    n = owner.task_add("Dispatch later", board="work")["number"]
    store = JobStore(h.hub.gateway, h.hub.workspace)
    with owner.store.connection() as conn:
        board = dict(conn.execute("SELECT * FROM boards WHERE slug='work'").fetchone())

    def offered():
        return {c["card"] for c in durable.unstarted_todo(owner.ctx, store, board)}

    assert n in offered()
    assert h.post(h.owner, f"/api/card-snooze/{n}", {"until": "2030-01-08T09:00Z"})[0] == 200
    assert n not in offered()
    assert h.post(h.owner, f"/api/card-snooze/{n}/cancel", {})[0] == 200
    assert n in offered()
