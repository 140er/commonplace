"""The one-command machine setup: checks, reports, launcher and a fixture machine end to end.

Nothing here touches a real host: the machine is a fake probe and a recording user manager,
the hub is the real pairing handlers behind in-memory streams or a real in-process hub, and the
shell launcher runs only against stub programs or an invalid envelope.
"""

# ruff: noqa: F811

import base64
import io
import json
import os
import re
import socket
import stat
import subprocess
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace import control_db, sessions
from commonplace.compute import pairing, pairing_routes
from commonplace.control_db import ControlDB
from commonplace.runner import enrollment, installer, machine_setup, registry
from commonplace.runner.install_host import FakeHost
from tests.runner_harness import http, owners, signup_hub  # noqa: F401
from tests.test_compute_pairing import Pairing, flow, machine_key  # noqa: F401
from tests.test_runner_install import RouteTransport

CODE = "SyntheticPairingCodeSentinel_" + "x" * 14  # 43 characters
HUB = "https://hub.example.test"
ENVELOPE = {
    "v": 1,
    "hub": HUB,
    "workspace_id": "ws_0123456789abcdef",
    "pairing_id": "pair_0123456789abcdef",
    "enrollment_id": "enr_0123456789abcdef",
    "label": "build-box",
    "manifest_digest": "a" * 64,
    "kind": "runner",
}


def encode(values):
    raw = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class FakeProbe:
    """A clean stock Ubuntu 24.04 machine unless a test changes one fact."""

    def __init__(self, home, **facts):
        self.facts = {
            "uid": 1000,
            "system": ("Linux", "ubuntu", "24.04"),
            "python": (3, 12),
            "imports": {"cryptography", "commonplace.runner.installer"},
            "user_manager": True,
            "linger": True,
            "enable_linger": True,
            "free": 10 * 1024**3,
            **facts,
        }
        self._home = home
        self.confirmations = 0

    def uid(self):
        return self.facts["uid"]

    def home(self):
        return self._home

    def account(self):
        return "builder"

    def system(self):
        return self.facts["system"]

    def python(self):
        return self.facts["python"]

    def can_import(self, name):
        return name in self.facts["imports"]

    def user_manager(self):
        return self.facts["user_manager"]

    def linger(self):
        return self.facts["linger"]

    def enable_linger(self):
        self.confirmations += 1
        self.facts["linger"] = self.facts["enable_linger"]
        return self.facts["enable_linger"]

    def free_bytes(self, path):
        return self.facts["free"]


