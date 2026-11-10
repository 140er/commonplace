"""Separately provisioned target helper and strict chunked script wire contract.

TARGET_HELPER_COMMAND is a literal SSH exec command, used only after the existing
independently pinned inner SSH handshake. No paths/arguments/env enter that
command. This module adds no transport route or installation. An operator must
provision a dedicated account, exclusive controller key (ExposeAuthInfo yes),
protected fixed configuration, private workspace/{attempts,inputs,scratch},
read-only runtime and delegated cgroups. Everything is disabled by default.

API: TargetHelper(config, key_principal=..., authorize=...).serve(channel).
authorize(request, phase) must return True at begin/commit; the parent controller
must already have consumed the exact current task approval/fenced SSH permit.
key_principal comes from SSH authentication, NEVER from caller JSON. The fixed
CLI checks sshd's public authentication-info file against protected local config.

Wire: existing length-prefixed JSON (8KiB/frame), v=1, <=64 frames/192KiB/40s.
script_begin {request:ScriptRequest.document(),digest:request.digest} ->
script_ready {digest} -> script_chunk {seq:int,data:base64 <=3072 raw bytes}*
-> script_commit {digest} -> script_result {digest,result:metadata} ->
script_data {stream,path,seq,data}* -> script_done {digest}.
All frames include v/kind, exact fields only. A script_cancel {digest} may be
sent after commit on the same authenticated channel; cancellation is intent
until observed exit and cgroup emptiness. Partial/lost frames never cause retry.
Raw output/artifacts are private controller data; use scripts.project_result.

ScriptWire, send_bundle, receive_result, cancel_frame are parent integration
helpers. No private key or filesystem root is representable in this payload.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import select
import stat
import sys
import time
from dataclasses import replace
from pathlib import Path

from .protocol import FrameIO, ProtocolDenied, canonical, exact_fields, finite
from .script_sandbox import (
    BubblewrapSandbox,
    ScriptResult,
    ScriptTargetConfig,
    atomic_file,
    private_directory,
    read_regular_at,
)
from .scripts import ScriptDenied, ScriptRequest, bounded_integer

TARGET_HELPER_COMMAND = "/usr/bin/python3 -I -m commonplace.compute.target_helper"
TARGET_CONFIG_PATH = "/etc/commonplace/compute-script-target-v1.json"
MAX_TARGET_ATTEMPTS = 64
CLAIM_EXPIRY_GRACE_SECONDS = 300
MAX_WIRE_BYTES = 196608
MAX_WIRE_FRAMES = 64
CHUNK_BYTES = 3072
_CODES = frozenset(
    {
        "invalid_fields",
        "unsupported_protocol",
        "invalid_frame",
        "invalid_frame_size",
        "invalid_json",
        "duplicate_field",
        "invalid_identifier",
        "invalid_digest",
        "invalid_time",
        "invalid_limit",
        "invalid_manifest",
        "invalid_script",
        "invalid_arguments",
        "invalid_path",
        "path_overlap",
        "manifest_too_large",
        "unsupported_operation",
        "unsupported_admission_mode",
        "script_digest_mismatch",
        "script_authority_denied",
        "binding_mismatch",
        "admission_expired",
        "sandbox_unavailable",
        "runtime_digest_mismatch",
        "replay_denied",
        "target_capacity",
        "unsafe_workspace",
        "unsafe_file",
        "file_changed",
        "atomic_upload_denied",
        "input_digest_mismatch",
        "upload_sequence",
        "wire_limit",
        "deadline_exceeded",
        "control_disconnected",
        "tunnel_budget_exceeded",
        "cancelled_before_start",
        "exited",
        "runtime_limit",
        "output_limit",
        "cancel_requested",
        "termination_unconfirmed",
        "artifact_denied",
        "helper_unavailable",
    }
)


def _fixed_code(error):
    return str(error) if str(error) in _CODES else "helper_unavailable"


def _message(value, kind, fields):
    exact_fields(value, {"v", "kind", *fields})
    if type(value["v"]) is not int or value["v"] != 1 or value["kind"] != kind:
        raise ScriptDenied("unsupported_protocol")
    return value


def _decode_chunk(value):
    if type(value) is not str or not 1 <= len(value) <= CHUNK_BYTES * 4 // 3:
        raise ScriptDenied("wire_limit")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ScriptDenied("invalid_frame") from None
    if not 1 <= len(raw) <= CHUNK_BYTES or base64.b64encode(raw).decode("ascii") != value:
        raise ScriptDenied("invalid_frame")
    return raw


class ScriptWire:
    def __init__(self, channel, *, deadline=None):
        self.io = FrameIO(
            channel,
            time.monotonic() + 40 if deadline is None else deadline,
            byte_limit=MAX_WIRE_BYTES,
        )
        self.frames = 0

    def _charge(self):
        self.frames += 1
        if self.frames > MAX_WIRE_FRAMES:
            raise ScriptDenied("wire_limit")

    def send(self, kind, **fields):
        self._charge()
        self.io.send({"v": 1, "kind": kind, **fields})

    def receive(self):
        self._charge()
        return self.io.receive()


def cancel_frame(request):
    return {"v": 1, "kind": "script_cancel", "digest": request.digest}


def _receive(wire):
    value = wire.receive()
    if value.get("kind") == "script_error":
        _message(value, "script_error", {"code"})
        if type(value["code"]) is not str or value["code"] not in _CODES:
            raise ScriptDenied("invalid_frame")
        raise ScriptDenied(value["code"])
    return value


def send_bundle(wire, request, script):
    request.manifest.verify(script)
    wire.send("script_begin", request=request.document(), digest=request.digest)
    ready = _message(_receive(wire), "script_ready", {"digest"})
    if ready["digest"] != request.digest:
        raise ScriptDenied("binding_mismatch")
    for seq, offset in enumerate(range(0, len(script), CHUNK_BYTES)):
        wire.send(
            "script_chunk",
            seq=seq,
            data=base64.b64encode(
                script[offset : offset + CHUNK_BYTES],
            ).decode("ascii"),
        )
    wire.send("script_commit", digest=request.digest)


def _result_metadata(result):
    return {
        "state": result.state,
        "code": result.code,
        "exit_code": result.exit_code,
        "cancel_requested": result.cancel_requested,
        "termination_confirmed": result.termination_confirmed,
        "stdout": len(result.stdout),
        "stderr": len(result.stderr),
        "artifacts": [
            {"path": path, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for path, raw in result.artifacts
        ],
    }


def _send_result(wire, request, result):
    wire.send("script_result", digest=request.digest, result=_result_metadata(result))
    values = [("stdout", "", result.stdout), ("stderr", "", result.stderr)]
    values += [("artifact", path, raw) for path, raw in result.artifacts]
    events = 0
    for stream, path, raw in values:
        for seq, offset in enumerate(range(0, len(raw), CHUNK_BYTES)):
            events += 1
            if events > request.manifest.limits.events:
                raise ScriptDenied("wire_limit")
            wire.send(
                "script_data",
                stream=stream,
                path=path,
                seq=seq,
                data=base64.b64encode(raw[offset : offset + CHUNK_BYTES]).decode("ascii"),
            )
    wire.send("script_done", digest=request.digest)


def receive_result(wire, request):
    value = _message(_receive(wire), "script_result", {"digest", "result"})
    if value["digest"] != request.digest:
        raise ScriptDenied("binding_mismatch")
    meta = value["result"]
    exact_fields(
        meta,
        {
            "state",
            "code",
            "exit_code",
            "cancel_requested",
            "termination_confirmed",
            "stdout",
            "stderr",
            "artifacts",
        },
    )
    if (
        meta["state"] not in ("denied", "cancelled", "observed_exit", "uncertain")
        or type(meta["code"]) is not str
        or meta["code"] not in _CODES
        or type(meta["cancel_requested"]) is not bool
        or type(meta["termination_confirmed"]) is not bool
    ):
        raise ScriptDenied("invalid_frame")
    exit_code = meta["exit_code"]
    if exit_code is not None:
        bounded_integer(exit_code, -255, 255)
    if (meta["state"] == "observed_exit" and exit_code is None) or (
        meta["state"] in ("denied", "cancelled") and exit_code is not None
    ):
        raise ScriptDenied("invalid_frame")
    limits = request.manifest.limits
    for name in ("stdout", "stderr"):
        bounded_integer(meta[name], 0, limits.output_bytes)
    if meta["stdout"] + meta["stderr"] > limits.output_bytes:
        raise ScriptDenied("wire_limit")
    if type(meta["artifacts"]) is not list or len(meta["artifacts"]) > len(
        request.manifest.output_paths
    ):
        raise ScriptDenied("invalid_frame")
    specs = [("stdout", "", meta["stdout"], None), ("stderr", "", meta["stderr"], None)]
    seen, total = set(), 0
    for item in meta["artifacts"]:
        exact_fields(item, {"path", "size", "sha256"})
        if (
            type(item["path"]) is not str
            or item["path"] not in request.manifest.output_paths
            or item["path"] in seen
        ):
            raise ScriptDenied("invalid_frame")
        seen.add(item["path"])
        total += bounded_integer(item["size"], 0, limits.artifacts_bytes)
        if total > limits.artifacts_bytes:
            raise ScriptDenied("wire_limit")
        specs.append(("artifact", item["path"], item["size"], item["sha256"]))
    buffers, events = [], 0
    for stream, path, size, sha256 in specs:
        raw, seq = bytearray(), 0
        while len(raw) < size:
            events += 1
            if events > limits.events:
                raise ScriptDenied("wire_limit")
            part = _message(_receive(wire), "script_data", {"stream", "path", "seq", "data"})
            if (part["stream"], part["path"], part["seq"]) != (stream, path, seq) or type(
                part["seq"]
            ) is not int:
                raise ScriptDenied("upload_sequence")
            chunk = _decode_chunk(part["data"])
            if len(raw) + len(chunk) > size:
                raise ScriptDenied("wire_limit")
            raw.extend(chunk)
            seq += 1
        if sha256 is not None and hashlib.sha256(raw).hexdigest() != sha256:
            raise ScriptDenied("script_digest_mismatch")
        buffers.append(bytes(raw))
    done = _message(_receive(wire), "script_done", {"digest"})
    if done["digest"] != request.digest:
        raise ScriptDenied("binding_mismatch")
    return ScriptResult(
        meta["state"],
        meta["code"],
        exit_code,
        meta["cancel_requested"],
        meta["termination_confirmed"],
        buffers[0],
        buffers[1],
        tuple((spec[1], raw) for spec, raw in zip(specs[2:], buffers[2:])),
    )


def _mkdir_at(parent, name):
    os.mkdir(name, 0o700, dir_fd=parent)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    os.fchmod(fd, 0o700)
    os.fsync(parent)
    return fd


def _write_job_file(job, path, data):
    directory = os.dup(job)
    try:
        parts = path.split("/")
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        atomic_file(directory, parts[-1], data)
    finally:
        os.close(directory)


def _remove_owned_job(directory, *, remaining=4096, depth=64):
    """Remove an owned tree after the caller has ruled out a running script."""
    if depth <= 0:
        raise ScriptDenied("target_capacity")
    os.fchmod(directory, 0o700)
    with os.scandir(directory) as entries:
        for entry in entries:
            remaining -= 1
            if remaining < 0:
                raise ScriptDenied("target_capacity")
            if entry.is_dir(follow_symlinks=False):
                # The script owns these directories and may have removed every
                # permission. Restore access before opening, then fchmod the fd.
                os.chmod(entry.name, 0o700, dir_fd=directory, follow_symlinks=False)
                child = os.open(
                    entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                try:
                    remaining = _remove_owned_job(child, remaining=remaining, depth=depth - 1)
                finally:
                    os.close(child)
                os.rmdir(entry.name, dir_fd=directory)
            else:
                os.unlink(entry.name, dir_fd=directory)
    return remaining


def _prune_finished_claim(attempts, name, now):
    """Called under the attempts lock; unknown or unfinished claims stay put."""
    claim = None
    try:
        claim = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=attempts)
        document = json.loads(read_regular_at(claim, "claim.json", 8192, uid=os.geteuid()))
        expires_at = finite(document["expires_at"])
        if now <= expires_at + CLAIM_EXPIRY_GRACE_SECONDS:
            return False
        # Read through the same protected regular-file checks, not just exists().
        read_regular_at(claim, "result.json", 8192, uid=os.geteuid())
        _remove_owned_job(claim)
        os.rmdir(name, dir_fd=attempts)
        os.fsync(attempts)
        return True
    except (OSError, ProtocolDenied, ValueError, KeyError, TypeError):
        return False
    finally:
        if claim is not None:
            os.close(claim)


class TargetHelper:
    def __init__(self, config=None, *, key_principal=None, authorize=None):
        self.config = ScriptTargetConfig() if config is None else config
        self.key_principal = key_principal
        self.authorize = authorize
        # Deliberately concrete: no caller-injected fake execution adapter.
        self.sandbox = BubblewrapSandbox(self.config)

    def _validate(self, request, phase):
        binding = request.manifest.binding
        if not self.config.enabled:
            raise ScriptDenied("sandbox_unavailable")
        if (
            binding.workspace,
            binding.gateway,
            binding.node,
            self.key_principal,
            request.controller_contract,
        ) != (
            self.config.workspace,
            self.config.gateway,
            self.config.node,
            self.config.key_principal,
            self.config.controller_contract,
        ):
            raise ScriptDenied("binding_mismatch")
        now = time.time()
        if not now < request.expires_at <= now + 300:
            raise ScriptDenied("admission_expired")
        if self.authorize is None or self.authorize(request, phase) is not True:
            raise ScriptDenied("script_authority_denied")
        self.sandbox.check(request.manifest)

    def serve(self, channel, *, cancelled=lambda: False):
        wire = ScriptWire(channel)
        try:
            self._serve(wire, cancelled)
        except Exception:
            error = sys.exc_info()[1]
            try:
                wire.send("script_error", code=_fixed_code(error))
            except (ProtocolDenied, OSError, ValueError):
                pass
        # Never print tracebacks/script/output to ssh stderr or logs.

    def _serve(self, wire, cancelled):
        begin = _message(wire.receive(), "script_begin", {"request", "digest"})
        request = ScriptRequest.from_dict(begin["request"])
        if begin["digest"] != request.digest:
            raise ScriptDenied("binding_mismatch")
        self._validate(request, "begin")
        wire.send("script_ready", digest=request.digest)
        script, seq = bytearray(), 0
        while len(script) < request.manifest.script_size:
            chunk = _message(wire.receive(), "script_chunk", {"seq", "data"})
            if type(chunk["seq"]) is not int or chunk["seq"] != seq:
                raise ScriptDenied("upload_sequence")
            raw = _decode_chunk(chunk["data"])
            if len(script) + len(raw) > request.manifest.script_size:
                raise ScriptDenied("wire_limit")
            script.extend(raw)
            seq += 1
        commit = _message(wire.receive(), "script_commit", {"digest"})
        if commit["digest"] != request.digest:
            raise ScriptDenied("binding_mismatch")
        # Independently verify exact bytes BEFORE journal/upload/filesystem effects.
        request.manifest.verify(bytes(script))
        self._validate(request, "commit")
        requested = False

        def cancellation():
            nonlocal requested
            requested = requested or bool(cancelled()) or time.monotonic() >= wire.io.deadline
            if not requested and wire.io.channel.recv_ready():
                previous_deadline = wire.io.deadline
                try:
                    # A partial cancellation frame must not stall wall/CPU checks.
                    wire.io.deadline = min(previous_deadline, time.monotonic() + 0.05)
                    value = _message(wire.receive(), "script_cancel", {"digest"})
                finally:
                    wire.io.deadline = previous_deadline
                if value["digest"] != request.digest:
                    raise ScriptDenied("binding_mismatch")
                requested = True
            return requested

        result = self.execute_verified(request, bytes(script), cancelled=cancellation)
        _send_result(wire, request, result)

    def execute_verified(self, request, script, *, cancelled=lambda: False):
        """Internal authenticated helper seam, not a public dispatch endpoint.

        Also verifies bytes/config here so direct package callers cannot bypass
        checks. Claims survive every partial upload/exception. Only finished
        claims well past request expiry can be freed; unknown work never replays.
        """
        import fcntl

        request.manifest.verify(script)
        self._validate(request, "commit")
        root = private_directory(self.config.workspace_root)
        claim, scratch, job = None, None, None
        job_name = hashlib.sha256(request.digest.encode("ascii")).hexdigest()
        result = None
        try:
            attempts = os.open(
                "attempts", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
            )
            try:
                fcntl.flock(attempts, fcntl.LOCK_EX)
                info = os.fstat(attempts)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ScriptDenied("unsafe_workspace")
                count = 0
                with os.scandir(attempts) as entries:
                    now = time.time()
                    for entry in entries:
                        if not _prune_finished_claim(attempts, entry.name, now):
                            count += 1
                if count >= MAX_TARGET_ATTEMPTS:
                    raise ScriptDenied("target_capacity")
                claim_name = hashlib.sha256(request.execution.encode("utf-8")).hexdigest()
                try:
                    claim = _mkdir_at(attempts, claim_name)
                except FileExistsError:
                    raise ScriptDenied("replay_denied") from None
            finally:
                os.close(attempts)
            atomic_file(
                claim,
                "claim.json",
                canonical(
                    {
                        "v": 1,
                        "execution": request.execution,
                        "attempt": request.attempt,
                        "digest": request.digest,
                        "script_sha256": request.manifest.script_sha256,
                        "expires_at": request.expires_at,
                    }
                ),
            )
            atomic_file(claim, "script.py", script)
            scratch = private_directory(self.config.scratch_root)
            job = _mkdir_at(scratch, job_name)
            # bwrap needs an existing empty destination for the read-only script bind.
            atomic_file(job, "script.py", b"")
            self._stage_inputs(root, job, request)
            script_path = Path(self.config.workspace_root) / "attempts" / claim_name / "script.py"
            job_path = Path(self.config.scratch_root) / job_name
            result = self.sandbox.run(request, script_path, job_path, cancelled=cancelled)
            if result.state == "observed_exit" and result.termination_confirmed:
                try:
                    result = replace(result, artifacts=self._artifacts(job, request))
                except (OSError, ProtocolDenied):
                    result = replace(result, code="artifact_denied")
            # Metadata only: target does not persist/log arbitrary stdout/errors.
            atomic_file(
                claim,
                "result.json",
                canonical(
                    {
                        "state": result.state,
                        "code": result.code,
                        "exit_code": result.exit_code,
                        "cancel_requested": result.cancel_requested,
                        "termination_confirmed": result.termination_confirmed,
                    }
                ),
            )
            return result
        finally:
            if job is not None and result is not None and result.termination_confirmed:
                try:
                    _remove_owned_job(job)
                    os.rmdir(job_name, dir_fd=scratch)
                except Exception:
                    # Script-owned permissions, depth and filesystem errors
                    # cannot erase the observed result. Leftovers remain in the
                    # bounded scratch tmpfs for operator cleanup.
                    pass
            for fd in (job, scratch, claim, root):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    @staticmethod
    def _stage_inputs(root, job, request):
        if not request.manifest.input_paths:
            return
        inputs = os.open("inputs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            for item in request.manifest.input_paths:
                raw = read_regular_at(inputs, item.path, item.size, uid=os.geteuid())
                if len(raw) != item.size or hashlib.sha256(raw).hexdigest() != item.sha256:
                    raise ScriptDenied("input_digest_mismatch")
                _write_job_file(job, item.path, raw)
        finally:
            os.close(inputs)

    @staticmethod
    def _artifacts(job, request):
        artifacts, total = [], 0
        for path in request.manifest.output_paths:
            raw = read_regular_at(
                job, path, request.manifest.limits.artifacts_bytes - total, uid=os.geteuid()
            )
            total += len(raw)
            artifacts.append((path, raw))
        return tuple(artifacts)


class _StdioChannel:
    def __init__(self):
        self.timeout = 40

    def settimeout(self, value):
        self.timeout = value

    @staticmethod
    def recv_ready():
        return bool(select.select([0], [], [], 0)[0])

    def recv(self, size):
        if not select.select([0], [], [], self.timeout)[0]:
            raise ScriptDenied("deadline_exceeded")
        return os.read(0, size)

    def sendall(self, data):
        deadline = time.monotonic() + self.timeout
        while data:
            if not select.select([], [1], [], max(0, deadline - time.monotonic()))[1]:
                raise ScriptDenied("deadline_exceeded")
            data = data[os.write(1, data) :]


def _operator_config():
    path = Path(TARGET_CONFIG_PATH)
    directory = private_directory(path.parent, uid=0, private=False)
    try:
        raw = read_regular_at(directory, path.name, 8192, uid=0)
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if info.st_mode & 0o022:
            raise ScriptDenied("invalid_configuration")
    finally:
        os.close(directory)
    value = json.loads(raw)
    exact_fields(value, ScriptTargetConfig.__dataclass_fields__)
    return ScriptTargetConfig(**value)


def _ssh_key_principal():
    # OpenSSH ExposeAuthInfo creates this private public-authentication receipt.
    # Operator must not AcceptEnv SSH_USER_AUTH and must dedicate the account/key.
    path = Path(os.environ.get("SSH_USER_AUTH", ""))
    if not path.is_absolute():
        raise ScriptDenied("binding_mismatch")
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        raw = read_regular_at(directory, path.name, 8192, uid=os.geteuid())
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ScriptDenied("binding_mismatch")
    finally:
        os.close(directory)
    parts = raw.decode("ascii").strip().split()
    if len(parts) != 3 or parts[:2] != ["publickey", "ssh-ed25519"]:
        raise ScriptDenied("binding_mismatch")
    return hashlib.sha256(base64.b64decode(parts[2], validate=True)).hexdigest()


def main():
    """Fixed installed SSH command; protected config only, no caller argv flags."""
    try:
        config = _operator_config()
        if not config.enabled or len(sys.argv) != 1:
            return 1
        principal = _ssh_key_principal()
        # SSH proves possession of the sole configured controller key. Approval
        # and current grant intersection remain the existing controller's job.
        TargetHelper(config, key_principal=principal, authorize=lambda request, phase: True).serve(
            _StdioChannel(),
        )
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
