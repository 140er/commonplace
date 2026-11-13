"""Signed synthetic releases, isolated files and route seams; no live services."""

# ruff: noqa: F811
import base64
import copy
import io
import json
import socket
import stat
import zipfile
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commonplace import sessions
from commonplace.runner import install_receipts, installer, registry, runner_api
from commonplace.runner.install_files import MANIFEST, STATE, digest, encoded
from commonplace.runner.install_host import InstallError
from tests.test_runner_install import CODE, TOKENS, setup, snapshot  # noqa: F401


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("runtime tests must not contact a live service")

    monkeypatch.setattr(socket, "create_connection", refuse)


def bundle(name, *, version="1.2.3", supported=True, sentinel=""):
    output = io.BytesIO()
    script = (
        "import json,os,sys\n"
        "args=sys.argv[1:]\n"
        "assert not any('TOKEN' in k or 'GRANT' in k for k in os.environ)\n"
        f"assert {sentinel!r} not in ' '.join(sys.argv) if {bool(sentinel)!r} else True\n"
        "assert sys.stdin.read() == ''\n"
        f"name={name!r}\n"
        f"version={version!r}\n"
        f"supported={supported!r}\n"
        "if args == ['--version']: print(version)\n"
        "elif args == ['--help']: print({'host':'acp-mcp-call serve','claude':'--print auth','codex':'exec login'}[name] if supported else 'unavailable')\n"
        "elif args == ['auth','--help'] or args == ['login','--help']: print('status')\n"
        "elif args == ['auth','status','--json']: print(json.dumps({'loggedIn':False}))\n"
        "elif args == ['login','status']: sys.exit(1)\n"
        "else: sys.exit(20)\n"
    )
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("__main__.py", script)
    return output.getvalue()


def selection(providers=("claude",)):
    names = ("host", *providers)
    artifacts = {name: bundle(name) for name in names}
    runtime = {
        "components": {
            name: {
                "version": "1.2.3",
                "sha256": digest(raw),
                "url": "https://releases.example.test/" + name + "/1.2.3",
                "format": "python-zipapp",
            }
            for name, raw in artifacts.items()
        },
        "topic_models": {"work": {"provider": providers[0], "model": "fixture-model"}},
    }
    if len(providers) == 2:
        runtime["topic_models"]["review"] = {"provider": providers[1], "model": "fixture-review"}
    return runtime, artifacts


def route(s, method, path, body=None, principal=None):
    raw = encoded(body) if body is not None else b""
    handler = SimpleNamespace(
        path=path,
        command=method,
        close_connection=False,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
        rfile=io.BytesIO(raw),
        wfile=io.BytesIO(),
        send_header=lambda *a: None,
        end_headers=lambda: None,
    )
    handler.send_response = lambda code: setattr(handler, "status", code)
    gateway = SimpleNamespace(
        db=s.db, principal_for=lambda _: principal or s.principal, runner_receipt_signer=s.key
    )
    runner_api._install_manifest(gateway, handler)
    return handler.status, json.loads(handler.wfile.getvalue())


def approve(s, runtime):
    row = registry.get_machine(s.db, s.owner.workspace_id, s.config["machine_id"])
    previous = s.db._conn.execute(
        "SELECT version FROM runner_install_manifests WHERE enrollment_id=?",
        (s.intent["enrollment_id"],),
    ).fetchone()
    try:
        registry.attest(
            s.db,
            s.owner,
            row["id"],
            {
                "topics": json.loads(row["topics"]),
                "provider_instance_id": "fixture-provider",
                "worker_connection_id": row["worker_connection_id"],
                "install_settings": {
                    "grant_version": row["grant_version"],
                    "binding_epoch": row["binding_epoch"],
                    "settings_version": previous[0] if previous else 0,
                    "projects": {
                        "demo": {
                            "project_id": "project-demo",
                            "checkout": "shared",
                            "runtime_ceiling": "approval-required",
                        }
                    },
                    "runtime": runtime,
                },
            },
        )
    except ValueError:
        return False
    return True


