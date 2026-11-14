"""Synthetic immutable bundle/framing fixtures; the integration maintainer runs the grouped suite."""

import base64
import hashlib
import json
import sqlite3
import time
from dataclasses import replace

import pytest

from commonplace.compute.protocol import ProtocolDenied, decode_frame, encode_frame
from commonplace.compute.script_sandbox import ScriptResult
from commonplace.compute.scripts import (
    MAX_SCRIPT_BYTES,
    ScriptBinding,
    ScriptBundleStore,
    ScriptDenied,
    ScriptInput,
    ScriptLimits,
    ScriptManifest,
    ScriptRequest,
    project_result,
)
from commonplace.compute.target_helper import (
    ScriptWire,
    _send_result,
    cancel_frame,
    receive_result,
    send_bundle,
)


def binding(**changes):
    return replace(
        ScriptBinding(
            "ws_fixture",
            "project",
            "owner",
            "agent",
            "task:328",
            "gateway",
            "node",
            "a" * 64,
        ),
        **changes,
    )


def manifest(script=b"print('fixture')\n", **changes):
    return ScriptManifest.create(binding(), "bundle-1", script, runtime_digest="b" * 64, **changes)


def request(script=b"print('fixture')\n", **changes):
    return ScriptRequest("execution-1", "attempt-1", manifest(script, **changes), time.time() + 60)


class MemoryChannel:
    """Finite encoded byte channel, no sockets/processes/provider or SSH claims."""

    def __init__(self, messages=()):
        self.incoming = bytearray(b"".join(encode_frame(x) for x in messages))
        self.sent = []

    def recv(self, count):
        part = bytes(self.incoming[:count])
        del self.incoming[:count]
        return part

    def sendall(self, raw):
        self.sent.append(decode_frame(raw))

    def settimeout(self, value):
        assert 0 < value <= 40

    def recv_ready(self):
        return bool(self.incoming)


def test_verbatim_crypto_connection_store_reopen_and_immutable(tmp_path):
    script = "# exact café\r\nprint('literal $HOME; `cmd`')\n".encode()
    value = manifest(script, arguments=("a b", "$(touch ignored)", ""))
    path = tmp_path / "compute-scripts.sqlite3"
    calls = []

    def authorize(context, current, action):
        calls.append((context, current, action))
        return context == "live-owner" and current == binding()

    store = ScriptBundleStore(path, workspace="ws_fixture", authorize=authorize)
    assert not path.exists()
    store.put(value, script, context="live-owner")
    assert store.put(value, script, context="live-owner") == value
    reopened = ScriptBundleStore(path, workspace="ws_fixture", authorize=authorize)
    assert reopened.get("bundle-1", binding=binding(), context="live-owner") == (value, script)
    assert reopened.list(binding=binding(), context="live-owner") == (value,)
    assert calls and {x[2] for x in calls} == {"put", "get", "list"}
    with pytest.raises(ScriptDenied, match="bundle_conflict"):
        store.put(replace(value, arguments=("changed",)), script, context="live-owner")
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable bundle"):
            conn.execute("UPDATE script_bundles_v1 SET script=x'00'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable bundle"):
            conn.execute("DELETE FROM script_bundles_v1")


def test_store_uses_workspace_crypto_and_denial_is_lazy(tmp_path, monkeypatch):
    import commonplace.compute.scripts as module

    path = tmp_path / "never.sqlite3"
    store = ScriptBundleStore(path, workspace="ws_fixture")
    monkeypatch.setattr(module, "connect", lambda *a, **k: pytest.fail("denied request opened DB"))
    with pytest.raises(ScriptDenied, match="script_authority_denied"):
        store.put(manifest(), b"print('fixture')\n", context=None)
    assert not path.exists()
    seen = []

    def locked(*args, **kwargs):
        seen.append(args[0])
        raise ValueError("synthetic locked workspace")

    monkeypatch.setattr(module, "connect", locked)
    store.authorize = lambda *args: True
    with pytest.raises(ScriptDenied, match="bundle_unavailable"):
        store.put(manifest(), b"print('fixture')\n", context=None)
    assert seen == [str(path)] and not path.exists()


