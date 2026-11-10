"""Safe filesystem/process fixtures; no confinement-positive claim from fakes.

The final opt-in test requires separately provisioned disposable Linux resources;
only an absent fixture is skipped; provisioned failures fail. Deterministic upload tests bypass
only host qualification and cancel BEFORE spawn. They test atomic storage and
replay, never pretend that monkeypatched configuration provides confinement.
"""

import base64
import hashlib
import json
import os
import stat
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace.compute.protocol import ProtocolDenied
from commonplace.compute.script_sandbox import (
    BubblewrapSandbox,
    ScriptResult,
    ScriptTargetConfig,
    atomic_file,
    private_directory,
    read_regular_at,
)
from commonplace.compute.scripts import ScriptDenied, ScriptInput, ScriptManifest, ScriptRequest
from commonplace.compute.target_helper import (
    TargetHelper,
    _mkdir_at,
)
from tests.test_compute_scripts import MemoryChannel, binding, request

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux target filesystem contract")


def config(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir(mode=0o700)
    for name in ("attempts", "scratch", "inputs"):
        (root / name).mkdir(mode=0o700)
    return ScriptTargetConfig(
        enabled=True,
        workspace="ws_fixture",
        gateway="gateway",
        node="node",
        key_principal="c" * 64,
        workspace_root=str(root),
        scratch_root=str(root / "scratch"),
        runtime_root="/srv/compute-fixture/runtime",
        runtime_digest="b" * 64,
        cgroup_root="/sys/fs/cgroup/compute-fixture",
    )


def cancelled_storage_helper(tmp_path, monkeypatch):
    """Upload-only fixture: no process is run and no sandbox success is asserted."""
    settings = config(tmp_path)
    helper = TargetHelper(settings, key_principal="c" * 64, authorize=lambda *args: True)
    monkeypatch.setattr(helper.sandbox, "check", lambda value: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("fixture must not spawn"))
    return helper, Path(settings.workspace_root)


def test_disabled_and_root_hosts_deny_without_subprocess_or_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("unexpected subprocess"))
    helper = TargetHelper()
    value = request()
    channel = MemoryChannel(
        [
            {
                "v": 1,
                "kind": "script_begin",
                "request": value.document(),
                "digest": value.digest,
            }
        ]
    )
    helper.serve(channel)
    assert channel.sent == [{"v": 1, "kind": "script_error", "code": "sandbox_unavailable"}]
    assert list(tmp_path.iterdir()) == []
    settings = config(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        BubblewrapSandbox, "_check_host", lambda *a: pytest.fail("root must deny early")
    )
    with pytest.raises(ScriptDenied, match="sandbox_unavailable"):
        BubblewrapSandbox(settings).check(value.manifest)


def test_unprovisioned_configuration_never_spawns(tmp_path, monkeypatch):
    settings = config(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: 12345)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **kw: pytest.fail("unavailable must not spawn")
    )
    helper = TargetHelper(settings, key_principal="c" * 64, authorize=lambda *a: True)
    with pytest.raises(ScriptDenied, match="sandbox_unavailable|runtime_digest_mismatch"):
        helper.execute_verified(request(), b"print('fixture')\n")
    assert list((Path(settings.workspace_root) / "attempts").iterdir()) == []
    assert list((Path(settings.workspace_root) / "scratch").iterdir()) == []


@pytest.mark.parametrize(
    "change",
    [
        {"workspace": "foreign"},
        {"gateway": "foreign"},
        {"node": "foreign"},
    ],
)
def test_exact_binding_denial_before_filesystem(tmp_path, monkeypatch, change):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    value = request()
    value = replace(value, manifest=replace(value.manifest, binding=binding(**change)))
    with pytest.raises(ScriptDenied, match="binding_mismatch"):
        helper.execute_verified(value, b"print('fixture')\n")
    assert list((root / "attempts").iterdir()) == []


def test_wrong_key_authority_and_expiry_are_independent(tmp_path, monkeypatch):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    value = request()
    helper.key_principal = "wrong"
    with pytest.raises(ScriptDenied, match="binding_mismatch"):
        helper.execute_verified(value, b"print('fixture')\n")
    helper.key_principal = "c" * 64
    helper.authorize = lambda *args: False
    with pytest.raises(ScriptDenied, match="script_authority_denied"):
        helper.execute_verified(value, b"print('fixture')\n")
    helper.authorize = lambda *args: True
    with pytest.raises(ScriptDenied, match="admission_expired"):
        helper.execute_verified(replace(value, expires_at=time.time() - 1), b"print('fixture')\n")
    assert list((root / "attempts").iterdir()) == []