def prepare(s):
    install_receipts.ensure(s.db)
    s.owner = sessions.person_principal(
        s.db, s.db._conn.execute("SELECT id FROM users").fetchone()[0], s.intent["workspace_id"]
    )
    s.key = Ed25519PrivateKey.generate()
    public = s.key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    s.local = installer.Installer(s.home, s.host, s.transport)
    assert (
        s.local.install(s.intent, accepted=True, code_source=lambda: CODE, approval_key=public) == 0
    )
    s.config = json.loads(s.local.fs.read(installer.CONFIG))
    s.principal = s.db.authenticate_token(s.local.fs.read(STATE + "/machine.token").decode())

    def receipt(token, nonce):
        code, data = route(s, "GET", "/api/runner/install-manifest?nonce=" + nonce)
        if code != 200:
            raise InstallError("receipt unavailable")
        return data

    def status(token, body):
        code, data = route(s, "POST", "/api/runner/install-status", body)
        if code != 200:
            raise InstallError("status unavailable")
        s.reports.append(copy.deepcopy(body))
        return data

    s.reports = []
    s.transport.receipt, s.transport.runtime_status = receipt, status
    return s


def test_signed_runtime_selection_produces_cache(setup):
    """The same assertion is RED on the base, without missing-import/setup errors."""
    s = prepare(setup)
    runtime, artifacts = selection()
    assert approve(s, runtime), "owner-approved runtime selections must be accepted"
    from commonplace.runner.runtime_host import FakeRuntimeHost
    from commonplace.runner.runtime_install import RUNTIME_CACHE

    host = FakeRuntimeHost()
    host.artifacts = {digest(raw): raw for raw in artifacts.values()}
    s.local.runtime_host = host
    s.local.sync_receipts()
    config = json.loads(s.local.fs.read(RUNTIME_CACHE))
    assert config["provider_instance_id"] == "fixture-provider"
    assert config["topic_models"] == runtime["topic_models"]
    assert config["components"]["claude"]["login"] == "logged-out"
    assert config["agent_ready"] is False
    needs = install_receipts.attention(s.db, s.owner)
    assert any(item["message"] == "Sign-in needed" for item in needs)


@pytest.fixture
def ready(setup):
    from commonplace.runner.runtime_host import FakeRuntimeHost

    s = prepare(setup)
    s.runtime, s.artifacts = selection()
    assert approve(s, s.runtime)
    s.runtime_host = FakeRuntimeHost()
    s.runtime_host.artifacts = {digest(raw): raw for raw in s.artifacts.values()}
    s.local.runtime_host = s.runtime_host
    return s


def runtime_cache(s):
    from commonplace.runner.runtime_install import Runtime

    return Runtime(s.local, s.config, s.transport, s.runtime_host).read()


def test_clean_install_private_convergent_mapping_and_owned_uninstall(ready):
    from commonplace.runner.runtime_install import RUNTIME_CACHE

    s = ready
    before = snapshot(s.home)
    s.local.sync_receipts()
    first = snapshot(s.home)
    config = runtime_cache(s)
    assert len(config["projects"]) == 1
    checkout = s.home / config["projects"]["demo"]["directory"]
    assert checkout.is_dir() and list(checkout.iterdir()) == []
    for path in (s.home / STATE).rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    downloads = len([c for c in s.runtime_host.calls if c[0] == "download"])
    s.local.sync_receipts()
    assert snapshot(s.home) == first
    assert len([c for c in s.runtime_host.calls if c[0] == "download"]) == downloads
    assert runtime_cache(s) == config
    (checkout / "keep").write_text("unrelated")
    assert s.local.uninstall() == 0
    assert (checkout / "keep").read_text() == "unrelated"
    assert not (s.home / RUNTIME_CACHE).exists()
    assert not s.host.active and s.host.commands[-2:] == ["disable-linger", "reload"]
    assert set(snapshot(s.home)) - set(before) <= {
        STATE + "/checkouts",
        str(checkout.relative_to(s.home)),
        str((checkout / "keep").relative_to(s.home)),
    }