def test_digest_covers_every_permission_relevant_field():
    value = request(arguments=("literal",), output_paths=("result.txt",))
    baseline = value.digest
    manifests = [
        replace(value.manifest, bundle_id="other"),
        replace(value.manifest, arguments=("changed",)),
        replace(value.manifest, input_paths=(ScriptInput("in.txt", "c" * 64, 4),)),
        replace(value.manifest, output_paths=("other.txt",)),
        replace(value.manifest, runtime_digest="d" * 64),
        replace(value.manifest, script_sha256="e" * 64),
        replace(value.manifest, limits=ScriptLimits(processes=8)),
        replace(value.manifest, binding=binding(authority_epoch=2)),
        replace(value.manifest, binding=binding(node="other")),
    ]
    assert all(replace(value, manifest=x).digest != baseline for x in manifests)
    assert replace(value, attempt="new").digest != baseline
    assert replace(value, expires_at=value.expires_at + 1).digest != baseline
    assert ScriptRequest.from_dict(json.loads(json.dumps(value.document()))) == value


@pytest.mark.parametrize(
    "kwargs",
    [
        {"arguments": ["a"] * 17},
        {"arguments": ["a" * 257]},
        {"arguments": ["bad\x00"]},
        {"arguments": "bad"},
        {"arguments": [True]},
        {"arguments": ["\ud800"]},
        {"privilege": "root"},
        {"runtime": "sh.v1"},
        {"operation": "install"},
        {"output_paths": ["../escape"]},
        {"output_paths": ["/absolute"]},
        {"output_paths": ["a//b"]},
        {"output_paths": ["a\\b"]},
        {"output_paths": ["a", "a/b"]},
        {"output_paths": ["same", "same"]},
        {"output_paths": ["script.py"]},
        {"output_paths": ["a"] * 9},
    ],
)
def test_invalid_manifest_is_inert(kwargs):
    with pytest.raises(ProtocolDenied):
        manifest(**kwargs)


@pytest.mark.parametrize("script", [b"", b"\xff", b"a\x00b", b"a" * (MAX_SCRIPT_BYTES + 1)])
def test_exact_utf8_size_and_digest(script):
    with pytest.raises(ScriptDenied, match="invalid_script"):
        manifest(script)


def test_snapshot_limits_and_strict_fields():
    args = ["original"]
    value = manifest(arguments=args)
    args[0] = "later"
    assert value.arguments == ("original",)
    with pytest.raises(ScriptDenied, match="script_digest_mismatch"):
        value.verify(b"print('tampered')\n")
    with pytest.raises(ProtocolDenied, match="invalid_fields"):
        ScriptManifest.from_dict({**value.document(), "env": {"SECRET": "ignored"}})
    with pytest.raises(ScriptDenied):
        ScriptLimits(processes=True)
    with pytest.raises(ScriptDenied):
        ScriptLimits(memory_bytes=2**40)


def test_store_capacity_and_list_bounds(tmp_path, monkeypatch):
    import commonplace.compute.scripts as module

    monkeypatch.setattr(module, "MAX_BUNDLES", 2)
    store = ScriptBundleStore(
        tmp_path / "bundles.sqlite3", workspace="ws_fixture", authorize=lambda *a: True
    )
    value = manifest()
    for name in ("one", "two"):
        store.put(replace(value, bundle_id=name), b"print('fixture')\n", context=None)
    assert len(store.list(binding=binding(), context=None, limit=1)) == 1
    with pytest.raises(ScriptDenied, match="bundle_capacity"):
        store.put(value, b"print('fixture')\n", context=None)
    with pytest.raises(ScriptDenied):
        store.list(binding=binding(), context=None, limit=101)


def test_chunked_upload_under_existing_frame_budget():
    script = b"#" + b"x" * (MAX_SCRIPT_BYTES - 1)
    value = request(script)
    channel = MemoryChannel([{"v": 1, "kind": "script_ready", "digest": value.digest}])
    send_bundle(ScriptWire(channel), value, script)
    chunks = channel.sent[1:-1]
    assert len(chunks) == 11
    assert [x["seq"] for x in chunks] == list(range(11))
    assert b"".join(base64.b64decode(x["data"]) for x in chunks) == script
    assert all(len(encode_frame(x)) <= 8196 for x in channel.sent)
    assert cancel_frame(value) == {"v": 1, "kind": "script_cancel", "digest": value.digest}


