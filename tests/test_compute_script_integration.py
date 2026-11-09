"""Real controller journals/API and actual pinned two-hop Paramiko socketpairs.

All identities, keys, receipts and paths are synthetic. The target process is an
explicit WIRE-ONLY fixture: it runs TargetHelper's real strict upload/result
protocol and a durable synthetic attempt journal, but NEVER launches a script or
claims production confinement. Parent owns suite execution; child static checks.
"""

import base64
import hashlib
import json
import os
import select
import sqlite3
import threading
from dataclasses import replace

import pytest

from commonplace import sessions
from commonplace.compute.control import StoreError
from commonplace.compute.executor import ExecutionService
from commonplace.compute.protocol import (
    ADMISSION_MODE,
    AdmissionChallenge,
    GatewayHelper,
    HostKeyPin,
    ProtocolDenied,
    ReplayJournal,
    admission_message,
    canonical,
    compile_operation,
    validate_admission,
)
from commonplace.compute.script_control import ScriptRuntimeConfig, ScriptRuntimeReceipt
from commonplace.compute.script_sandbox import ScriptResult
from commonplace.compute.script_transport import validate_envelope
from commonplace.compute.scripts import OPERATION, ScriptDenied, ScriptManifest, ScriptRequest
from commonplace.compute.target_helper import TARGET_HELPER_COMMAND, TargetHelper
from tests.test_compute_admission import admission_state as admission_state
from tests.test_compute_api import API
from tests.test_compute_control import approve, compute_env as compute_env
from tests.test_compute_executor import execution_env as execution_env, run_row, view
from tests.test_compute_fence import local_state as local_state
from tests.test_compute_ssh_fixture import SocketPairAdmissionSSH, paramiko

pytestmark = pytest.mark.skipif(os.name != "posix", reason="protected POSIX custody")


def runtime_receipt(state, **changes):
    with state.env.store._journal() as conn:
        proof = conn.execute(
            "SELECT * FROM enrollment_verifications WHERE node_id=?", (state.node["id"],)
        ).fetchone()
    receipt = ScriptRuntimeReceipt(
        "fixture-provisioner",
        state.env.workspace,
        state.gateway["id"],
        state.node["id"],
        proof["enrollment"],
        proof["receipt_digest"],
        "b" * 64,
        hashlib.sha256(base64.b64decode(state.credentials[1].public_key.split()[1])).hexdigest(),
        state.env.clock.now,
        state.env.clock.now + 200,
        b"",
    )
    receipt = replace(receipt, **changes)
    return replace(receipt, signature=state.provisioner.sign(receipt.signed_bytes()))


def script_payload(state, **changes):
    return {
        "node_id": state.node["id"],
        "task_number": state.env.task,
        "request_id": "immutable-script",
        "source": "# exact café\r\nprint('fixture')\r\n",
        "arguments": ["a b", "$(literal)", ""],
        "output_paths": ["report.txt"],
        **changes,
    }


@pytest.fixture
def script_state(admission_state):
    state = admission_state
    state.env.store.cancel_execution(state.env.principal, state.proposal["id"], **state.env.auth)
    receipt = runtime_receipt(state)
    workspace = replace(
        state.config.workspaces[0],
        scripts_enabled=True,
        script_runtimes=(
            ScriptRuntimeConfig(state.node["id"], receipt.runtime_digest, receipt.key_principal),
        ),
    )
    state.config = replace(state.config, workspaces=(workspace,))
    state.env.db._compute_config = state.config
    state.service._accept_script_runtime(
        state.env.principal,
        state.node["id"],
        **state.env.auth,
        receipt=receipt,
    )
    state.script, created = state.env.store.create_script(
        state.env.principal,
        **state.env.auth,
        **script_payload(state),
    )
    assert created
    state.script = json.loads(canonical(state.script))
    state.receipt = receipt
    return state


def propose_script(state, *, approval=True, request_id="script-execution"):
    proposal, created = state.env.store.propose_execution(
        state.env.principal,
        **state.env.auth,
        node_id=state.node["id"],
        task_number=state.env.task,
        operation=OPERATION,
        arguments={"bundle_id": state.script["id"]},
        request_id=request_id,
    )
    assert created
    state.proposal = approve(state.env, proposal) if approval else proposal
    return state.proposal


def authority_epoch(state):
    with state.coordinator.locked():
        return state.coordinator.epoch()