class ScriptedHub:
    """Records what the program asks the hub, and answers from a script."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def check(self, body):
        self.calls.append(("check", body))
        answer = self.answers.get("check", {"usable": True})
        if isinstance(answer, Exception):
            raise answer
        return answer

    def status(self, token, body):
        self.calls.append(("status", body))
        return {"ok": True}


# ---- the envelope and the origin -------------------------------------------------------------


def test_the_envelope_is_the_public_values_only():
    assert machine_setup.parse_envelope(encode(ENVELOPE)) == ENVELOPE
    assert "code" not in ENVELOPE and CODE not in encode(ENVELOPE)


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"v": 2},
        {"kind": "target"},
        {"label": "Not Allowed"},
        {"manifest_digest": "A" * 64},
        {"workspace_id": "a b"},
        {"hub": "http://hub.example.test"},
        {"hub": "https://user@hub.example.test"},
        {"hub": "https://hub.example.test/path"},
        {"hub": "https://hub.example.test?x=1"},
        {"hub": "ftp://hub.example.test"},
    ],
)
def test_a_changed_envelope_is_refused(change):
    values = {**ENVELOPE, **change}
    with pytest.raises(ValueError):
        machine_setup.parse_envelope(encode(values))


@pytest.mark.parametrize("text", [None, "", "not base64!", "A" * 3000, encode({"v": 1})])
def test_a_broken_envelope_is_refused(text):
    with pytest.raises(ValueError):
        machine_setup.parse_envelope(text)


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("https://hub.example.test", True),
        ("https://hub.example.test:8443", True),
        ("http://127.0.0.1:8765", True),
        ("http://localhost:1", True),
        ("http://[::1]:8765", True),
        ("http://192.0.2.10", False),
        ("http://hub.example.test", False),
        ("https://hub.example.test/", True),
        ("https://hub.example.test/x", False),
    ],
)
def test_plain_http_is_only_for_this_machine(origin, expected):
    assert machine_setup.origin_ok(origin) is expected


# ---- the checks, in order, each with a cause the app knows -----------------------------------


@pytest.mark.parametrize(
    "facts,cause",
    [
        ({"uid": 0}, "run_as_root"),
        ({"system": ("Darwin", "", "14.0")}, "unsupported_os"),
        ({"system": ("Linux", "ubuntu", "22.04")}, "unsupported_os"),
        ({"system": ("Linux", "debian", "11")}, "unsupported_os"),
        ({"system": ("Linux", "fedora", "40")}, "unsupported_os"),
        ({"python": (3, 9)}, "missing_python"),
        ({"imports": {"commonplace.runner.installer"}}, "missing_prerequisite"),
        ({"imports": {"cryptography"}}, "missing_runner_program"),
        ({"user_manager": False}, "no_service_manager"),
        ({"free": 1024}, "disk_full"),
    ],
)
def test_each_check_names_the_cause_and_what_to_do(tmp_path, facts, cause):
    probe = FakeProbe(tmp_path, **facts)
    with pytest.raises(machine_setup.Failure) as raised:
        machine_setup.prerequisites(probe)
    failure = raised.value
    assert failure.cause == cause and cause in pairing.CAUSES
    assert failure.message.endswith(".") and failure.then.endswith(".")


@pytest.mark.parametrize(
    "system",
    [
        ("Linux", "ubuntu", "24.04"),
        ("Linux", "ubuntu", "25.10"),
        ("Linux", "debian", "12"),
        ("Linux", "debian", "13"),
    ],
)
def test_supported_systems_pass(tmp_path, system):
    assert machine_setup.prerequisites(FakeProbe(tmp_path, system=system)) is None


def test_the_first_failing_check_wins(tmp_path):
    probe = FakeProbe(tmp_path, uid=0, python=(3, 8), user_manager=False)
    with pytest.raises(machine_setup.Failure) as raised:
        machine_setup.prerequisites(probe)
    assert raised.value.cause == "run_as_root"


def test_every_cause_the_program_can_report_is_one_the_app_has():
    source = Path(machine_setup.__file__).read_text()
    used = set(re.findall(r'Failure\(\s*"([a-z_]+)"', source))
    used |= set(re.findall(r'"cause": "([a-z_]+)"', source))
    used |= set(re.findall(r'cause = "([a-z_]+)"', source))
    used |= set(re.findall(r'"(service_failed|check_failed|cannot_reach_hub|unknown)"', source))
    assert used and used <= set(pairing.CAUSES), sorted(used - set(pairing.CAUSES))


# ---- the program, with a scripted hub ---------------------------------------------------------


def run(tmp_path, *, probe=None, hub=None, make=None, code=CODE, argv=None):
    lines = []
    probe = probe or FakeProbe(tmp_path)
    hub = hub or ScriptedHub()
    status = machine_setup.main(
        argv if argv is not None else [encode(ENVELOPE)],
        code=code,
        probe=probe,
        hub=hub,
        make_installer=make or (lambda *a: pytest.fail("the installer must not run")),
        out=lines.append,
    )
    return status, lines, hub, probe


def test_a_failed_check_is_reported_and_nothing_is_installed(tmp_path):
    status, lines, hub, probe = run(tmp_path, probe=FakeProbe(tmp_path, python=(3, 9)))
    assert status == 1
    assert hub.calls == [
        (
            "check",
            {
                "workspace_id": ENVELOPE["workspace_id"],
                "pairing_id": ENVELOPE["pairing_id"],
                "code": CODE,
                "report": {"step": "prerequisites", "state": "failed", "cause": "missing_python"},
            },
        )
    ]
    text = "\n".join(lines)
    assert "Python 3.10 or later is required" in text and "Nothing was changed" in text
    assert probe.confirmations == 0 and not (tmp_path / ".commonplace-runner").exists()


def test_the_code_is_never_printed(tmp_path):
    for facts in ({}, {"python": (3, 9)}, {"uid": 0}):
        _, lines, _, _ = run(tmp_path, probe=FakeProbe(tmp_path, **facts), make=_refuse_install)
        assert CODE not in "\n".join(lines)


def _refuse_install(*args):
    raise RuntimeError("stop before the installer")


def test_a_refused_code_is_explained_in_the_apps_words(tmp_path):
    answer = {
        "usable": False,
        "reason": "used",
        **{"text": pairing.REASONS["used"][0], "next": pairing.REASONS["used"][1]},
    }
    status, lines, hub, probe = run(tmp_path, hub=ScriptedHub({"check": answer}))
    assert status == 1
    assert lines[-2:] == [pairing.REASONS["used"][0], pairing.REASONS["used"][1]]
    assert probe.confirmations == 0


def test_an_unreachable_hub_is_said_plainly(tmp_path):
    status, lines, _, _ = run(tmp_path, hub=ScriptedHub({"check": machine_setup.Unreachable()}))
    assert status == 1
    assert any("could not reach" in line for line in lines) and any(
        "internet connection" in line for line in lines
    )


@pytest.mark.parametrize(
    "argv,code",
    [
        ([], CODE),
        (["not-an-envelope"], CODE),
        ([encode(ENVELOPE)], ""),
        ([encode(ENVELOPE)], "short"),
        ([encode(ENVELOPE), "x"], CODE),
    ],
)
def test_a_broken_command_stops_before_asking_anyone(tmp_path, argv, code):
    status, lines, hub, _ = run(tmp_path, argv=argv, code=code)
    assert status == 2 and hub.calls == [] and "Copy the whole command again" in "".join(lines)


def test_linger_off_asks_for_one_confirmation_and_stops_if_refused(tmp_path):
    probe = FakeProbe(tmp_path, linger=False, enable_linger=False)
    status, lines, hub, _ = run(tmp_path, probe=probe)
    assert status == 1 and probe.confirmations == 1
    assert hub.calls[-1][1]["report"] == {
        "step": "prerequisites",
        "state": "failed",
        "cause": "missing_privilege",
    }
    assert any("one administrator confirmation" in line for line in lines)
    assert any("sudo loginctl enable-linger builder" in line for line in lines)


# ---- a fixture machine, end to end ------------------------------------------------------------


class RouteHub:
    """The real pairing handlers behind in-memory streams: what the machine's HTTP calls reach."""

    def __init__(self, db):
        def principal_for(h):
            value = h.headers.get("Authorization", "")
            return db.authenticate_token(value[7:]) if value.startswith("Bearer ") else None

        self.gateway = SimpleNamespace(db=db, principal_for=principal_for)
        self.calls = []

    def _call(self, handler_fn, path, body, token=None):
        raw = json.dumps(body).encode()
        headers = {"Content-Length": str(len(raw))}
        if token:
            headers["Authorization"] = "Bearer " + token
        h = SimpleNamespace(
            path=path,
            command="POST",
            close_connection=False,
            headers=headers,
            rfile=io.BytesIO(raw),
            wfile=io.BytesIO(),
            send_header=lambda *a: None,
            end_headers=lambda: None,
        )
        h.send_response = lambda status: setattr(h, "status", status)
        handler_fn(self.gateway, h)
        self.calls.append((path, h.status))
        return json.loads(h.wfile.getvalue())

    def check(self, body):
        return self._call(pairing_routes._check, pairing_routes.CHECK, body)

    def status(self, token, body):
        return self._call(pairing_routes._status, pairing_routes.STATUS, body, token)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("the machine fixture must not open a socket or a real user manager")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(installer.LiveHost, "__init__", refuse)
    monkeypatch.setattr(pairing, "_check_wings", lambda *a: None)
    monkeypatch.setattr(pairing.urls, "APP_URL", HUB)
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    user = db.create_user("owner@example.test")
    workspace = db.create_workspace("fresh", user)
    owner = sessions.person_principal(db, user, workspace)
    pairing.ensure(db)
    enrollment.set_enabled(db, owner, True)
    created, item, setup = pairing.create(
        db,
        None,
        owner,
        {
            "request_id": "req-1",
            "kind": "runner",
            "label": "build-box",
            "projects": ["demo"],
            "topics": ["worker"],
            "wings": ["shared"],
        },
    )
    assert created
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    host = FakeHost()
    transport = RouteTransport(db)

    def make(envelope, out):
        intent = installer.intent_from(
            SimpleNamespace(
                hub=envelope["hub"],
                workspace=envelope["workspace_id"],
                label=envelope["label"],
                manifest_digest=envelope["manifest_digest"],
                enrollment_id=envelope["enrollment_id"],
            )
        )
        return intent, installer.Installer(home, host, transport, out=out)

    try:
        yield SimpleNamespace(
            db=db,
            owner=owner,
            item=item,
            setup=setup,
            home=home,
            host=host,
            make=make,
            hub=RouteHub(db),
            probe=FakeProbe(home),
            transport=transport,
        )
    finally:
        db.close()