def test_result_roundtrip_redacts_combined_output_and_hides_artifact_content():
    value = request(output_paths=("result.txt",))
    secret = b"sk-ant-" + b"X" * 40
    original = ScriptResult(
        "observed_exit",
        "exited",
        7,
        True,
        True,
        b"hello\n" + secret,
        b"warning",
        (("result.txt", b"private bytes"),),
    )
    channel = MemoryChannel()
    _send_result(ScriptWire(channel), value, original)
    decoded = receive_result(ScriptWire(MemoryChannel(channel.sent)), value)
    assert decoded == original
    public = project_result(decoded)
    assert secret.decode() not in json.dumps(public)
    assert "private bytes" not in json.dumps(public)
    assert public["exit_code"] == 7 and public["cancel_requested"] is True
    assert public["artifacts"][0]["sha256"] == hashlib.sha256(b"private bytes").hexdigest()
    assert "private bytes" not in repr(original) and secret.decode() not in repr(original)


def test_result_tamper_unknown_fields_and_frame_budget():
    value = request()
    channel = MemoryChannel()
    _send_result(
        ScriptWire(channel), value, ScriptResult("observed_exit", "exited", 0, stdout=b"ok")
    )
    channel.sent[1]["seq"] = True
    with pytest.raises(ScriptDenied):
        receive_result(ScriptWire(MemoryChannel(channel.sent)), value)
    wire = ScriptWire(MemoryChannel())
    wire.frames = 64
    with pytest.raises(ScriptDenied, match="wire_limit"):
        wire.send("anything")


@pytest.mark.parametrize("exit_code", [None, True, 999])
def test_observed_exit_requires_actual_bounded_exit_code(exit_code):
    value = request()
    channel = MemoryChannel()
    _send_result(ScriptWire(channel), value, ScriptResult("observed_exit", "exited", exit_code))
    with pytest.raises(ScriptDenied):
        receive_result(ScriptWire(MemoryChannel(channel.sent)), value)


def test_cleanup_without_nofollow_chmod(tmp_path, monkeypatch):
    import os

    from commonplace.compute.target_helper import _remove_owned_job

    def chmod(*args, **kwargs):
        if kwargs.get("follow_symlinks") is False:
            raise NotImplementedError("no-follow chmod unavailable")

    monkeypatch.setattr(os, "chmod", chmod)
    (tmp_path / "job" / "nested").mkdir(parents=True)
    (tmp_path / "job" / "nested" / "data").write_bytes(b"owned")
    fd = os.open(tmp_path / "job", os.O_RDONLY | os.O_DIRECTORY)
    try:
        _remove_owned_job(fd)
    finally:
        os.close(fd)
    (tmp_path / "job").rmdir()
    assert not (tmp_path / "job").exists()


def test_cleanup_restores_access_to_script_locked_directories(tmp_path):
    import os

    from commonplace.compute.target_helper import _remove_owned_job

    # A script may leave directories with every permission removed, nested
    # below one another and beside a symlink that must not be followed.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"not owned")
    outside_mode = outside.stat().st_mode
    locked = tmp_path / "job" / "locked"
    (locked / "inner").mkdir(parents=True)
    (locked / "inner" / "data").write_bytes(b"owned")
    (tmp_path / "job" / "link").symlink_to(outside, target_is_directory=True)
    (locked / "inner").chmod(0)
    locked.chmod(0)
    fd = os.open(tmp_path / "job", os.O_RDONLY | os.O_DIRECTORY)
    try:
        _remove_owned_job(fd)
    finally:
        os.close(fd)
    (tmp_path / "job").rmdir()
    assert not (tmp_path / "job").exists()
    assert (outside / "keep").read_bytes() == b"not owned"
    assert outside.stat().st_mode == outside_mode