def test_digest_tampering_precedes_all_upload_effects(tmp_path, monkeypatch):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    with pytest.raises(ScriptDenied, match="script_digest_mismatch"):
        helper.execute_verified(request(), b"print('changed')\n")
    assert list((root / "attempts").iterdir()) == []
    value = request()
    wrong = base64.b64encode(b"x" * value.manifest.script_size).decode()
    channel = MemoryChannel(
        [
            {"v": 1, "kind": "script_begin", "request": value.document(), "digest": value.digest},
            {"v": 1, "kind": "script_chunk", "seq": 0, "data": wrong},
            {"v": 1, "kind": "script_commit", "digest": value.digest},
        ]
    )
    helper.serve(channel)
    assert channel.sent[-1]["code"] == "script_digest_mismatch"
    assert list((root / "attempts").iterdir()) == []


def test_private_atomic_verbatim_upload_cancel_replay_and_reopen(tmp_path, monkeypatch):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    script = b"# exact\r\nprint('fixture')\n"
    value = request(script)
    result = helper.execute_verified(value, script, cancelled=lambda: True)
    assert result.state == "cancelled" and result.exit_code is None
    assert result.cancel_requested and result.termination_confirmed
    claim = root / "attempts" / hashlib.sha256(value.execution.encode()).hexdigest()
    assert stat.S_IMODE(claim.stat().st_mode) == 0o700
    assert (claim / "script.py").read_bytes() == script
    assert stat.S_IMODE((claim / "script.py").stat().st_mode) == 0o600
    assert json.loads((claim / "claim.json").read_bytes())["digest"] == value.digest
    assert list((root / "scratch").iterdir()) == []
    restored = TargetHelper(helper.config, key_principal="c" * 64, authorize=lambda *a: True)
    monkeypatch.setattr(restored.sandbox, "check", lambda value: None)
    for attempt in (value, replace(value, attempt="new-attempt")):
        with pytest.raises(ScriptDenied, match="replay_denied"):
            restored.execute_verified(attempt, script, cancelled=lambda: True)
    assert len(list((root / "attempts").iterdir())) == 1


def test_partial_restored_claim_never_reruns_or_frees_capacity(tmp_path, monkeypatch):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    value = request()
    claim = root / "attempts" / hashlib.sha256(value.execution.encode()).hexdigest()
    claim.mkdir(mode=0o700)  # Simulated crash immediately after durable mkdir.
    with pytest.raises(ScriptDenied, match="replay_denied"):
        helper.execute_verified(value, b"print('fixture')\n", cancelled=lambda: True)
    assert claim.is_dir() and list(claim.iterdir()) == []