def view(m):
    return pairing.get(m.db, m.owner, m.item["id"], owner=True)["pairing"]


def test_one_command_and_one_accept_take_a_clean_machine_to_ready(machine):
    m = machine
    lines = []
    status = machine_setup.main(
        [m.setup["envelope"]],
        code=m.setup["code"],
        probe=m.probe,
        hub=m.hub,
        make_installer=m.make,
        out=lines.append,
    )
    assert status == 0, lines
    item = view(m)
    assert item["state"] == "connected"
    assert [s["state"] for s in item["steps"]] == ["ok"] * 4
    # What the person compares: the app shows what the machine printed.
    printed = next(line for line in lines if line.startswith("Machine fingerprint:"))
    assert printed == "Machine fingerprint: " + item["fingerprint"]
    assert lines[-2].startswith("Done on this machine")
    result = pairing.accept(
        m.db,
        m.owner,
        item["id"],
        {
            "request_id": "accept-1",
            "expected_version": item["version"],
            "identity_digest": item["identity_digest"],
        },
    )
    assert result["state"] == "ready" and result["state_label"] == "Set up and checked"
    row = registry.get_machine(m.db, m.owner.workspace_id, "build-box")
    assert row["enabled"] == 1 and row["binding_state"] == "unbound"


def test_the_fixture_machine_never_touches_ssh_and_the_code_never_lands_in_a_file(machine):
    m = machine
    lines = []
    machine_setup.main(
        [m.setup["envelope"]],
        code=m.setup["code"],
        probe=m.probe,
        hub=m.hub,
        make_installer=m.make,
        out=lines.append,
    )
    assert m.host.commands and all(
        c in ("reload", "enable", "enable-linger") for c in m.host.commands
    )
    assert CODE not in "\n".join(lines) and m.setup["code"] not in "\n".join(lines)
    for path in m.home.rglob("*"):
        if path.is_file():
            assert m.setup["code"].encode() not in path.read_bytes(), path
    for path in m.home.rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0, (path, oct(mode))