def test_existing_matching_components_are_reused_and_occupied_ones_preserved(ready):
    s = ready
    external = s.home / "existing-host"
    external.write_bytes(s.artifacts["host"])
    external.chmod(0o600)
    s.runtime_host.existing["host"] = {"path": str(external), "raw": s.artifacts["host"]}
    s.runtime_host.existing["claude"] = {"path": "/occupied/provider", "raw": b"unrelated"}
    s.runtime_host.existing["codex"] = {"path": "/unselected/provider", "raw": b"unselected"}
    s.local.sync_receipts()
    config = runtime_cache(s)
    assert config["components"]["host"]["path"] == str(external)
    assert ("download", digest(s.artifacts["host"])) not in s.runtime_host.calls
    assert ("locate", "codex") not in s.runtime_host.calls
    s.local.uninstall()
    assert external.read_bytes() == s.artifacts["host"]
    assert s.runtime_host.existing["claude"]["raw"] == b"unrelated"


@pytest.mark.parametrize("failure", ["tamper", "download", "occupied", "symlink"])
def test_failure_before_verification_executes_nothing_and_publishes_nothing(ready, failure):
    from commonplace.runner.runtime_install import PREFIX

    s = ready
    if failure in ("occupied", "symlink"):
        prefix = s.home / PREFIX
        if failure == "symlink":
            target = s.home / "other"
            target.mkdir(mode=0o700)
            prefix.symlink_to(target, target_is_directory=True)
        else:
            prefix.mkdir(mode=0o700)
            (prefix / "keep").write_text("unrelated")
    elif failure == "tamper":
        s.runtime_host.artifacts[digest(s.artifacts["claude"])] = b"tampered"
    else:
        del s.runtime_host.artifacts[digest(s.artifacts["claude"])]
    with pytest.raises(InstallError):
        s.local.sync_receipts()
    assert not any(c[0] == "probe" for c in s.runtime_host.calls)
    assert not list((s.home / STATE).rglob("*.artifact"))


@pytest.mark.parametrize(
    "mutation", ["pin", "signature", "wrong-key", "expired", "mapping", "project", "version"]
)
def test_untrusted_release_manifest_never_reaches_a_host_adapter(ready, mutation):
    s = ready
    real = s.transport.receipt

    def changed(token, nonce):
        result = copy.deepcopy(real(token, nonce))
        receipt = result["receipt"]
        if mutation == "pin":
            receipt["runtime"]["components"]["host"]["sha256"] = "0" * 64
        elif mutation == "mapping":
            receipt["runtime"]["topic_models"]["work"]["model"] = "unapproved"
        elif mutation == "project":
            receipt["projects"]["demo"]["project_id"] = "other"
        elif mutation == "version":
            receipt["runtime"]["components"]["host"]["version"] = "latest"
        elif mutation == "expired":
            receipt["expires_at"] = receipt["issued_at"]
            result["signature"] = base64.b64encode(
                s.key.sign(install_receipts.canonical(receipt))
            ).decode()
        else:
            signing = Ed25519PrivateKey.generate()
            result["signature"] = base64.b64encode(
                signing.sign(install_receipts.canonical(receipt))
            ).decode()
        return result

    s.transport.receipt = changed
    with pytest.raises(InstallError):
        s.local.sync_receipts()
    assert s.runtime_host.calls == []


def test_local_pin_and_mapping_edits_are_rejected_then_rederived(ready):
    from commonplace.runner.runtime_install import RUNTIME_CACHE

    s = ready
    s.local.sync_receipts()
    original = runtime_cache(s)
    changed = copy.deepcopy(original)
    changed["components"]["host"]["sha256"] = "0" * 64
    changed["topic_models"]["work"]["model"] = "unapproved"
    s.local.fs.write(RUNTIME_CACHE, encoded(changed), replace=True)
    with pytest.raises(InstallError):
        runtime_cache(s)
    s.local.sync_receipts()
    assert runtime_cache(s) == original