def test_default_off_rest_and_unknown_authority_fields(compute_env):
    api = API(compute_env)
    assert api.request(path="/scripts") == (200, {"scripts": []})
    assert api.request()[1]["can_manage"] is True
    assert api.request()[1]["capabilities"]["scripts"] is False
    _, node = api.enrolled()
    payload = {
        "node_id": node["id"],
        "task_number": compute_env.task,
        "request_id": "off",
        "source": "print(1)",
        "arguments": [],
        "output_paths": [],
    }
    assert api.request("POST", "/scripts", payload)[0] == 503
    for name, value in (
        ("verified", True),
        ("workspace_root", "/tmp"),
        ("runtime_digest", "b" * 64),
        ("input_paths", []),
        ("scripts_enabled", True),
        ("key_principal", "a" * 64),
    ):
        assert api.request("POST", "/scripts", {**payload, name: value})[0] == 400
    assert not (compute_env.path.parent / "compute-scripts.sqlite3").exists()


def test_rest_exact_source_replay_inspection_csrf_and_body_limit(script_state):
    state = script_state
    api = API(state.env)
    status, result = api.request("POST", "/scripts", script_payload(state))
    assert status == 200 and result["script"] == state.script
    assert api.request("POST", "/scripts", script_payload(state, source="changed"))[0] == 409
    status, result = api.request(path="/scripts/" + state.script["id"])
    assert status == 200 and result["script"]["source"] == script_payload(state)["source"]
    assert result["script"]["manifest"]["binding"]["authority_epoch"] is None
    assert api.request(path="/scripts")[1] == {"scripts": [state.script]}
    assert api.request()[1]["capabilities"]["scripts"] is True
    maximum = script_payload(state, request_id="maximum", source="#" + "x" * 32767)
    assert api.request("POST", "/scripts", maximum)[0] == 201
    assert api.request("POST", "/scripts", {**maximum, "source": "é" * 16385})[0] == 400
    assert api.request("POST", "/scripts", maximum, headers={"Origin": None})[0] == 403
    assert (
        api.request("POST", "/scripts", maximum, headers={"Origin": "http://foreign.test"})[0]
        == 403
    )
    assert api.request("POST", "/scripts", raw=b" " * 65537)[0] == 400
    assert api.request("POST", "/executions", raw=b" " * 8193)[0] == 400
    assert api.request("POST", "/scripts", raw=b'{"source":"a","source":"b"}')[0] == 400
    assert api.request("POST", "/scripts", script_payload(state, request_id="bad/key"))[0] == 400
    assert api.request(path="/scripts/foreign")[0] == 404
    cookie = sessions.start_session(state.env.db, state.env.member, state.env.workspace)
    assert api.request(path="/scripts/" + state.script["id"], cookie=cookie)[0] == 403


def test_real_rest_script_proposal_approval_and_claim(script_state):
    state = script_state
    api = API(state.env)
    payload = {
        "node_id": state.node["id"],
        "task_number": state.env.task,
        "request_id": "rest-script-execution",
        "operation": OPERATION,
        "arguments": {"bundle_id": state.script["id"]},
    }
    status, response = api.request("POST", "/executions", payload)
    assert status == 201
    proposal = response["execution"]
    assert proposal["arguments"] == payload["arguments"]
    assert proposal["script"]["manifest"] == state.script["manifest"]
    assert api.request("POST", "/executions", payload) == (200, response)
    assert (
        api.request("POST", "/executions", {**payload, "arguments": {"bundle_id": "other"}})[0]
        == 409
    )
    assert (
        api.request(
            "POST",
            "/executions/" + proposal["id"] + "/approve",
            {
                "expected_digest": proposal["digest"],
                "expires_at": state.env.clock.now + 60,
            },
        )[0]
        == 200
    )
    assert state.service._claim()[0].operation == OPERATION