def test_the_hub_hears_the_checks_before_the_code_is_spent(machine):
    m = machine
    machine_setup.main(
        [m.setup["envelope"]],
        code=m.setup["code"],
        probe=m.probe,
        hub=m.hub,
        make_installer=m.make,
        out=lambda line: None,
    )
    routes = [path for path, _ in m.hub.calls]
    assert routes[0] == pairing_routes.CHECK
    exchange = [i for i, (route, _) in enumerate(m.transport.calls) if route == installer.EXCHANGE]
    assert exchange and routes[:1] == [pairing_routes.CHECK]
    assert routes.count(pairing_routes.STATUS) == 3


def test_a_failed_service_start_shows_its_cause_and_a_rerun_finishes(machine):
    m = machine

    class Stubborn(FakeHost):
        broken = True

        def change(self, action):
            if action == "enable" and self.broken:
                raise OSError("unit refused")
            super().change(action)

    m.host = Stubborn()
    lines = []
    args = dict(code=m.setup["code"], probe=m.probe, hub=m.hub, out=lines.append)

    def make(envelope, out):
        intent = installer.intent_from(
            SimpleNamespace(
                hub=envelope["hub"],
                workspace=envelope["workspace_id"],
                label=envelope["label"],
                manifest_digest=envelope["manifest_digest"],
                enrollment_id=envelope["enrollment_id"],
            )
        )
        return intent, installer.Installer(m.home, m.host, m.transport, out=out)

    assert machine_setup.main([m.setup["envelope"]], make_installer=make, **args) == 1
    item = view(m)
    assert item["state"] == "failed"
    assert item["cause"]["step"] == "service" and item["cause"]["code"] == "service_failed"
    assert item["cause"]["next"] == pairing.CAUSES["service_failed"][1]
    assert machine_setup.AGAIN in lines and not any("continues from" in line for line in lines)
    m.host.broken = False
    assert machine_setup.main([m.setup["envelope"]], make_installer=make, **args) == 0
    assert view(m)["state"] == "connected"


def test_a_check_that_fails_after_install_is_reported_as_the_check_step(machine):
    m = machine

    def make(envelope, out):
        intent, inst = m.make(envelope, out)
        original = inst.verify

        def verify(*a, **k):
            if k.get("check_service", True) and getattr(inst, "_verified_once", False):
                raise installer.InstallError("verification failed")
            inst._verified_once = True
            return original(*a, **k)

        inst.verify = verify
        return intent, inst

    lines = []
    status = machine_setup.main(
        [m.setup["envelope"]],
        code=m.setup["code"],
        probe=m.probe,
        hub=m.hub,
        make_installer=make,
        out=lines.append,
    )
    assert status == 1
    # The final-check failure prints its reason in the window too, not only in the app.
    assert "Reason: verification failed" in lines and machine_setup.AGAIN in lines
    item = view(m)
    assert item["state"] == "failed" and item["cause"]["step"] == "check"
    assert item["cause"]["code"] == "check_failed"


def test_a_machine_that_fails_a_check_is_shown_in_the_app_without_spending_the_code(machine):
    m = machine
    status = machine_setup.main(
        [m.setup["envelope"]],
        code=m.setup["code"],
        probe=FakeProbe(m.home, user_manager=False),
        hub=m.hub,
        make_installer=_refuse_install,
        out=lambda line: None,
    )
    assert status == 1
    item = view(m)
    assert item["state"] == "failed" and item["cause"]["code"] == "no_service_manager"
    assert item["cause"]["text"] == pairing.CAUSES["no_service_manager"][0]
    # Fixing it and running the same command again works: the code was never spent.
    status = machine_setup.main(
        [m.setup["envelope"]],
        code=m.setup["code"],
        probe=m.probe,
        hub=m.hub,
        make_installer=m.make,
        out=lambda line: None,
    )
    assert status == 0 and view(m)["state"] == "connected"


def test_running_the_same_command_again_on_an_installed_machine_changes_nothing(machine):
    m = machine
    args = dict(code=m.setup["code"], probe=m.probe, hub=m.hub, make_installer=m.make)
    assert machine_setup.main([m.setup["envelope"]], out=lambda line: None, **args) == 0
    before = {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()}
    lines = []
    assert machine_setup.main([m.setup["envelope"]], out=lines.append, **args) == 0
    assert any("Continuing the setup" in line for line in lines)
    assert {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()} == before
    assert view(m)["state"] == "connected"


def test_a_spent_code_is_not_a_resume_for_another_enrollment(machine):
    m = machine
    args = dict(code=m.setup["code"], probe=m.probe, hub=m.hub)
    assert (
        machine_setup.main(
            [m.setup["envelope"]], make_installer=m.make, out=lambda line: None, **args
        )
        == 0
    )
    manifest = m.home / ".commonplace-runner" / "manifest.json"
    journal = json.loads(manifest.read_text())
    journal["intent"]["enrollment_id"] = "enr_somebody_else"
    manifest.write_text(json.dumps(journal))
    lines = []
    status = machine_setup.main(
        [m.setup["envelope"]], make_installer=_refuse_install, out=lines.append, **args
    )
    assert status == 1 and pairing.REASONS["used"][0] in lines