def test_partial_publication_recovers_without_reinstalling_verified_artifacts(ready, monkeypatch):
    s = ready
    original = s.local.file
    failed = []

    def interrupted(path, raw):
        if "/claude-" in path and not failed:
            failed.append(True)
            raise OSError("SyntheticWriteSentinel")
        return original(path, raw)

    monkeypatch.setattr(s.local, "file", interrupted)
    with pytest.raises(InstallError):
        s.local.sync_receipts()
    s.local.sync_receipts()
    assert runtime_cache(s)["components"]["host"]["availability"] == "present"
    assert (
        len([c for c in s.runtime_host.calls if c == ("download", digest(s.artifacts["host"]))])
        == 1
    )


def test_unsupported_capability_blocks_readiness_and_needs_owner(ready):
    s = ready
    s.runtime_host.facts["claude"] = {"availability": "unsupported", "login": "unknown"}
    s.local.sync_receipts()
    assert runtime_cache(s)["agent_ready"] is False
    assert install_receipts.attention(s.db, s.owner)[0]["reason"] == "unsupported_runtime"
    with pytest.raises(registry.Conflict):
        registry.patch(s.db, s.owner, s.config["machine_id"], {"enabled": True})


def test_stale_reports_and_foreign_principals_cannot_widen_scope(ready):
    from commonplace.runner import enrollment

    s = ready
    s.local.sync_receipts()
    body = s.reports[-1]
    worker = s.db.authenticate_token(s.local.fs.read(STATE + "/worker.token").decode())
    assert route(s, "POST", "/api/runner/install-status", body, worker)[0] == 404
    corrupt = {
        **body,
        "components": {
            **body["components"],
            "codex": {"availability": "present", "login": "unverified"},
        },
    }
    assert route(s, "POST", "/api/runner/install-status", corrupt)[0] == 400
    registry.patch(s.db, s.owner, s.config["machine_id"], {"topics": ["review"]})
    assert route(s, "POST", "/api/runner/install-status", body)[0] == 409
    assert not any(
        item.get("reason") == "sign_in_needed" for item in install_receipts.attention(s.db, s.owner)
    )
    enrollment.set_enabled(s.db, s.owner, False)
    assert route(s, "POST", "/api/runner/install-status", body)[0] == 404


def test_runtime_records_classified_exported_and_deleted(ready, monkeypatch, tmp_path):
    import time

    from commonplace.account import deletion, export, tables

    s = ready
    s.local.sync_receipts()
    assert tables.RULES["runner_installations"].workspace == ("workspace_id",)
    assert tables.unclassified(tables.present(s.db._conn)) == []
    gateway = SimpleNamespace(
        db=s.db,
        wall_clock=time.time,
        supervisor=SimpleNamespace(
            root=tmp_path / "account-data" / "workspaces", stop=lambda _: None
        ),
    )
    empty = tmp_path / "empty-export"
    empty.mkdir()
    planned = {
        "user": s.owner.user_id,
        "email": s.owner.user_email,
        "workspaces": [
            {
                "id": s.owner.workspace_id,
                "name": "fixture",
                "role": "owner",
                "content": True,
                "directory": empty,
                "databases": [],
                "files": [],
            }
        ],
    }
    output = io.BytesIO()
    assert export.write(gateway, planned, output)["complete"] is True
    with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
        rows = archive.read(
            "workspaces/" + s.owner.workspace_id + "/control/runner_installations.ndjson"
        )
        assert json.loads(rows)["machine_id"] == s.config["machine_id"]
        assert not any(secret.encode() in rows for secret in (*TOKENS, CODE))
    deletion.ensure_schema(s.db)
    sessions.ensure_schema(s.db)
    session = sessions.start_session(s.db, s.owner.user_id, s.owner.workspace_id)
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    deletion.request(gateway, s.owner, session, s.owner.user_email)
    assert deletion.purge(gateway, s.owner.user_id)["state"] == "purged"
    assert s.db._conn.execute("SELECT count(*) FROM runner_installations").fetchone()[0] == 0


def test_plan_has_no_effects_and_lists_runtime_actions(ready):
    s = ready
    before, calls = snapshot(s.home), list(s.runtime_host.calls)
    output = []
    s.local.out = output.append
    s.local.plan()
    assert snapshot(s.home) == before and s.runtime_host.calls == calls
    assert all(
        any(word in line.lower() for line in output)
        for word in ("digest", "topic", "sign-in", "0600", "0700")
    )