def test_missing_runtime_receipt_disables_creation_without_hiding_old_objects(script_state):
    state = script_state
    with state.env.store._journal() as conn:
        conn.execute("UPDATE enrollment_verifications SET script_runtime=NULL")
    assert (
        state.env.store.inventory(state.env.principal, **state.env.auth)["capabilities"]["scripts"]
        is False
    )
    with pytest.raises(StoreError, match="scripts_unavailable"):
        state.env.store.create_script(
            state.env.principal, **state.env.auth, **script_payload(state, request_id="no-receipt")
        )
    assert state.env.store.get_script(state.env.principal, state.script["id"], **state.env.auth)[
        "source"
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"arguments": ["x"] * 17},
        {"arguments": ["é" * 129]},
        {"output_paths": ["a"] * 9},
        {"output_paths": ["../escape"]},
        {"output_paths": ["a", "a/b"]},
        {"source": "\x00"},
    ],
)
def test_finite_rest_contract(script_state, changes):
    api = API(script_state.env)
    assert (
        api.request(
            "POST", "/scripts", script_payload(script_state, request_id="invalid", **changes)
        )[0]
        == 400
    )


def test_proposal_approval_claim_separates_stable_contract_and_fresh_dispatch(script_state):
    state = script_state
    proposal = propose_script(state, approval=False)
    manifest_before = canonical(state.script["manifest"])
    proposed_epoch = authority_epoch(state)
    approved = approve(state.env, proposal)
    assert approved["digest"] == proposal["digest"]
    assert authority_epoch(state) > proposed_epoch
    request, _, _ = state.service._claim()
    assert request.authority_epoch == authority_epoch(state)
    assert request.operation == OPERATION and request.approval_digest
    assert state.service._claim() is None
    with state.service._guard() as lease:
        with state.env.store._journal() as conn:
            state.service._check(conn, lease, request)
            _, binding, _, _, origin = state.service._fresh(conn, request.execution)
            upload, source = state.service._script_payload(request, conn, binding, origin)
    assert canonical(upload.manifest.document()) == manifest_before
    assert upload.authority_epoch == request.authority_epoch
    assert upload.approval_digest == request.approval_digest
    assert upload.manifest.binding.authority_epoch is None
    assert ScriptRequest.from_dict(upload.document()) == upload
    assert upload.digest == request.arguments["script_digest"]
    assert source.decode() == script_payload(state)["source"]
    assert state.service._authorize(request, "connect") is True


def test_private_lookup_survives_unrelated_epoch_but_rechecks_current_owner(script_state):
    state = script_state
    before = authority_epoch(state)
    state.env.service.task_add("Unrelated canonical task", board="compute")
    assert authority_epoch(state) > before
    assert state.env.store.get_script(state.env.principal, state.script["id"], **state.env.auth)[
        "source"
    ]
    sessions.end_session(state.env.db, state.env.cookie)
    with pytest.raises(StoreError):
        state.env.store.get_script(state.env.principal, state.script["id"], **state.env.auth)


@pytest.mark.parametrize("stage", ["propose", "approve", "claim", "permit"])
def test_runtime_expiry_rechecked_at_every_authority_stage(script_state, stage):
    state = script_state
    if stage != "propose":
        proposal = propose_script(state, approval=stage != "approve")
    if stage == "permit":
        request, _, _ = state.service._claim()
        assert state.service._authorize(request, "connect")
        assert state.service._authorize(request, "dispatch")
        challenge = AdmissionChallenge(request, "a" * 64, "b" * 64, request.start_by)
    state.env.clock.now = state.receipt.expires_at
    if stage == "propose":
        with pytest.raises(StoreError):
            propose_script(state)
    elif stage == "approve":
        with pytest.raises(StoreError):
            approve(state.env, proposal)
    elif stage == "claim":
        assert state.service._claim() is None
    else:
        assert state.service._gateway_admission(request, "admit", challenge=challenge) is False
    assert (
        state.env.store.inventory(state.env.principal, **state.env.auth)["capabilities"]["scripts"]
        is False
    )


@pytest.mark.parametrize(
    "changed", ["runtime", "key", "mode", "off", "receipt", "credential", "verifier"]
)
def test_changed_trusted_runtime_or_enrollment_cannot_use_old_consent(script_state, changed):
    state = script_state
    proposal = propose_script(state)
    workspace = state.config.workspaces[0]
    if changed == "runtime":
        workspace = replace(
            workspace,
            script_runtimes=(replace(workspace.script_runtimes[0], runtime_digest="c" * 64),),
        )
    elif changed == "key":
        workspace = replace(
            workspace,
            script_runtimes=(replace(workspace.script_runtimes[0], key_principal="c" * 64),),
        )
    elif changed == "mode":
        workspace = replace(workspace, admission_mode=None)
    elif changed == "off":
        workspace = replace(workspace, scripts_enabled=False)
    elif changed == "receipt":
        with state.env.store._journal() as conn:
            conn.execute("UPDATE enrollment_verifications SET script_runtime=NULL")
    elif changed == "verifier":
        workspace = replace(workspace, provisioning_keys={})
    else:
        credential = state.credentials[1]
        state.custody.revoke(credential.credential_id, credential.binding, state.context)
    state.env.db._compute_config = replace(state.config, workspaces=(workspace,))
    assert state.service._claim() is None
    assert run_row(state, proposal) is None


