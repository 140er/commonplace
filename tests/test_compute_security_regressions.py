"""Acceptance reconcile regressions; synthetic stores/files and no device or LAN."""

import hashlib
import json
import os
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace.compute.control import StoreError
from commonplace.compute.script_sandbox import ScriptResult
from commonplace.compute.scripts import ScriptDenied, ScriptInput
from tests.test_compute_admission import admission_state as admission_state
from tests.test_compute_control import (
    approve,
    compute_env as compute_env,
    enroll,
    scope_payload,
)
from tests.test_compute_executor import (
    execution_env as execution_env,
    mutate,
    prepare,
    run_row,
)
from tests.test_compute_fence import local_state as local_state
from tests.test_compute_script_integration import (
    propose_script,
    script_state as script_state,
)
from tests.test_compute_script_sandbox import cancelled_storage_helper
from tests.test_compute_scripts import request


def actions(env, execution):
    # A separate connection proves durability after the service transaction.
    with sqlite3.connect(env.path) as conn:
        return [
            r[0]
            for r in conn.execute(
                "SELECT action FROM audit WHERE object_id=? ORDER BY id", (execution,)
            )
        ]


@pytest.mark.parametrize("new_session", [False, True])
def test_script_requester_cannot_self_approve_by_default(script_state, new_session):
    state = script_state
    object.__setattr__(state.config.workspaces[0], "allow_script_self_approval", False)
    proposal = propose_script(state, approval=False)
    if new_session:
        from commonplace import sessions

        cookie = sessions.start_session(state.env.db, state.env.owner, state.env.workspace)
        with pytest.raises(StoreError):
            state.env.store.approve_execution(
                state.env.principal,
                proposal["id"],
                session_hash=sessions._hash(cookie),
                expected_digest=proposal["digest"],
                expires_at=state.env.clock.now + 60,
            )
    else:
        with pytest.raises(StoreError):
            approve(state.env, proposal)
    assert (
        state.env.store.get_execution(state.env.principal, proposal["id"], **state.env.auth)[
            "state"
        ]
        == "awaiting_approval"
    )


def test_self_approval_policy_is_rechecked_before_claim(script_state):
    state = script_state
    proposal = propose_script(state)
    object.__setattr__(state.config.workspaces[0], "allow_script_self_approval", False)
    assert state.service._claim() is None
    assert "execution.admission_denied" in actions(state.env, proposal["id"])


def test_taskless_scope_preserves_null_context(compute_env):
    env = compute_env
    gateway, _ = enroll(env)
    scope = env.store.create_scope(
        env.principal, **env.auth, **scope_payload(env, gateway["id"], task_number=None)
    )
    assert scope["context"]["task"] is None
    with sqlite3.connect(env.path) as conn:
        task, metadata = conn.execute("SELECT task_number,metadata FROM scopes").fetchone()
    assert task is None and json.loads(metadata)["context"]["task"] is None


@pytest.mark.parametrize(
    "change", ["approval_expiry", "origin_session", "node_pin", "verification_removed"]
)
def test_preclaim_denial_is_durably_audited(execution_env, change):
    state = execution_env
    proposal = prepare(state)
    mutate(state, change)
    assert not state.service._run_once()
    assert run_row(state, proposal) is None
    assert state.driver.calls == []
    assert "execution.admission_denied" in actions(state.env, proposal["id"])


@pytest.mark.parametrize("outcome", ["exit", "uncertain", "authority_lost"])
def test_execution_outcome_and_exit_are_durably_audited(execution_env, outcome):
    state = execution_env
    proposal = prepare(state)
    if outcome == "uncertain":
        state.driver.lost_open_ack = True
    elif outcome == "authority_lost":
        state.driver.hooks["exec"] = lambda: mutate(state, "origin_session")
    assert state.service._run_once()
    row = run_row(state, proposal)
    audit = actions(state.env, proposal["id"])
    assert f"execution.{row['phase']}.{row['reason']}" in audit
    if row["exit_code"] is not None:
        assert f"execution.exit.{row['exit_code']}" in audit
    assert not any("Linux" in action for action in audit)


@pytest.mark.parametrize("swap", ["claim_directory", "script", "input", "after_check"])
def test_rename_to_execute_uses_only_approved_bytes(tmp_path, monkeypatch, swap):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    script = b"print('fixture')\n"
    value = request(script)
    input_bytes = b"approved input"
    if swap == "input":
        (root / "inputs" / "data").write_bytes(input_bytes)
        value = replace(
            value,
            manifest=replace(
                value.manifest,
                input_paths=(
                    ScriptInput("data", hashlib.sha256(input_bytes).hexdigest(), len(input_bytes)),
                ),
            ),
        )
    observed = []

    def fake_spawn(args, **kwargs):
        # This fixture never creates a process. Observe exactly what bwrap would
        # bind, including mutation after the final userspace validation.
        if swap == "after_check":
            claim = next((root / "attempts").iterdir())
            (claim / "script.py").write_bytes(b"substituted code")
        data = {}
        for index, arg in enumerate(args[:-2]):
            if arg == "--ro-bind-data":
                fd = int(args[index + 1])
                assert fd in kwargs["pass_fds"]
                data[args[index + 2]] = os.pread(fd, 65536, 0)
            elif arg == "--ro-bind" and args[index + 2].startswith("/job/"):
                data[args[index + 2]] = Path(args[index + 1]).read_bytes()
        observed.append(data)
        return object()

    def run(request_value, script_path, job_path, **kwargs):
        claim = next((root / "attempts").iterdir())
        if swap == "claim_directory":
            claim.rename(claim.with_name(claim.name + "-held"))
            claim.mkdir(mode=0o700)
            (claim / "script.py").write_bytes(b"substituted code")
        elif swap == "script":
            (claim / "script.py").unlink()
            (claim / "script.py").write_bytes(b"substituted code")
        elif swap == "input":
            (Path(job_path) / "data").write_bytes(b"substituted input")
        try:
            helper.sandbox._spawn(request_value, script_path, job_path, SimpleNamespace(procs=999))
        except ScriptDenied:
            assert not observed
            return ScriptResult("denied", "sandbox_unavailable", termination_confirmed=True)
        assert observed[-1]["/job/script.py"] == script
        if swap == "input":
            assert observed[-1]["/job/data"] == input_bytes
        return ScriptResult("observed_exit", "exited", 0, termination_confirmed=True)

    monkeypatch.setattr(subprocess, "Popen", fake_spawn)
    monkeypatch.setattr(helper.sandbox, "run", run)
    helper.execute_verified(value, script)


@pytest.mark.parametrize("stage", [0, 4])
def test_recovered_outcome_is_durably_audited(execution_env, stage):
    state = execution_env
    proposal = prepare(state)
    claimed = state.service._claim()
    assert claimed is not None
    with state.env.store._journal() as conn:
        conn.execute("UPDATE execution_runs SET stage=?", (stage,))
    state.env.clock.now = claimed[0].run_until + 1
    assert state.service._claim() is None
    row = run_row(state, proposal)
    assert f"execution.{row['phase']}.{row['reason']}" in actions(state.env, proposal["id"])


def test_preclaim_credential_denial_is_durably_audited(admission_state):
    state = admission_state
    credential = state.credentials[1]
    state.custody.revoke(credential.credential_id, credential.binding, state.context)
    assert state.service._claim() is None
    assert run_row(state, state.proposal) is None
    assert "execution.admission_denied" in actions(state.env, state.proposal["id"])