def test_synthetic_artifacts_run_on_isolated_linux_adapter_without_credentials(
    ready, monkeypatch, capsys, caplog
):
    from commonplace.runner.runtime_host import LiveRuntimeHost

    s = ready
    host = LiveRuntimeHost()
    monkeypatch.setattr(host, "locate", s.runtime_host.locate)
    monkeypatch.setattr(host, "download", s.runtime_host.download)
    s.local.runtime_host = host
    monkeypatch.setenv("SYNTHETIC_TOKEN", "SyntheticEnvironmentSentinel")
    s.local.sync_receipts()
    config = runtime_cache(s)
    assert config["components"]["host"]["availability"] == "present"
    assert config["components"]["claude"]["login"] == "logged-out"
    output = capsys.readouterr()
    evidence = (
        output.out
        + output.err
        + caplog.text
        + s.local.fs.read(MANIFEST).decode()
        + json.dumps(s.reports)
    )
    assert not any(value in evidence for value in (*TOKENS, CODE, "SyntheticEnvironmentSentinel"))


def test_both_selected_provider_adapters_and_topics_are_cached(ready, monkeypatch):
    from commonplace.runner.runtime_host import LiveRuntimeHost

    s = ready
    runtime, artifacts = selection(("claude", "codex"))
    registry.patch(s.db, s.owner, s.config["machine_id"], {"topics": ["work", "review"]})
    assert approve(s, runtime)
    host = LiveRuntimeHost()
    monkeypatch.setattr(host, "locate", lambda name: None)
    monkeypatch.setattr(
        host,
        "download",
        lambda release: next(raw for raw in artifacts.values() if digest(raw) == release["sha256"]),
    )
    s.local.runtime_host = host
    s.local.sync_receipts()
    config = runtime_cache(s)
    assert set(config["components"]) == {"host", "claude", "codex"}
    assert config["topic_models"] == runtime["topic_models"]
    assert all(config["components"][name]["login"] == "logged-out" for name in ("claude", "codex"))


def test_new_files_are_exclusive_and_private_at_creation(ready, monkeypatch):
    import os

    s = ready
    opened = []
    real_open = os.open

    def checked(path, flags, mode=0o777, **kwargs):
        if flags & os.O_CREAT:
            opened.append(str(path))
            assert flags & os.O_EXCL and flags & os.O_NOFOLLOW
            assert mode == 0o600
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", checked)
    s.local.sync_receipts()
    assert any(".artifact" in path for path in opened)


def test_local_cache_edits_do_not_broaden_project_or_execution_scope(ready):
    from commonplace.runner.runtime_install import RUNTIME_CACHE

    s = ready
    s.local.sync_receipts()
    original = runtime_cache(s)
    for key, value in (
        ("projects", {}),
        ("agent_ready", True),
        ("provider_instance_id", "unapproved"),
    ):
        s.local.fs.write(RUNTIME_CACHE, encoded({**original, key: value}), replace=True)
        with pytest.raises(InstallError):
            runtime_cache(s)
        s.local.sync_receipts()
        assert runtime_cache(s) == original


def test_runtime_uninstall_retains_artifacts_until_service_stop_is_confirmed(ready, monkeypatch):
    s = ready
    s.local.sync_receipts()
    artifacts = list((s.home / STATE).rglob("*.artifact"))
    original = s.host.change
    monkeypatch.setattr(
        s.host, "change", lambda action: None if action == "disable" else original(action)
    )
    with pytest.raises(InstallError, match="stop unconfirmed"):
        s.local.uninstall()
    assert all(path.exists() for path in artifacts)
    monkeypatch.setattr(s.host, "change", original)
    s.local.uninstall()
    assert all(not path.exists() for path in artifacts)