def test_receipt_requires_pinned_independent_signature_and_exact_runtime(script_state):
    state = script_state
    for receipt in (
        replace(state.receipt, signature=b"x" * 64),
        runtime_receipt(state, runtime_digest="c" * 64),
        runtime_receipt(state, enrollment="c" * 64),
        runtime_receipt(state, key_principal="c" * 64),
    ):
        with pytest.raises(StoreError, match="verification_unavailable"):
            state.service._accept_script_runtime(
                state.env.principal, state.node["id"], **state.env.auth, receipt=receipt
            )


class WireOnlyTarget(TargetHelper):
    """Explicit fake process for protocol tests. NO sandbox or host script launch."""

    def __init__(self, fixture, channel):
        super().__init__()
        self.fixture, self.channel = fixture, channel

    def _validate(self, request, phase):
        fixture = self.fixture
        assert request.digest == fixture.request.arguments["script_digest"]
        assert request.manifest.runtime_digest == fixture.state.receipt.runtime_digest
        assert request.manifest.binding.node == fixture.state.node["id"]
        assert request.authority_epoch == fixture.request.authority_epoch
        fixture.hit("script_" + phase)

    def execute_verified(self, request, script, *, cancelled=lambda: False):
        validate_envelope(self.fixture.request, request, script)
        with sqlite3.connect(self.fixture.target_journal) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS wire_attempts (attempt TEXT PRIMARY KEY, digest TEXT)"
            )
            conn.execute(
                "INSERT INTO wire_attempts VALUES (?,?)", (request.attempt, request.digest)
            )
        self.fixture.uploads.append(script)
        self.fixture.hit("script_committed")
        if self.fixture.script_fault == "lost_result":
            self.channel.close()
            raise ProtocolDenied("control_disconnected")
        if self.fixture.script_fault == "cancel":
            self.fixture.state.env.store.cancel_execution(
                self.fixture.state.env.principal, request.execution, **self.fixture.state.env.auth
            )
            assert select.select([self.channel], [], [], 2)[0]
            assert cancelled()
            return ScriptResult("observed_exit", "cancel_requested", -15, True, True)
        return ScriptResult(
            "observed_exit",
            "exited",
            0,
            False,
            True,
            b"fixture token=sk-" + b"x" * 48,
            b"",
            (("report.txt", b"private fixture"),),
        )


class ScriptSocketPairSSH(SocketPairAdmissionSSH):
    """Actual pinned outer/inner SSH + wire-only target; no listener/devices."""

    def __init__(self, state, *, script_fault=None, **kwargs):
        super().__init__(state, state.gateway_host, state.target_host, **kwargs)
        self.script_fault = script_fault
        self.uploads = []
        self.target_journal = state.env.path.parent / "wire-only-script-target.sqlite3"

    def server(self, hop):
        if hop == "gateway":
            return super().server(hop)
        fixture = self
        enrollment = self.state.credentials[1]

        class Server(paramiko.ServerInterface):
            def __init__(self):
                self.command = threading.Event()

            def check_auth_publickey(self, username, key):
                expected = base64.b64decode(enrollment.public_key.split()[1])
                if username == enrollment.binding.account and key.asbytes() == expected:
                    fixture.hit("auth:target")
                    fixture.authenticated.append("target")
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "publickey"

            def check_channel_request(self, kind, chanid):
                return (
                    paramiko.OPEN_SUCCEEDED
                    if kind == "session"
                    else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
                )

            def check_channel_exec_request(self, channel, command):
                if command != TARGET_HELPER_COMMAND.encode():
                    return False
                fixture.hit("fixed_script_helper")
                fixture.commands.append(command)
                self.command.set()
                return True

        return Server()

    def serve_target(self, sock):
        server = self.server("target")
        transport = paramiko.Transport(sock)
        self.resources.append(transport)
        transport.add_server_key(self.target_host)
        transport.start_server(server=server)
        channel = transport.accept(5)
        if channel is not None and server.command.wait(5):
            WireOnlyTarget(self, channel).serve(channel)
            if not channel.closed:
                channel.send_exit_status(0)
                channel.shutdown_write()
                channel.close()
        self.stop.wait(5)

    def serve_gateway(self, sock, gateway, route):
        fixture = self
        transport = paramiko.Transport(sock)
        self.resources.append(transport)
        transport.add_server_key(self.gateway_host)
        server = self.server("gateway")
        transport.start_server(server=server)
        channel = transport.accept(5)
        if channel is None or not server.subsystem.wait(5):
            return

        class Helper(GatewayHelper):
            def prepare(self, request):
                fixture.request = request
                fixture.hit("ready")
                return super().prepare(request)

            def challenge(self, request, ready, go):
                fixture.hit("before_challenge")
                return super().challenge(request, ready, go)

            def admit_permit(self, request, challenge, permit):
                fixture.hit("before_helper_permit")
                return super().admit_permit(request, challenge, permit)

        with sqlite3.connect(self.helper_path) as conn:
            helper = Helper(
                gateway,
                [route],
                ReplayJournal(conn),
                lambda req, phase: True,
                self.open_route,
                binding=(gateway.workspace, gateway.gateway),
                enabled=True,
                admission_mode=ADMISSION_MODE,
                scripts_enabled=True,
                wall=lambda: fixture.state.env.clock.now,
            )
            helper.serve(channel)