def test_a_used_code_is_refused_on_the_machine_with_the_apps_reason(machine):
    m = machine
    assert (
        machine_setup.main(
            [m.setup["envelope"]],
            code=m.setup["code"],
            probe=m.probe,
            hub=m.hub,
            make_installer=m.make,
            out=lambda line: None,
        )
        == 0
    )
    other = m.home.parent / "other-machine"
    other.mkdir()
    lines = []
    status = machine_setup.main(
        [m.setup["envelope"]],
        code=m.setup["code"],
        probe=FakeProbe(other),
        hub=m.hub,
        make_installer=_refuse_install,
        out=lines.append,
    )
    assert status == 1
    assert pairing.REASONS["used"][0] in lines and pairing.REASONS["used"][1] in lines


# ---- the launcher -----------------------------------------------------------------------------


def test_the_launcher_embeds_the_program_byte_for_byte_and_parses():
    text = pairing_routes.launcher_text()
    start = text.index("<<'COMMONPLACE_SETUP_END'\n") + len("<<'COMMONPLACE_SETUP_END'\n")
    end = text.rindex("COMMONPLACE_SETUP_END\n")
    assert text[start:end] == Path(machine_setup.__file__).read_text()
    assert text.count("COMMONPLACE_SETUP_END") == 2 + 0
    for shell in ("sh", "dash", "bash"):
        found = subprocess.run(["which", shell], capture_output=True, text=True).stdout.strip()
        if found:
            subprocess.run([found, "-n"], input=text, text=True, check=True)
    assert CODE not in text and "cmmn_" not in text


@pytest.fixture
def stubs(tmp_path):
    """A PATH with only the tools the launcher needs, a recording curl and no python3."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("cat", "sed", "tr", "base64", "mktemp", "rm", "id"):
        (bin_dir / tool).symlink_to(
            subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
        )
    log = tmp_path / "curl.log"
    stdin_log = tmp_path / "curl.stdin"
    curl = bin_dir / "curl"
    curl.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {log}\ncat > {stdin_log}\n")
    curl.chmod(0o755)
    return SimpleNamespace(bin=bin_dir, log=log, stdin=stdin_log, home=tmp_path)


def launch(stubs, envelope, code=CODE):
    env = {"PATH": str(stubs.bin), "HOME": str(stubs.home), "TMPDIR": str(stubs.home)}
    return subprocess.run(
        ["/bin/sh", "-c", pairing_routes.launcher_text(), "machine-setup", envelope],
        input=code + "\n",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("old", [False, True])
def test_a_missing_or_old_python_is_reported_with_its_cause(stubs, old):
    if old:
        python = stubs.bin / "python3"
        python.write_text("#!/bin/sh\nexit 1\n")
        python.chmod(0o755)
    hub = {**ENVELOPE, "hub": "http://127.0.0.1:9", "kind": "runner"}
    result = launch(stubs, encode(hub))
    assert result.returncode == 1
    assert "Python 3.10 or later is required." in result.stdout
    assert "sudo apt install python3" in result.stdout and "Nothing was changed" in result.stdout
    sent = json.loads(stubs.stdin.read_text())
    assert sent == {
        "workspace_id": ENVELOPE["workspace_id"],
        "pairing_id": ENVELOPE["pairing_id"],
        "code": CODE,
        "report": {"step": "prerequisites", "state": "failed", "cause": "missing_python"},
    }
    argv = stubs.log.read_text()
    assert CODE not in argv and "http://127.0.0.1:9/api/runner-pairing/check" in argv
    assert CODE not in result.stdout + result.stderr
    assert list(stubs.home.glob("commonplace-setup.*")) == []


@pytest.mark.parametrize(
    "hub", ["http://hub.example.test", "ftp://x", "https://a b", "https://hub.example.test;rm"]
)
def test_the_launcher_reports_only_to_a_safe_origin(stubs, hub):
    result = launch(stubs, encode({**ENVELOPE, "hub": hub}))
    assert result.returncode == 1 and not stubs.log.exists()


def test_the_embedded_program_runs_under_isolated_python_and_refuses_a_broken_command(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    result = subprocess.run(
        ["/bin/sh", "-c", pairing_routes.launcher_text(), "machine-setup", "not-an-envelope"],
        input=CODE + "\n",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert "Copy the whole command again from the app." in result.stdout
    assert CODE not in result.stdout + result.stderr
    assert list(tmp_path.glob("commonplace-setup.*")) == []


# ---- the route and the real hub ---------------------------------------------------------------


def test_the_launcher_is_public_identical_and_carries_no_tenant_data(flow):
    made = flow.made()
    status, headers = None, None
    from tests.runner_harness import _http

    status, headers, anonymous = _http(flow.hub.port, "GET", "/machine-setup.sh")
    assert status == 200 and headers["Content-Type"].startswith("text/x-shellscript")
    assert headers["Cache-Control"] == "no-store"
    status, _, signed_in = _http(
        flow.hub.port, "GET", "/machine-setup.sh", cookie=flow.owner["cookie"]
    )
    assert signed_in == anonymous == pairing_routes.launcher_text()
    for secret in (
        made["setup"]["code"],
        made["pairing"]["id"],
        flow.owner["workspace"],
        flow.owner["email"],
    ):
        assert secret not in anonymous


def test_a_machine_without_python_shows_up_in_the_app_as_failed_with_its_cause(flow, tmp_path):
    made = flow.made()
    values = flow.envelope(made)
    values["hub"] = f"http://127.0.0.1:{flow.hub.port}"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("cat", "sed", "tr", "base64", "mktemp", "rm", "curl"):
        (bin_dir / tool).symlink_to(
            subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
        )
    from tests.runner_harness import _http

    _, _, served = _http(flow.hub.port, "GET", "/machine-setup.sh")
    result = subprocess.run(
        ["/bin/sh", "-c", served, "machine-setup", encode(values)],
        input=made["setup"]["code"] + "\n",
        env={"PATH": str(bin_dir), "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1 and "Python 3.10 or later is required." in result.stdout
    item = flow.get(made)
    assert item["state"] == "failed"
    assert item["cause"] == {
        "step": "prerequisites",
        "code": "missing_python",
        "text": pairing.CAUSES["missing_python"][0],
        "next": pairing.CAUSES["missing_python"][1],
    }
    # The code was not spent: the same command works once Python is there.
    assert flow.check(made)[1]["usable"] is True


# ---- review round 1: the step a failure reached, a refusal that is not a lost connection -----------------


class FakeOpener:
    """The production Transport's HTTP opener, answering the way a real site or a dead network does."""

    def __init__(self, error, before=None):
        self.error, self.before = error, before

    def open(self, request, timeout=None):
        if self.before:
            self.before()
        raise self.error