@pytest.mark.parametrize("case", ["version", "headless", "native-wrapper"])
def test_actual_adapter_refuses_unknown_release_or_capabilities(ready, monkeypatch, case):
    from commonplace.runner.runtime_host import LiveRuntimeHost

    s = ready
    raw = bundle(
        "host", version="2.0.0" if case == "version" else "1.2.3", supported=case != "headless"
    )
    release = {**s.runtime["components"]["host"], "sha256": digest(raw)}
    if case == "native-wrapper":
        release["format"] = "native"
    s.runtime["components"]["host"] = release
    assert approve(s, s.runtime)
    host = LiveRuntimeHost()
    monkeypatch.setattr(host, "locate", lambda _: None)
    monkeypatch.setattr(host, "download", lambda r: raw if r == release else s.artifacts["claude"])
    s.local.runtime_host = host
    s.local.sync_receipts()
    config = runtime_cache(s)
    assert config["components"]["host"]["availability"] == "unsupported"
    assert config["agent_ready"] is False
    assert not list((s.home / STATE).rglob("host-*.artifact"))


def test_probe_output_and_failure_messages_never_reflect_a_sentinel(ready, monkeypatch, capsys):
    from commonplace.runner.runtime_host import LiveRuntimeHost

    s = ready
    sentinel = "SyntheticProbeFailureSentinel"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "__main__.py",
            f"import sys; print({sentinel!r}); print({sentinel!r},file=sys.stderr); sys.exit(1)",
        )
    raw = output.getvalue()
    s.runtime["components"]["host"]["sha256"] = digest(raw)
    assert approve(s, s.runtime)
    host = LiveRuntimeHost()
    monkeypatch.setattr(host, "locate", lambda _: None)
    monkeypatch.setattr(
        host, "download", lambda r: raw if r["sha256"] == digest(raw) else s.artifacts["claude"]
    )
    s.local.runtime_host = host
    s.local.sync_receipts()
    s.local.status()
    captured = capsys.readouterr()
    assert (
        sentinel
        not in captured.out
        + captured.err
        + json.dumps(s.reports)
        + s.local.fs.read(MANIFEST).decode()
    )
    assert runtime_cache(s)["components"]["host"]["availability"] == "unsupported"


def test_local_signed_release_pin_edit_is_rejected_before_any_probe(ready):
    from cryptography.exceptions import InvalidSignature

    from commonplace.runner.receipt_cache import CACHE

    s = ready
    s.local.sync_receipts()
    stored = json.loads(s.local.fs.read(CACHE))
    stored["signed"]["receipt"]["runtime"]["components"]["host"]["version"] = "9.9.9"
    s.local.fs.write(CACHE, encoded(stored), replace=True)
    calls = list(s.runtime_host.calls)
    with pytest.raises(InvalidSignature):
        runtime_cache(s)
    assert s.runtime_host.calls == calls


def test_disabled_setup_performs_no_runtime_operations_and_hides_status(ready):
    from commonplace.runner import enrollment
    from commonplace.runner.runtime_install import RUNTIME_CACHE

    s = ready
    s.local.sync_receipts()
    calls = list(s.runtime_host.calls)
    body = s.reports[-1]
    enrollment.set_enabled(s.db, s.owner, False)
    assert route(s, "POST", "/api/runner/install-status", body) == (404, {"error": "not found"})
    with pytest.raises(InstallError):
        s.local.sync_receipts()
    assert s.runtime_host.calls == calls
    assert json.loads(s.local.fs.read(RUNTIME_CACHE)) == {}
    assert install_receipts.attention(s.db, s.owner) == []


def test_download_adapter_is_bounded_private_and_does_not_reflect_errors(ready, monkeypatch):
    from commonplace.runner import runtime_host

    s = ready
    release = s.runtime["components"]["host"]
    requests = []
    raw = s.artifacts["host"]

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 30 and request.get_header("Authorization") is None
            return Response(raw)

    handlers = []
    monkeypatch.setattr(
        runtime_host.urllib.request, "build_opener", lambda *args: handlers.extend(args) or Opener()
    )
    host = runtime_host.LiveRuntimeHost()
    assert host.download(release) == raw
    assert requests[0].full_url == release["url"]
    assert any(
        isinstance(handler, runtime_host.urllib.request.ProxyHandler) and handler.proxies == {}
        for handler in handlers
    )
    monkeypatch.setattr(runtime_host, "MAX_ARTIFACT", 1)
    with pytest.raises(InstallError, match="download unavailable"):
        host.download(release)
