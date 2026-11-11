"""Bot machines provider: who may use which machine, and starting or cancelling an agent."""

import json

import pytest

from commonplace import sessions
from commonplace.bots import machines
from commonplace.runner import jobs, registry
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service

GRANT = {
    "label": "example-machine",
    "projects": ["demo"],
    "topics": ["worker"],
    "wings": ["shared"],
}


class Setup:
    def __init__(self, bots_hub):
        self.bots = bots_hub
        self.gateway = bots_hub.hub.gateway
        self.db = bots_hub.hub.db
        self.owner = bots_hub.person(bots_hub.hub.admin)
        self.member = bots_hub.person(bots_hub.member_user)
        self.other_owner = sessions.person_principal(
            self.db, bots_hub.other_user, bots_hub.other_workspace
        )
        self.secrets = []

    def enroll(self, owner=None, attest=True, **grant):
        owner = owner or self.owner
        made = registry.enroll(self.db, owner, {**GRANT, **grant})
        self.secrets += [
            made["machine_token"],
            made["worker_token"],
            made["machine_connection_id"],
            made["worker_connection_id"],
        ]
        if attest:
            row = registry.get_machine(self.db, owner.workspace_id, made["machine_id"])
            registry.attest(
                self.db,
                owner,
                made["machine_id"],
                {
                    "topics": json.loads(row["topics"]),
                    "provider_instance_id": "worker",
                    "worker_connection_id": made["worker_connection_id"],
                },
            )
        return made

    def card(self, title="Demo task", board="work"):
        return service(self.bots.hub, self.owner).task_add(title=title, board=board)["number"]

    def set_state(self, job, state):
        store = jobs.open_store(self.gateway, self.owner.workspace_id)
        with store.transaction() as conn:
            conn.execute("UPDATE runner_jobs SET state=? WHERE id=?", (state, job))

    def jobs(self):
        store = jobs.open_store(self.gateway, self.owner.workspace_id)
        with store.connection() as conn:
            return conn.execute("SELECT * FROM runner_jobs").fetchall()


@pytest.fixture(name="env")
def env_fixture(bots_hub):
    yield Setup(bots_hub)
    machines.unregister_provider("fake")


def test_owner_sees_exactly_the_attested_machine(env):
    env.enroll(max_runtime_mode="auto")
    listed = machines.list_machines(env.gateway, env.owner)
    assert len(listed) == 1
    machine = listed[0]
    assert set(machine) == {
        "id",
        "label",
        "last_seen",
        "capabilities",
        "grants",
        "trust",
        "verified",
    }
    assert machine["label"] == "example-machine"
    assert machine["last_seen"] is None
    assert machine["capabilities"] == {"start_agent": True, "run_command": False}
    assert machine["grants"] == {
        "projects": ["demo"],
        "topics": ["worker"],
        "wings": ["shared"],
        "max_runtime_mode": "auto",
    }
    assert machine["trust"] == "owner_attested"
    assert machine["verified"] is False
    assert machines.why_none(env.gateway, env.owner) == ""


def test_member_sees_none_and_gets_a_plain_reason(env):
    env.enroll()
    assert machines.list_machines(env.gateway, env.member) == []
    reason = machines.why_none(env.gateway, env.member)
    assert "owner" in reason and reason.endswith(".")


def test_owner_without_machines_is_told_so(env):
    assert machines.list_machines(env.gateway, env.owner) == []
    assert "No machine is set up" in machines.why_none(env.gateway, env.owner)


def test_revoked_machine_disappears(env):
    made = env.enroll()
    assert len(machines.list_machines(env.gateway, env.owner)) == 1
    registry.revoke(env.db, env.owner, made["machine_id"])
    assert machines.list_machines(env.gateway, env.owner) == []
    assert "No machine is set up" in machines.why_none(env.gateway, env.owner)


def test_unattested_machine_is_listed_but_cannot_start(env):
    made = env.enroll(attest=False)
    (machine,) = machines.list_machines(env.gateway, env.owner)
    assert machine["trust"] == "unbound"
    assert machine["capabilities"]["start_agent"] is False
    assert machine["verified"] is False
    with pytest.raises(machines.Refused) as caught:
        machines.start_agent(
            env.gateway, env.owner, env.card(), machine=made["machine_id"], request_key="k-1"
        )
    assert caught.value.code == "not_attested"
    assert "attested" in caught.value.text
    assert env.jobs() == []