def production_transport(error, before=None):
    transport = installer.Transport("https://hub.example.test")
    transport.opener = FakeOpener(error, before)
    return transport


def refusal():
    import urllib.error

    return urllib.error.HTTPError(
        "https://hub.example.test/api/runner-enrollment/exchange",
        403,
        "Forbidden",
        {},
        io.BytesIO(b'{"error": "enrollment refused"}'),
    )


def lost_connection():
    import urllib.error

    return urllib.error.URLError("network is unreachable")


def run_with(m, transport=None, lines=None):
    lines = [] if lines is None else lines

    def make(envelope, out):
        intent = installer.intent_from(
            SimpleNamespace(
                hub=envelope["hub"],
                workspace=envelope["workspace_id"],
                label=envelope["label"],
                manifest_digest=envelope["manifest_digest"],
                enrollment_id=envelope["enrollment_id"],
            )
        )
        return intent, installer.Installer(m.home, m.host, transport or m.transport, out=out)

    status = machine_setup.main(
        [m.setup["envelope"]],
        code=m.setup["code"],
        probe=m.probe,
        hub=m.hub,
        make_installer=make,
        out=lines.append,
    )
    return status, lines


def test_a_write_that_fails_before_the_code_is_spent_is_the_files_step_and_the_code_survives(
    machine, monkeypatch
):
    import errno

    m = machine
    real = installer.Installer.file

    def broken(self, path, data):
        if path == installer.KEY:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, path, data)

    monkeypatch.setattr(installer.Installer, "file", broken)
    status, lines = run_with(m)
    assert status == 1
    item = view(m)
    assert item["state"] == "failed"
    assert (item["cause"]["step"], item["cause"]["code"]) == ("files", "disk_full")
    assert item["cause"]["step"] != "prerequisites", "the checks passed; the write is what failed"
    assert machine_setup.AGAIN in lines and not any("continues" in line for line in lines)
    assert not any("one-time code was used" in line for line in lines)
    assert m.hub.check(
        {"workspace_id": m.owner.workspace_id, "pairing_id": m.item["id"], "code": m.setup["code"]}
    )["usable"]
    monkeypatch.setattr(installer.Installer, "file", real)
    assert run_with(m)[0] == 0 and view(m)["state"] == "connected"


def test_a_write_that_fails_after_the_code_is_spent_says_so_and_the_page_is_never_told_it_is_fine(
    machine, monkeypatch
):
    m = machine
    real = installer.Installer.file

    def broken(self, path, data):
        if path == installer.DELIVERY:
            raise OSError(5, "Input/output error")
        return real(self, path, data)

    monkeypatch.setattr(installer.Installer, "file", broken)
    status, lines = run_with(m)
    assert status == 1
    assert "The one-time code was used, but this machine could not save what it needs." in lines
    assert not any("continues" in line for line in lines)
    # The app cannot be told, and it shows Approve/Decline only until 15 minutes pass: the window must not send the
    # person to a Set up again that is not there yet.
    assert machine_setup.AGAIN not in lines
    assert list(machine_setup.SPENT_AGAIN) == lines[-2:]
    item = view(m)
    assert item["stalled"] is False and not item.get("retry")
    # Nothing could be reported (the machine holds no credential) and the app says nothing false.
    assert item["state"] == "connected"
    assert [s["state"] for s in item["steps"]][1:] == ["pending"] * 3, (
        "nothing past the checks is reported"
    )
    assert item["can_accept"] is False
    # The window says why, in the installer's fixed words, and never the code.
    assert "Reason: Input/output error" in lines
    assert m.setup["code"] not in "\n".join(lines)