def test_preexisting_symlink_and_hardlink_are_not_followed(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    (directory / "linked").symlink_to(outside)
    (directory / "dirlink").symlink_to(tmp_path, target_is_directory=True)
    os.link(outside, directory / "hardlinked")
    fd = private_directory(directory)
    try:
        for name in ("linked", "dirlink/outside", "hardlinked"):
            with pytest.raises((OSError, ScriptDenied)):
                read_regular_at(fd, name, 100)
        with pytest.raises(ScriptDenied, match="atomic_upload_denied"):
            atomic_file(fd, "linked", b"replacement")
        with pytest.raises(ScriptDenied, match="invalid_path"):
            atomic_file(fd, "../outside", b"replacement")
    finally:
        os.close(fd)
    assert outside.read_bytes() == b"untouched"


def test_directory_replacement_keeps_openat_anchor(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    renamed = tmp_path / "original"
    directory.rename(renamed)
    directory.symlink_to(tmp_path, target_is_directory=True)
    try:
        atomic_file(fd, "new", b"anchored")
    finally:
        os.close(fd)
    assert (renamed / "new").read_bytes() == b"anchored"
    assert not (tmp_path / "new").exists()


def test_no_overwrite_even_with_simultaneous_atomic_destination(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    try:
        atomic_file(fd, "name", b"first")
        with pytest.raises(ScriptDenied, match="atomic_upload_denied"):
            atomic_file(fd, "name", b"second")
        assert read_regular_at(fd, "name", 100) == b"first"
    finally:
        os.close(fd)


def test_inputs_exact_hash_and_output_symlink_denial(tmp_path):
    settings = config(tmp_path)
    root = private_directory(settings.workspace_root)
    scratch = private_directory(settings.scratch_root)
    job = _mkdir_at(scratch, "fixture-job")
    source = Path(settings.workspace_root) / "inputs" / "source.txt"
    source.write_bytes(b"original")
    item = ScriptInput("source.txt", hashlib.sha256(b"original").hexdigest(), 8)
    value = request(input_paths=(item,), output_paths=("result.txt",))
    try:
        TargetHelper._stage_inputs(root, job, value)
        assert read_regular_at(job, "source.txt", 8) == b"original"
        os.symlink(str(source), "result.txt", dir_fd=job)
        with pytest.raises((OSError, ScriptDenied)):
            TargetHelper._artifacts(job, value)
        source.write_bytes(b"tampered")
        with pytest.raises(ScriptDenied, match="input_digest_mismatch"):
            TargetHelper._stage_inputs(root, job, value)
        assert source.read_bytes() == b"tampered"
    finally:
        os.close(job)
        os.close(scratch)
        os.close(root)


def test_caller_json_cannot_select_roots_runtime_environment_or_privilege(tmp_path):
    helper = TargetHelper(config(tmp_path), key_principal="c" * 64, authorize=lambda *a: True)
    value = request()
    payload = value.document()
    payload["workspace_root"] = "/"
    channel = MemoryChannel(
        [{"v": 1, "kind": "script_begin", "request": payload, "digest": value.digest}]
    )
    helper.serve(channel)
    assert channel.sent[-1] == {"v": 1, "kind": "script_error", "code": "invalid_fields"}
    payload = value.document()
    payload["manifest"]["privilege"] = "root"
    with pytest.raises(ScriptDenied, match="unsupported_operation"):
        ScriptRequest.from_dict(payload)


@pytest.mark.parametrize("version,seq", [(True, 0), (2, 0), (1, True), (1, 1)])
def test_strict_frame_version_and_chunk_order(tmp_path, monkeypatch, version, seq):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    value = request()
    channel = MemoryChannel(
        [
            {
                "v": version,
                "kind": "script_begin",
                "request": value.document(),
                "digest": value.digest,
            },
            {
                "v": 1,
                "kind": "script_chunk",
                "seq": seq,
                "data": base64.b64encode(b"print('fixture')\n").decode(),
            },
            {"v": 1, "kind": "script_commit", "digest": value.digest},
        ]
    )
    helper.serve(channel)
    assert channel.sent[-1]["kind"] == "script_error"
    assert list((root / "attempts").iterdir()) == []


def test_literal_argv_clean_environment_and_mandatory_namespace_flags(tmp_path):
    settings = config(tmp_path)
    value = request(arguments=("; touch /host", "$(id)", "a b", "--"))
    args = BubblewrapSandbox(settings).argv(value, "/private/script.py", "/private/job")
    assert args[0] == "/usr/bin/bwrap"
    assert args[-4:] == list(value.manifest.arguments)
    assert {
        "--unshare-all",
        "--disable-userns",
        "--assert-userns-disabled",
        "--clearenv",
        "--cap-drop",
        "--die-with-parent",
    } <= set(args)
    assert "--share-net" not in args and "--share-pid" not in args
    assert args[args.index("--ro-bind") + 1 : args.index("--ro-bind") + 3] == [
        settings.runtime_root,
        "/",
    ]
    assert all(value not in args for value in ("/home", "/etc", "/run", "/sys"))


def test_spawn_has_real_descendant_cgroup_preexec_and_no_shell(tmp_path, monkeypatch):
    settings = config(tmp_path)
    seen = {}

    def fixture_popen(args, **kwargs):
        seen.update(kwargs)
        return "process fixture"

    monkeypatch.setattr(subprocess, "Popen", fixture_popen)
    result = BubblewrapSandbox(settings)._spawn(
        request(),
        "/private/script.py",
        "/private/job",
        SimpleNamespace(procs=999),
    )
    assert result == "process fixture"
    assert seen["shell"] is False and seen["env"] == {}
    assert seen["close_fds"] and seen["start_new_session"]
    assert seen["pass_fds"] == (999,) and callable(seen["preexec_fn"])


@pytest.mark.parametrize("scenario", ["exit", "cancel", "timeout", "output", "unstarted"])
def test_finite_process_fixture_observed_exit_cancel_timeout_and_output(
    tmp_path, monkeypatch, scenario
):
    """Synthetic pipe/process fixture exercises collection, never OS confinement."""
    import commonplace.compute.script_sandbox as module

    settings = config(tmp_path)
    chunks = {
        100: [b"COMPUTE_SCRIPT_STARTED_V1\n", b"result", b""],
        101: [b""],
    }
    if scenario == "output":
        chunks[100] = [b"COMPUTE_SCRIPT_STARTED_V1\n", b"x" * 4096, b"x" * 4096, b""]
    if scenario == "unstarted":
        chunks[100] = [b""]
        chunks[101] = [b"private launcher error", b""]

    class Stream:
        def __init__(self, fd):
            self.fd = fd

        def fileno(self):
            return self.fd

    class Process:
        stdout, stderr = Stream(100), Stream(101)
        returncode = None

        def poll(self):
            if scenario in {"exit", "output", "unstarted"} and not any(chunks.values()):
                self.returncode = 7
            return self.returncode

    process = Process()

    class Group:
        kills = 0

        def write(self, name, value):
            assert (name, value) == ("cgroup.kill", "1")
            self.kills += 1
            if process.returncode is None:
                process.returncode = -9

        @staticmethod
        def cpu_usec():
            return 0

        @staticmethod
        def empty():
            return True

    class Selector:
        def __init__(self):
            self.keys = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def register(self, stream, events, name):
            self.keys[stream.fd] = SimpleNamespace(fileobj=stream, data=name)

        def unregister(self, stream):
            del self.keys[stream.fd]

        def get_map(self):
            return self.keys

        def select(self, timeout):
            return [(key, 1) for key in self.keys.values()]

    clock = [0.0, 0]

    def monotonic():
        clock[1] += 1
        clock[0] += 1.1 if scenario == "timeout" and clock[1] == 2 else 0.01
        return clock[0]

    monkeypatch.setattr(module.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(module.os, "set_blocking", lambda *a: None)
    monkeypatch.setattr(module.os, "read", lambda fd, size: chunks[fd].pop(0))
    monkeypatch.setattr(module.time, "monotonic", monotonic)
    group = Group()
    value = request()
    if scenario == "timeout":
        value = replace(
            value,
            manifest=replace(value.manifest, limits=replace(value.manifest.limits, wall_seconds=1)),
        )
    result = BubblewrapSandbox(settings)._collect(
        value, process, group, lambda: scenario == "cancel"
    )
    assert group.kills >= 1
    if scenario == "unstarted":
        assert result.state == "denied" and result.exit_code is None
        assert result.stdout == result.stderr == b""
    else:
        assert result.state == "observed_exit" and result.termination_confirmed
        assert result.cancel_requested is (scenario == "cancel")
        assert len(result.stdout) + len(result.stderr) <= value.manifest.limits.output_bytes
        assert (
            result.code
            == {
                "exit": "exited",
                "cancel": "cancel_requested",
                "timeout": "runtime_limit",
                "output": "output_limit",
            }[scenario]
        )


def test_target_capacity_keeps_unknown_preexisting_data_untouched(tmp_path, monkeypatch):
    import commonplace.compute.target_helper as module

    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "MAX_TARGET_ATTEMPTS", 1)
    old = root / "attempts" / "preexisting-unknown-claim"
    old.mkdir(mode=0o700)
    (old / "evidence").write_bytes(b"keep forever")
    with pytest.raises(ScriptDenied, match="target_capacity"):
        helper.execute_verified(request(), b"print('fixture')\n", cancelled=lambda: True)
    assert (old / "evidence").read_bytes() == b"keep forever"


def test_target_reclaims_64_expired_finished_claims(tmp_path, monkeypatch):
    import commonplace.compute.target_helper as module

    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    value = request()
    now = value.expires_at - 60
    monkeypatch.setattr(module.time, "time", lambda: now)
    for n in range(64):
        old = replace(value, execution=f"old-{n}", attempt=f"old-attempt-{n}")
        helper.execute_verified(old, b"print('fixture')\n", cancelled=lambda: True)
    assert len(list((root / "attempts").iterdir())) == 64
    monkeypatch.setattr(module.time, "time", lambda: now + 3600)
    current = replace(value, execution="new", attempt="new", expires_at=now + 3660)
    result = helper.execute_verified(current, b"print('fixture')\n", cancelled=lambda: True)
    assert result.state == "cancelled" and result.termination_confirmed
    assert len(list((root / "attempts").iterdir())) == 1
    with pytest.raises(ScriptDenied, match="admission_expired"):
        helper.execute_verified(value, b"print('fixture')\n", cancelled=lambda: True)


@pytest.mark.parametrize("finished", [False, True])
def test_target_keeps_claims_without_result_and_recent_or_live_claims(
    tmp_path, monkeypatch, finished
):
    import commonplace.compute.target_helper as module

    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    value = request()
    now = value.expires_at - 60
    monkeypatch.setattr(module.time, "time", lambda: now)
    helper.execute_verified(value, b"print('fixture')\n", cancelled=lambda: True)
    claim = next((root / "attempts").iterdir())
    if not finished:
        (claim / "result.json").unlink()
    with pytest.raises(ScriptDenied, match="replay_denied"):
        helper.execute_verified(value, b"print('fixture')\n", cancelled=lambda: True)
    for elapsed in (61, 3600):
        monkeypatch.setattr(module.time, "time", lambda elapsed=elapsed: now + elapsed)
        other = replace(
            value,
            execution=f"new-{elapsed}",
            attempt=f"new-{elapsed}",
            expires_at=now + elapsed + 60,
        )
        helper.execute_verified(other, b"print('fixture')\n", cancelled=lambda: True)
        assert claim.exists() is (not finished or elapsed == 61)


@pytest.mark.parametrize("tree", ["mode_zero", "read_only", "deep"])
def test_script_owned_cleanup_cannot_destroy_observed_result(tmp_path, monkeypatch, tree):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)
    deepest = None
    observed = ScriptResult(
        "observed_exit",
        "exited",
        7,
        termination_confirmed=True,
        stdout=b"observed stdout",
        stderr=b"observed stderr",
    )

    def run(value, script, job, **kwargs):
        nonlocal deepest
        directory = os.open(job, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for _ in range(1100 if tree == "deep" else 1):
                os.mkdir("d", mode=0o700, dir_fd=directory)
                child = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=directory)
                os.close(directory)
                directory = child
            atomic_file(directory, "file", b"script-owned")
            if tree != "deep":
                os.fchmod(directory, 0 if tree == "mode_zero" else 0o500)
            deepest = os.dup(directory)
        finally:
            os.close(directory)
        return observed

    monkeypatch.setattr(helper.sandbox, "run", run)
    try:
        result = helper.execute_verified(request(), b"print('fixture')\n")
        assert result == observed
        stored = json.loads(
            next((root / "attempts").iterdir()).joinpath("result.json").read_bytes()
        )
        assert stored["state"] == "observed_exit" and stored["exit_code"] == 7
    finally:
        # Restore/remove a deliberately hostile fixture even when the baseline
        # loses its result. Walk upwards with bounded descriptors, not recursion.
        if deepest is not None:
            try:
                os.fchmod(deepest, 0o700)
                try:
                    os.unlink("file", dir_fd=deepest)
                except FileNotFoundError:
                    pass
                for _ in range(1100 if tree == "deep" else 1):
                    parent = os.open("..", os.O_RDONLY | os.O_DIRECTORY, dir_fd=deepest)
                    os.close(deepest)
                    deepest = parent
                    os.rmdir("d", dir_fd=deepest)
            except FileNotFoundError:
                pass
            finally:
                os.close(deepest)


def test_cleanup_io_failure_preserves_observed_result(tmp_path, monkeypatch):
    import commonplace.compute.target_helper as module

    helper, _ = cancelled_storage_helper(tmp_path, monkeypatch)
    observed = ScriptResult("observed_exit", "exited", 0, termination_confirmed=True, stdout=b"ok")
    monkeypatch.setattr(helper.sandbox, "run", lambda *args, **kwargs: observed)

    def unavailable(*args, **kwargs):
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(module, "_remove_owned_job", unavailable)
    assert helper.execute_verified(request(), b"print('fixture')\n") == observed


def test_cancel_frame_is_intent_without_fabricated_exit(tmp_path, monkeypatch):
    helper, _ = cancelled_storage_helper(tmp_path, monkeypatch)
    value = request()
    channel = MemoryChannel(
        [
            {"v": 1, "kind": "script_begin", "request": value.document(), "digest": value.digest},
            {
                "v": 1,
                "kind": "script_chunk",
                "seq": 0,
                "data": base64.b64encode(b"print('fixture')\n").decode(),
            },
            {"v": 1, "kind": "script_commit", "digest": value.digest},
            {"v": 1, "kind": "script_cancel", "digest": value.digest},
        ]
    )
    helper.serve(channel)
    result = next(x["result"] for x in channel.sent if x["kind"] == "script_result")
    assert result["state"] == "cancelled" and result["exit_code"] is None
    assert result["cancel_requested"] and result["termination_confirmed"]


def test_helper_errors_never_publish_raw_exception_text(tmp_path, monkeypatch):
    helper, _ = cancelled_storage_helper(tmp_path, monkeypatch)
    monkeypatch.setattr(
        helper.sandbox,
        "check",
        lambda *args: (_ for _ in ()).throw(
            OSError("synthetic SECRET script output"),
        ),
    )
    value = request()
    channel = MemoryChannel(
        [
            {
                "v": 1,
                "kind": "script_begin",
                "request": value.document(),
                "digest": value.digest,
            }
        ]
    )
    helper.serve(channel)
    assert "SECRET" not in json.dumps(channel.sent)
    assert channel.sent[-1]["code"] == "helper_unavailable"


def test_optional_actual_disposable_confined_script(tmp_path):
    """No auto-provisioning/probing: opt in only with a disposable operator setup."""
    config_path = os.environ.get("COMMONPLACE_COMPUTE_SANDBOX_FIXTURE")
    if not config_path:
        pytest.skip("no explicitly provisioned disposable cgroup/tmpfs/read-only runtime fixture")
    settings = ScriptTargetConfig(**json.loads(Path(config_path).read_text()))
    if settings.workspace != "fixture-script-workspace":
        pytest.fail("fixture must use the reserved disposable workspace identity")
    script = (
        b"import os,sys\n"
        b"assert os.getuid()!=0\n"
        b"assert os.environ.get('SSH_AUTH_SOCK') is None\n"
        b"assert not os.path.exists(sys.argv[1])\n"
        b"assert sys.argv[2]=='; literal $(id)'\n"
        b"open('result.txt','wb').write(b'confined artifact')\n"
        b"print('confined result')\n"
    )
    sentinel = tmp_path / "host-private.txt"
    sentinel.write_bytes(b"host data must remain private and unchanged")
    current = replace(
        binding(), workspace=settings.workspace, gateway=settings.gateway, node=settings.node
    )
    value = ScriptManifest.create(
        current,
        "disposable-bundle",
        script,
        runtime_digest=settings.runtime_digest,
        arguments=(str(sentinel), "; literal $(id)"),
        output_paths=("result.txt",),
    )
    BubblewrapSandbox(settings).check(value)
    attempt = "fixture-" + os.urandom(8).hex()
    execution = ScriptRequest(attempt, attempt, value, time.time() + 60)
    result = TargetHelper(
        settings, key_principal=settings.key_principal, authorize=lambda *a: True
    ).execute_verified(execution, script)
    assert result.state == "observed_exit" and result.exit_code == 0, (
        f"provisioned sandbox execution failed: {result.state}/{result.code}"
    )
    assert result.termination_confirmed and result.stdout == b"confined result\n"
    assert result.artifacts == (("result.txt", b"confined artifact"),)
    assert sentinel.read_bytes() == b"host data must remain private and unchanged"


@pytest.mark.parametrize("phase", ["preflight", "launch"])
def test_provisioned_positive_fixture_denials_fail(tmp_path, monkeypatch, phase):
    """A supplied fixture must never turn a broken sandbox into a skipped check."""
    settings = replace(config(tmp_path), workspace="fixture-script-workspace")
    config_path = tmp_path / "fixture.json"
    config_path.write_text(json.dumps(asdict(settings)))
    monkeypatch.setenv("COMMONPLACE_COMPUTE_SANDBOX_FIXTURE", str(config_path))
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **kw: pytest.fail("regression fixture must not spawn")
    )

    def preflight(self, manifest):
        if phase == "preflight":
            raise ProtocolDenied("sandbox_unavailable")

    monkeypatch.setattr(BubblewrapSandbox, "check", preflight)
    monkeypatch.setattr(
        TargetHelper,
        "execute_verified",
        lambda *a: SimpleNamespace(state="denied", code="sandbox_unavailable"),
    )
    error = ProtocolDenied if phase == "preflight" else AssertionError
    with pytest.raises(error, match="sandbox_unavailable"):
        test_optional_actual_disposable_confined_script(tmp_path)