def test_machine_of_another_workspace_is_never_listed(env):
    env.enroll(owner=env.other_owner, label="other-machine")
    assert machines.list_machines(env.gateway, env.owner) == []
    ids = [m["label"] for m in machines.list_machines(env.gateway, env.other_owner)]
    assert ids == ["other-machine"]
    env.enroll()
    assert [m["label"] for m in machines.list_machines(env.gateway, env.owner)] == [
        "example-machine"
    ]


def test_start_agent_creates_one_job_per_request_key(env):
    env.enroll()
    card = env.card()
    first = machines.start_agent(env.gateway, env.owner, card, request_key="turn-1:1")
    again = machines.start_agent(env.gateway, env.owner, card, request_key="turn-1:1")
    assert first == again
    assert first["state"] == "queued" and first["job"].startswith("job_")
    assert len(env.jobs()) == 1
    status = machines.start_status(env.gateway, env.owner, card)
    assert [(s["job"], s["state"], s["machine_label"]) for s in status] == [
        (first["job"], "queued", "example-machine")
    ]
    assert set(status[0]) == {"job", "machine_label", "state", "updated_at"}


def test_start_agent_with_a_named_machine_by_label_or_id(env):
    made = env.enroll()
    first = env.card("one")
    second = env.card("two")
    by_label = machines.start_agent(
        env.gateway, env.owner, first, machine="example-machine", request_key="k-a"
    )
    by_id = machines.start_agent(
        env.gateway, env.owner, second, machine=made["machine_id"], request_key="k-b"
    )
    assert by_label["job"] != by_id["job"]
    with pytest.raises(machines.Refused) as caught:
        machines.start_agent(env.gateway, env.owner, env.card("three"), machine="nope")
    assert caught.value.code == "machine_not_found"


def test_card_outside_the_machine_wings_is_refused_with_the_runner_code(env):
    env.enroll(wings=["notes"])
    card = env.card()
    with pytest.raises(machines.Refused) as caught:
        machines.start_agent(env.gateway, env.owner, card, request_key="k-w")
    assert caught.value.code == "outside_worker_wing"
    assert caught.value.text.endswith(".") and "machine" in caught.value.text
    assert env.jobs() == []


def test_card_that_is_not_todo_is_refused_plainly(env):
    env.enroll()
    card = service(env.bots.hub, env.owner).task_add(title="Later", board="work", status="backlog")[
        "number"
    ]
    with pytest.raises(machines.Refused) as caught:
        machines.start_agent(env.gateway, env.owner, card, request_key="k-n")
    assert caught.value.code == "not_todo"
    assert "to-do" in caught.value.text
    assert env.jobs() == []


def test_second_start_on_the_same_card_is_refused(env):
    env.enroll()
    card = env.card()
    machines.start_agent(env.gateway, env.owner, card, request_key="k-1")
    with pytest.raises(machines.Refused) as caught:
        machines.start_agent(env.gateway, env.owner, card, request_key="k-2")
    assert caught.value.code == "active_start"


def test_several_machines_need_a_choice(env):
    env.enroll(label="machine-a")
    env.enroll(label="machine-b")
    with pytest.raises(machines.Refused) as caught:
        machines.start_agent(env.gateway, env.owner, env.card(), request_key="k-m")
    assert caught.value.code == "machine_needed"
    assert "machine-a" in caught.value.text and "machine-b" in caught.value.text


def test_member_cannot_start_or_see_starts(env):
    env.enroll()
    card = env.card()
    with pytest.raises(machines.Refused) as caught:
        machines.start_agent(env.gateway, env.member, card, request_key="k-x")
    assert caught.value.code == "no_machine"
    assert machines.start_status(env.gateway, env.member, card) == []


def test_cancel_start_cancels_a_queued_start(env):
    env.enroll()
    started = machines.start_agent(env.gateway, env.owner, env.card(), request_key="k-c")
    assert machines.cancel_start(env.gateway, env.owner, started["job"]) == {"state": "cancelled"}
    assert env.jobs()[0]["state"] == "cancelled"


def test_cancel_start_refuses_a_launched_agent_plainly(env):
    env.enroll()
    started = machines.start_agent(env.gateway, env.owner, env.card(), request_key="k-l")
    env.set_state(started["job"], "started")
    with pytest.raises(machines.Refused) as caught:
        machines.cancel_start(env.gateway, env.owner, started["job"])
    assert caught.value.code == "already_finished"
    assert "can't be stopped" in caught.value.text
    with pytest.raises(machines.Refused) as unknown:
        machines.cancel_start(env.gateway, env.owner, "job_0000000000000000")
    assert unknown.value.code == "start_not_found"