def test_a_refused_exchange_is_not_called_a_lost_connection(machine):
    m = machine
    status, lines = run_with(m, production_transport(refusal()))
    text = "\n".join(lines)
    assert status == 1
    assert "could not reach" not in text and "delivery unavailable" not in text
    assert "The site did not accept the code." in lines and machine_setup.AGAIN in lines
    assert view(m)["state"] == "waiting", "a refusal reports nothing as a failed step"


def test_a_site_error_at_the_exchange_is_unavailable_not_a_refused_code(machine):
    import urllib.error

    m = machine
    broken = urllib.error.HTTPError("u", 503, "Unavailable", {}, io.BytesIO(b"{}"))
    status, lines = run_with(m, production_transport(broken))
    assert status == 1
    assert "The site did not accept the code." not in lines
    item = view(m)
    assert item["state"] == "failed" and item["cause"]["code"] == "cannot_reach_hub"
    assert m.hub.check(
        {"workspace_id": m.owner.workspace_id, "pairing_id": m.item["id"], "code": m.setup["code"]}
    )["usable"], "the code was never spent"


def test_a_code_that_expired_between_the_check_and_the_exchange_says_expired(machine, monkeypatch):
    m = machine
    now = control_db._now()
    transport = production_transport(
        refusal(),
        before=lambda: monkeypatch.setattr(control_db, "_now", lambda: now + timedelta(minutes=20)),
    )
    status, lines = run_with(m, transport)
    assert status == 1
    assert pairing.REASONS["expired"][0] in lines and pairing.REASONS["expired"][1] in lines
    assert not any("could not reach" in line for line in lines)
    assert view(m)["state"] == "expired"


def test_a_dead_network_at_the_exchange_is_a_lost_connection_and_is_reported_as_one(machine):
    m = machine
    status, lines = run_with(m, production_transport(lost_connection()))
    assert status == 1 and machine_setup.AGAIN in lines
    item = view(m)
    assert item["state"] == "failed" and item["cause"]["code"] == "cannot_reach_hub"
    # The checks passed and the files were never written: the files step, not the pre-flight one.
    assert item["cause"]["step"] == "files"


def test_set_up_again_after_a_half_finished_install_replaces_what_the_first_try_left(machine):
    class Stubborn(FakeHost):
        broken = True

        def change(self, action):
            if action == "enable" and self.broken:
                raise OSError("unit refused")
            super().change(action)

    m = machine
    m.host = Stubborn()
    status, _ = run_with(m)
    assert status == 1 and view(m)["cause"]["step"] == "service"
    # The owner chooses Set up again: the first enrollment is cancelled and a new command is made.
    first = view(m)
    pairing.close(
        m.db, m.owner, m.item["id"], {"request_id": "again", "expected_version": first["version"]}
    )
    _, item, setup = pairing.create(
        m.db,
        None,
        m.owner,
        {
            "request_id": "req-2",
            "kind": "runner",
            "label": "build-box",
            "projects": ["demo"],
            "topics": ["worker"],
            "wings": ["shared"],
        },
    )
    m.host.broken = False
    m.item, m.setup = item, setup
    status, lines = run_with(m)
    assert status == 0, lines
    assert "Removing the earlier unfinished setup on this machine ..." in lines
    assert view(m)["state"] == "connected" and [s["state"] for s in view(m)["steps"]] == ["ok"] * 4


# ---- review round 2: the window gives the reason, a healthy earlier machine survives, the right step ------


def test_a_failure_after_the_code_was_spent_prints_its_reason_in_the_window(machine):
    """The app says the reason is on the last lines of the window, so the window must have it."""

    class Refusing(FakeHost):
        def change(self, action):
            if action == "enable":
                raise installer.InstallError("noninteractive linger permission is required")
            super().change(action)

    m = machine
    m.host = Refusing()
    status, lines = run_with(m)
    assert status == 1
    assert "Reason: noninteractive linger permission is required" in lines
    assert machine_setup.AGAIN in lines
    assert lines.index("Reason: noninteractive linger permission is required") < lines.index(
        machine_setup.AGAIN
    )
    assert m.setup["code"] not in "\n".join(lines) and "cmmn_" not in "\n".join(lines)
    assert view(m)["cause"]["step"] == "service"


def test_a_service_that_is_not_running_after_install_says_so_in_the_window(machine):
    class Silent(FakeHost):
        def change(self, action):
            super().change(action)
            if action == "enable":
                self.active = False

    m = machine
    m.host = Silent()
    # verify() checks the service too, so the install itself refuses; the window carries the installer's reason.
    status, lines = run_with(m)
    assert status == 1
    assert any(line.startswith("Reason: ") for line in lines)
    assert machine_setup.AGAIN in lines