@pytest.fixture
def script_ssh(script_state):
    state = script_state
    fixtures = []

    def create(**kwargs):
        fixture = ScriptSocketPairSSH(state, **kwargs)
        fixtures.append(fixture)
        state.service._driver = fixture.driver
        return fixture

    yield create
    for fixture in fixtures:
        fixture.close()


def test_actual_two_hop_script_wire_journal_redacted_metadata_and_no_task_completion(
    script_state, script_ssh, monkeypatch
):
    state = script_state
    propose_script(state)
    fixture = script_ssh()
    monkeypatch.setattr(
        "socket.create_connection", lambda *a, **k: pytest.fail("no ambient network")
    )
    assert state.service._run_once()
    assert fixture.authenticated == ["gateway", "target"]
    assert fixture.commands == [TARGET_HELPER_COMMAND.encode()]
    assert fixture.uploads == [script_payload(state)["source"].encode()]
    run = view(state, state.proposal)["run"]
    assert run["phase"] == "terminal" and run["capacity_reserved"] is False, run
    assert run["result"]["state"] == "observed_exit"
    assert "sk-" + "x" * 48 not in run["result"]["stdout"]
    assert run["result"]["artifacts"] == [
        {"path": "report.txt", "size": 15, "sha256": hashlib.sha256(b"private fixture").hexdigest()}
    ]
    assert not (state.env.path.parent / "report.txt").exists()
    assert state.env.service.task_show(state.env.task)["status"] == "todo"
    assert not state.service._run_once()


@pytest.mark.parametrize("hop", ["gateway", "target"])
def test_script_both_pins_precede_auth_and_no_upload_on_mismatch(script_state, script_ssh, hop):
    state = script_state
    propose_script(state)
    fixture = script_ssh()
    original = fixture.driver.handshake

    def handshake(channel, pin, name, timeout):
        if name == hop:
            pin = HostKeyPin(pin.algorithm, b"x" * 64)
        return original(channel, pin, name, timeout)

    fixture.driver.handshake = handshake
    assert state.service._run_once()
    assert fixture.authenticated == ([] if hop == "gateway" else ["gateway"])
    assert not fixture.commands and not fixture.uploads
    assert not state.service._run_once()


@pytest.mark.parametrize("fault", ["lost_result", "missing_permit", "partial_permit"])
def test_lost_script_or_permit_ack_is_reserved_and_never_repeated(script_state, script_ssh, fault):
    state = script_state
    propose_script(state)
    fixture = script_ssh(
        script_fault=fault if fault == "lost_result" else None,
        fault=fault if fault != "lost_result" else None,
    )
    assert state.service._run_once()
    run = run_row(state, state.proposal)
    assert run["phase"] == "uncertain" and run["reserved"] == 1
    assert not state.service._run_once()
    restarted = ExecutionService(
        state.env.store,
        enabled=True,
        fence=state.fence,
        admission_mode=ADMISSION_MODE,
        wrapping_key_path=state.key_path,
        provisioning_keys=state.service._provisioning_keys,
        driver=fixture.driver,
    )
    assert not restarted._run_once()
    assert len(fixture.uploads) == (1 if fault == "lost_result" else 0)


