"""Real helper lifecycle/pipes/child/files; synthetic cgroup and host qualification.

These tests do not claim namespace or kernel-cgroup confinement. The fixed test
child writes only into its private job and is killed through its own process group.
"""

import base64
import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from commonplace.compute import script_sandbox, target_helper
from commonplace.compute.scripts import ScriptDenied
from commonplace.compute.target_helper import TargetHelper
from tests.test_compute_script_sandbox import cancelled_storage_helper, config
from tests.test_compute_scripts import MemoryChannel, request

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX target lifecycle")
SCRIPT = b"print('fixture')\n"


class ProcessGroup:
    """Cgroup stand-in tracking one real process group, never a confinement proof."""

    def __init__(self, config, request):
        self.process = None
        self.empty_after_close = False

    def write(self, name, value):
        assert (name, value) == ("cgroup.kill", "1")
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def cpu_usec(self):
        return 0

    def empty(self):
        return self.process is None or self.process.poll() is not None

    def close(self):
        pass


@pytest.mark.parametrize("phase", ["before_spawn", "running"])
@pytest.mark.parametrize("fault", ["eof", "io_error", "cancel", "wrong_digest"])
def test_control_loss_confirms_empty_target_cleans_and_allows_next_admission(
    tmp_path, monkeypatch, phase, fault
):
    settings = config(tmp_path)
    root = Path(settings.workspace_root)
    helper = TargetHelper(settings, key_principal="c" * 64, authorize=lambda *a: True)
    monkeypatch.setattr(helper.sandbox, "check", lambda value: None)
    groups, jobs = [], []

    def group(config, value):
        result = ProcessGroup(config, value)
        groups.append(result)
        return result

    def spawn(value, script_path, job_path, group):
        jobs.append(Path(job_path))
        group.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                "import pathlib,signal,sys; "
                "print('COMPUTE_SCRIPT_STARTED_V1', flush=True); "
                "pathlib.Path(sys.argv[1], 'fill').write_bytes(b'x'*3000000); "
                "signal.pause()",
                str(job_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        return group.process

    monkeypatch.setattr(script_sandbox, "_Cgroup", group)
    monkeypatch.setattr(helper.sandbox, "_spawn", spawn)
    value = request()
    frames = [
        {"v": 1, "kind": "script_begin", "request": value.document(), "digest": value.digest},
        {"v": 1, "kind": "script_chunk", "seq": 0, "data": base64.b64encode(SCRIPT).decode()},
        {"v": 1, "kind": "script_commit", "digest": value.digest},
    ]
    if fault in ("cancel", "wrong_digest"):
        frames.append(
            {
                "v": 1,
                "kind": "script_cancel",
                "digest": value.digest if fault == "cancel" else "0" * 64,
            }
        )

    class Channel(MemoryChannel):
        disconnected = False

        def recv_ready(self):
            if phase == "before_spawn":
                return True
            return bool(
                jobs
                and (jobs[0] / "fill").exists()
                and (jobs[0] / "fill").stat().st_size == 3000000
            )

        def recv(self, count):
            if not self.incoming:
                self.disconnected = True
                if fault == "io_error":
                    raise OSError("synthetic control-channel failure")
            return super().recv(count)

        def sendall(self, raw):
            if self.disconnected:
                raise OSError("controller is gone")
            super().sendall(raw)

    channel = Channel(frames)
    helper.serve(channel)
    if phase == "running":
        assert groups[0].empty_after_close and groups[0].process.poll() is not None
    claim = next((root / "attempts").iterdir())
    if fault == "wrong_digest":
        if phase == "before_spawn":
            assert channel.sent[-1]["code"] == "binding_mismatch"
            assert not (claim / "result.json").exists()
        else:
            stored = json.loads((claim / "result.json").read_bytes())
            assert stored["state"] == "uncertain" and not stored["cancel_requested"]
            assert stored["termination_confirmed"] is False
        return
    assert (claim / "result.json").exists(), "control loss before spawn must persist a result"
    stored = json.loads((claim / "result.json").read_bytes())
    assert stored["termination_confirmed"] is True and stored["cancel_requested"] is True
    assert stored["state"] == ("cancelled" if phase == "before_spawn" else "observed_exit")
    assert list((root / "scratch").iterdir()) == []
    if fault in ("eof", "io_error"):
        assert not any(frame["kind"] == "script_result" for frame in channel.sent)
    later = value.expires_at + 3600
    monkeypatch.setattr(target_helper.time, "time", lambda: later)
    fresh = replace(value, execution="later", attempt="later", expires_at=later + 60)
    result = helper.execute_verified(fresh, SCRIPT, cancelled=lambda: True)
    assert result.termination_confirmed and not claim.exists()
    assert list((root / "scratch").iterdir()) == []


@pytest.mark.parametrize("phase", ["cgroup", "spawn"])
def test_no_process_spawned_confirms_nonrun_cleans_and_reclaims_claim(tmp_path, monkeypatch, phase):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)

    def unavailable(*args):
        raise OSError("synthetic pre-spawn failure")

    monkeypatch.setattr(
        script_sandbox, "_Cgroup", unavailable if phase == "cgroup" else ProcessGroup
    )
    monkeypatch.setattr(helper.sandbox, "_spawn", unavailable)
    value = request()
    result = helper.execute_verified(value, SCRIPT)
    assert result.state == "denied" and result.code == "sandbox_unavailable"
    assert result.termination_confirmed is True
    assert list((root / "scratch").iterdir()) == []
    claim = next((root / "attempts").iterdir())
    assert json.loads((claim / "result.json").read_bytes())["termination_confirmed"] is True
    later = value.expires_at + 3600
    monkeypatch.setattr(target_helper.time, "time", lambda: later)
    fresh = replace(value, execution="later", attempt="later", expires_at=later + 60)
    helper.execute_verified(fresh, SCRIPT, cancelled=lambda: True)
    assert not claim.exists()


def test_scratch_statistics_failure_denies_before_claim(tmp_path, monkeypatch):
    helper, root = cancelled_storage_helper(tmp_path, monkeypatch)

    def unreadable(fd):
        raise OSError("synthetic fstatvfs failure")

    monkeypatch.setattr(target_helper.os, "fstatvfs", unreadable)
    with pytest.raises(ScriptDenied, match="sandbox_unavailable"):
        helper.execute_verified(request(), SCRIPT, cancelled=lambda: True)
    assert list((root / "attempts").iterdir()) == []
    assert list((root / "scratch").iterdir()) == []