def test_a_healthy_approved_earlier_machine_is_never_uninstalled_by_a_new_command(machine):
    m = machine
    assert run_with(m)[0] == 0
    item = view(m)
    pairing.accept(
        m.db,
        m.owner,
        item["id"],
        {
            "request_id": "ok",
            "expected_version": item["version"],
            "identity_digest": item["identity_digest"],
        },
    )
    before = {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()}
    token = (m.home / ".commonplace-runner" / "machine.token").read_text()
    _, second, setup = pairing.create(
        m.db,
        None,
        m.owner,
        {
            "request_id": "req-2",
            "kind": "runner",
            "label": "second-box",
            "projects": ["demo"],
            "topics": ["worker"],
            "wings": ["shared"],
        },
    )
    first_item = m.item
    m.item, m.setup = second, setup
    status, lines = run_with(m)
    assert status == 1
    assert "This account already runs machine build-box." in lines
    assert (
        "Remove it under Compute first, then run the command again. Nothing was changed." in lines
    )
    assert not any("Removing the earlier" in line for line in lines)
    assert {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()} == before, (
        "no file was touched"
    )
    # The earlier machine is still approved, enabled and reporting.
    m.item = first_item
    assert view(m)["state"] == "ready"
    assert m.hub.status(token.strip(), {"step": "probe"})["ok"] is True
    row = registry.get_machine(m.db, m.owner.workspace_id, "build-box")
    assert row["enabled"] == 1 and row["revoked_at"] is None


def test_an_earlier_setup_the_site_cannot_confirm_is_left_alone(machine):
    class Silent:
        def __init__(self, inner):
            self.inner = inner

        def check(self, body):
            return self.inner.check(body)

        def status(self, token, body):
            raise machine_setup.Unreachable

    m = machine
    assert run_with(m)[0] == 0
    first_item = m.item
    pairing.close(
        m.db, m.owner, first_item["id"], {"request_id": "c", "expected_version": view(m)["version"]}
    )
    _, second, setup = pairing.create(
        m.db,
        None,
        m.owner,
        {
            "request_id": "req-3",
            "kind": "runner",
            "label": "build-box",
            "projects": ["demo"],
            "topics": ["worker"],
            "wings": ["shared"],
        },
    )
    m.item, m.setup = second, setup
    m.hub = Silent(m.hub)
    status, lines = run_with(m)
    assert status == 1 and (m.home / ".commonplace-runner" / "manifest.json").exists()
    assert (
        "This machine could not reach the site to check the earlier setup on this account." in lines
    )


def test_a_missing_runner_program_asks_for_it_instead_of_pointing_at_a_guide(tmp_path):
    with pytest.raises(machine_setup.Failure) as raised:
        machine_setup.prerequisites(FakeProbe(tmp_path, imports={"cryptography"}))
    text = raised.value.message + " " + raised.value.then
    assert "Ask the person who runs this site for the runner program" in text
    assert "guide" not in text.lower() and "does not install it yet" in text
    assert "guide" not in pairing.CAUSES["missing_runner_program"][1].lower()
    assert "Ask the person who runs this site" in pairing.CAUSES["missing_runner_program"][1]


def test_a_server_error_is_no_answer_and_a_local_failure_is_still_said_when_the_site_is_silent(
    tmp_path,
):
    import urllib.error

    hub = machine_setup.Hub("https://hub.example.test")

    class Down:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b'{"error": "unavailable"}'))

    hub.opener = Down()
    with pytest.raises(machine_setup.Unreachable):
        hub.check({"workspace_id": "w", "pairing_id": "p", "code": CODE})
    lines = []
    status = machine_setup.main(
        [encode(ENVELOPE)],
        code=CODE,
        probe=FakeProbe(tmp_path, python=(3, 9)),
        hub=ScriptedHub({"check": machine_setup.Unreachable()}),
        make_installer=_refuse_install,
        out=lines.append,
    )
    assert status == 1
    assert "Python 3.10 or later is required." in lines, (
        "the machine's own problem is said even when the site is silent"
    )


def copy_setup_for_site(setup, origin):
    """The same command, but for another site: the envelope names that site."""
    envelope = machine_setup.parse_envelope(setup["envelope"]) | {"hub": origin}
    raw = base64.urlsafe_b64encode(json.dumps(envelope, separators=(",", ":")).encode())
    return setup | {"envelope": raw.decode().rstrip("=")}


def test_a_command_for_another_site_never_sends_the_old_token_and_changes_nothing(machine):
    m = machine
    assert run_with(m)[0] == 0
    token = (m.home / ".commonplace-runner" / "machine.token").read_text().strip()
    before = {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()}
    units = list(m.host.__dict__.items())
    _, new_item, new_setup = pairing.create(
        m.db,
        None,
        m.owner,
        {
            "request_id": "req-other",
            "kind": "runner",
            "label": "other-box",
            "projects": ["demo"],
            "topics": ["worker"],
            "wings": ["shared"],
        },
    )
    second = copy_setup_for_site(new_setup, "https://other-site.example")
    m.item = new_item
    sent, real = [], m.hub

    class Watching:
        def check(self, body):
            sent.append(("check", None, body))
            return real.check(body)

        def status(self, token_, body):
            sent.append(("status", token_, body))
            return real.status(token_, body)

    m.hub = Watching()
    m.setup = second
    status, lines = run_with(m)
    m.hub = real
    assert status == 1
    assert not [row for row in sent if row[1] == token], (
        "the old credential went to a different site"
    )
    assert any("another site" in line and HUB in line for line in lines), lines
    assert "Nothing was changed." in " ".join(lines)
    assert not any("Removing the earlier" in line for line in lines)
    assert {str(p): p.read_bytes() for p in m.home.rglob("*") if p.is_file()} == before
    assert list(m.host.__dict__.items()) == units
    assert token not in "\n".join(lines)