def test_actual_cancel_frame_is_intent_until_wire_fixture_reports_exit(script_state, script_ssh):
    state = script_state
    propose_script(state)
    fixture = script_ssh(script_fault="cancel")
    assert state.service._run_once()
    run = run_row(state, state.proposal)
    assert run["cancel_requested_at"] is not None
    assert run["phase"] == "terminal" and run["exit_code"] == -15
    assert len(fixture.uploads) == 1


def test_revocation_committed_before_permit_prevents_inner_auth_and_upload(
    script_state, script_ssh
):
    state = script_state
    propose_script(state)

    def hook(phase, fixture):
        if phase == "before_challenge":
            credential = state.credentials[1]
            state.custody.revoke(credential.credential_id, credential.binding, state.context)

    fixture = script_ssh(hook=hook)
    assert state.service._run_once()
    assert fixture.authenticated == ["gateway"] and not fixture.uploads
    with state.env.store._journal() as conn:
        assert conn.execute("SELECT count(*) FROM gateway_admissions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", "system.info.v1"),
        ("purpose", "system.info.v1"),
        ("digest", "f" * 64),
        ("approval_digest", "f" * 64),
    ],
)
def test_script_permit_cannot_be_converted_to_another_operation(script_state, field, value):
    state = script_state
    propose_script(state)
    request, _, _ = state.service._claim()
    permit = admission_message("admission_permit", request, "a" * 64, "b" * 64, request.start_by)
    permit[field] = value
    with pytest.raises(ProtocolDenied):
        validate_admission(permit, "admission_permit", request)


def test_fixed_probe_and_legacy_script_wire_remain_exact():
    assert compile_operation("system.info.v1", {}) == "/usr/bin/uname -srm"
    from tests.test_compute_scripts import binding

    manifest = ScriptManifest.create(binding(), "legacy", b"print(1)\n", runtime_digest="b" * 64)
    request = ScriptRequest("execution", "attempt", manifest, 1234.0)
    expected = {
        "execution": "execution",
        "attempt": "attempt",
        "manifest": manifest.document(),
        "expires_at": 1234.0,
        "controller_contract": ADMISSION_MODE,
    }
    assert canonical(request.document()) == canonical(expected)
    assert request.digest == hashlib.sha256(canonical(expected)).hexdigest()
    with pytest.raises(ScriptDenied):
        replace(request, approval_digest="f" * 64, authority_epoch=1)
    with pytest.raises(ScriptDenied):
        ScriptRequest.from_dict({**expected, "authority_epoch": None, "approval_digest": None})


@pytest.mark.parametrize(
    "change",
    [
        "script",
        "runtime",
        "path",
        "arguments",
        "attempt",
        "expiry",
        "node",
        "gateway_epoch",
        "target_credential_version",
        "authority_epoch",
    ],
)
def test_exact_upload_envelope_detects_tamper(script_state, change):
    state = script_state
    propose_script(state)
    request, _, _ = state.service._claim()
    with state.service._guard():
        with state.env.store._journal() as conn:
            _, binding, _, _, origin = state.service._fresh(conn, request.execution)
            upload, script = state.service._script_payload(request, conn, binding, origin)
    if change == "script":
        script += b"# changed"
    elif change == "runtime":
        upload = replace(upload, manifest=replace(upload.manifest, runtime_digest="c" * 64))
    elif change == "path":
        upload = replace(upload, manifest=replace(upload.manifest, output_paths=("other.txt",)))
    elif change == "arguments":
        upload = replace(upload, manifest=replace(upload.manifest, arguments=("changed",)))
    elif change == "attempt":
        upload = replace(upload, attempt="other")
    elif change == "expiry":
        upload = replace(upload, expires_at=upload.expires_at + 1)
    elif change == "authority_epoch":
        upload = replace(upload, authority_epoch=upload.authority_epoch + 1)
    else:
        value = "other" if change == "node" else getattr(upload.manifest.binding, change) + 1
        upload = replace(
            upload,
            manifest=replace(
                upload.manifest, binding=replace(upload.manifest.binding, **{change: value})
            ),
        )
    with pytest.raises(ProtocolDenied):
        validate_envelope(request, upload, script)