def test_run_command_is_unsupported(env):
    made = env.enroll()
    with pytest.raises(machines.Unsupported):
        machines.run_command(env.gateway, env.owner, made["machine_id"], "ls")
    with pytest.raises(machines.Unsupported):
        machines.run_command(env.gateway, env.owner, "unknown", "ls")
    assert machines.can_run_command(env.gateway, env.owner) is False


class FakeProvider:
    name = "fake"

    def __init__(self, run_command=False):
        self.run_command_on = run_command
        self.started, self.ran = [], []

    def machines(self, gateway, principal):
        return [
            {
                "id": "fake-1",
                "label": "fake-machine",
                "last_seen": "2026-01-01T00:00:00Z",
                "capabilities": {"start_agent": True, "run_command": self.run_command_on},
                "grants": {"projects": ["p"], "topics": ["t"], "wings": ["w"]},
                "trust": "owner_attested",
                "verified": True,  # a provider cannot claim verification
                "token": "must-not-leak",
                "hostname": "host.invalid",
            }
        ]

    def why_none(self, gateway, principal):
        return ""

    def start_agent(self, gateway, principal, card, machine, request_key):
        self.started.append((card, machine, request_key))
        return {"job": "fake-job", "state": "queued"}

    def start_status(self, gateway, principal, card):
        return [
            {
                "job": "fake-job",
                "machine_label": "fake-machine",
                "state": "queued",
                "updated_at": "2026-01-02T00:00:00Z",
            }
        ]

    def cancel_start(self, gateway, principal, job):
        if job != "fake-job":
            raise machines.Refused("start_not_found")
        return {"state": "cancelled"}

    def run_command(self, gateway, principal, machine, command):
        self.ran.append((machine, command))
        return {"state": "proposed"}


def test_a_second_provider_is_merged_and_its_capabilities_respected(env):
    env.enroll()
    fake = FakeProvider()
    machines.register_provider(fake)
    listed = {m["label"]: m for m in machines.list_machines(env.gateway, env.owner)}
    assert set(listed) == {"example-machine", "fake-machine"}
    assert listed["fake-machine"]["verified"] is False
    assert "token" not in listed["fake-machine"] and "hostname" not in listed["fake-machine"]
    # no machine named and two capable providers: the person must choose
    with pytest.raises(machines.Refused) as caught:
        machines.start_agent(env.gateway, env.owner, env.card(), request_key="k-f")
    assert caught.value.code == "machine_needed"
    # a named machine goes to its own provider
    assert machines.start_agent(
        env.gateway, env.owner, 7, machine="fake-machine", request_key="k-g"
    ) == {"job": "fake-job", "state": "queued"}
    assert fake.started == [(7, "fake-machine", "k-g")]
    assert [s["job"] for s in machines.start_status(env.gateway, env.owner, 7)] == ["fake-job"]
    assert machines.cancel_start(env.gateway, env.owner, "fake-job") == {"state": "cancelled"}
    # commands: not offered until a machine reports the capability
    assert machines.can_run_command(env.gateway, env.owner) is False
    with pytest.raises(machines.Unsupported):
        machines.run_command(env.gateway, env.owner, "fake-machine", "ls")
    machines.register_provider(FakeProvider(run_command=True))
    assert machines.can_run_command(env.gateway, env.owner) is True
    assert machines.run_command(env.gateway, env.owner, "fake-machine", "ls") == {
        "state": "proposed"
    }


def test_a_failing_provider_does_not_hide_the_others(env):
    env.enroll()

    class Broken(FakeProvider):
        def machines(self, gateway, principal):
            raise RuntimeError("down")

    machines.register_provider(Broken())
    assert [m["label"] for m in machines.list_machines(env.gateway, env.owner)] == [
        "example-machine"
    ]


def test_no_credential_appears_in_any_returned_value(env):
    made = env.enroll()
    card = env.card()
    started = machines.start_agent(env.gateway, env.owner, card, request_key="k-s")
    seen = [
        machines.list_machines(env.gateway, env.owner),
        machines.start_status(env.gateway, env.owner, card),
        started,
        machines.cancel_start(env.gateway, env.owner, started["job"]),
    ]
    with pytest.raises(machines.Refused) as caught:
        machines.start_agent(env.gateway, env.owner, card, machine="nope")
    seen.append([caught.value.code, caught.value.text])
    text = json.dumps(seen)
    for secret in env.secrets:
        assert secret not in text
    assert "lease_token" not in text and made["machine_id"] in text
