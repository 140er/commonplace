"""Add a machine in a real browser, Classic and Deck, desktop and phone, owner and member.

Synthetic local data only: no remote connection, no real machine. The machine side is played by the test through the
server's own functions (exchange, step reports), so the page sees exactly what a real machine would cause. Needs the
Playwright package and a Chromium cache. Playwright is deliberately not a declared dependency (as for the other browser
suites), so run

    CP_BROWSER_E2E=1 uv run --with playwright==1.64.1 pytest tests/test_compute_pairing_browser.py -rs

Without Playwright every case here is reported as skipped with a reason; with ``CP_BROWSER_E2E=1`` (a browser evidence
run) a missing Playwright fails instead, so the cases can never be skipped by accident. Nothing sleeps: the page checks
on the machine by itself, and the test waits for what it expects to appear. Set COMPUTE_PAIRING_SHOTS to a directory to
also save a viewport-sized screenshot of every state.
"""

import base64
import importlib.util
import json
import os
import re
import threading
from datetime import timedelta
from http.client import HTTPConnection
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from commonplace import control_db, sessions
from commonplace.boards.service import BoardService
from commonplace.compute import pairing
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolContext
from commonplace.gateway import Gateway
from commonplace.runner import enrollment
from commonplace.workers import WorkerSupervisor
from tests.test_runner_enrollment import decrypt, public

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if os.environ.get("CP_BROWSER_E2E") == "1":
    from playwright import sync_api as playwright_api  # an evidence run never skips
elif HAS_PLAYWRIGHT:
    from playwright import sync_api as playwright_api
else:
    playwright_api = None
pytestmark = pytest.mark.skipif(
    playwright_api is None,
    reason="browser cases need the playwright package (see this file's docstring for the command)",
)
sync_playwright = playwright_api.sync_playwright if playwright_api else None
expect = playwright_api.expect if playwright_api else None

SIZES = {"desktop": (1280, 800), "phone": (390, 844)}
TOUCH = 44
UIS = ["deck", "classic"]
SCREENS = ["desktop", "phone"]
WINGS = ["fixture", "shared"]
FINGERPRINT_GROUPS = 8


def _shot(page, name):
    target = os.environ.get("COMPUTE_PAIRING_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f"{name}.png"))


def _no_sideways_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (
        "page scrolls sideways"
    )


@pytest.fixture(scope="module")
def machine_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


class Fixture:
    """A real in-process hub with one owner and one member, and a stand-in for the machine.

    Everything the stand-in does goes over HTTP, as a real machine and a real owner's browser would: only the
    server's own threads touch its database.
    """

    def call(self, method, path, body=None, *, token=None, cookie=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if cookie:
            headers["Cookie"] = f"{sessions.COOKIE}={cookie}"
            headers["Origin"] = self.origin
        connection = HTTPConnection(self.origin.removeprefix("http://"), timeout=30)
        try:
            connection.request(method, path, None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            connection.close()

    def add(self, **changes):
        body = {
            "request_id": f"req-{next(self.counter)}",
            "kind": "runner",
            "label": "build-box",
            "projects": ["demo"],
            "topics": ["worker"],
            "wings": ["fixture"],
            **changes,
        }
        status, result = self.call("POST", "/api/compute/pairings", body, cookie=self.cookie)
        assert status == 201, result
        return result["pairing"], result["setup"]

    def connect(self, setup):
        """What the installer does with the page's command: spend its code and open the machine."""
        values = json.loads(
            base64.urlsafe_b64decode(setup["envelope"] + "=" * (-len(setup["envelope"]) % 4))
        )
        status, envelope = self.call(
            "POST",
            "/api/runner-enrollment/exchange",
            {
                "workspace_id": values["workspace_id"],
                "machine_label": values["label"],
                "manifest_digest": values["manifest_digest"],
                "code": setup["code"],
                "installation_id": "install-" + values["pairing_id"],
                "public_key": public(self.key),
            },
        )
        assert status == 201, envelope
        return decrypt(self.key, envelope)

    def report(self, credentials, step, state="ok", cause=None):
        body = {"step": step, **({"state": state} if step != "heartbeat" else {})}
        if cause:
            body["cause"] = cause
        status, result = self.call(
            "POST", "/api/runner/pairing-status", body, token=credentials["machine_token"]
        )
        assert status == 200, result
        return result

    def alive(self, credentials):
        """Does the machine's own credential still work? (A heartbeat is the lightest thing it may say.)"""
        status, _ = self.call(
            "POST",
            "/api/runner/pairing-status",
            {"step": "heartbeat"},
            token=credentials["machine_token"],
        )
        return status == 200

    def steps(self, credentials, *steps):
        for step in steps or pairing.STEPS:
            self.report(credentials, step)

    def view(self, item):
        status, result = self.call("GET", "/api/compute/pairings/" + item["id"], cookie=self.cookie)
        assert status == 200, result
        return result["pairing"]

    def ready(self, **changes):
        """A machine that connected, was approved and passed its checks (no browser involved)."""
        item, setup = self.add(**changes)
        credentials = self.connect(setup)
        view = self.view(item)
        status, result = self.call(
            "POST",
            f"/api/compute/pairings/{item['id']}/accept",
            {
                "request_id": "accept-" + item["id"],
                "expected_version": view["version"],
                "identity_digest": view["identity_digest"],
            },
            cookie=self.cookie,
        )
        assert status == 200, result
        self.steps(credentials)
        return item, credentials


def _serve(tmp_path, monkeypatch, key, *, enabled=True):
    for name in ("CHAT", "HOLD"):
        monkeypatch.setenv(f"COMMONPLACE_{name}_WORKER", "off")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    for name in ("COMMONPLACE_ALLOWED_HOSTS", "COMMONPLACE_APP_URL", "COMMONPLACE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pairing, "_check_wings", lambda *args: None)
    monkeypatch.setattr(pairing.urls, "APP_URL", "https://hub.example.test")
    monkeypatch.setattr("commonplace.web.agents.grantable_wings", lambda *args: list(WINGS))
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    gateway = Gateway(db, WorkerSupervisor(str(tmp_path), launcher=object()))
    owner = db.create_user("owner@synthetic.test")
    member = db.create_user("member@synthetic.test")
    wid = db.create_workspace("Pairing browser fixture", owner)
    db.add_member(wid, member, "member", actor=owner)
    cookie = sessions.start_session(db, owner, wid)
    member_cookie = sessions.start_session(db, member, wid)
    principal = sessions.session_principal(db, cookie)
    BoardService(ToolContext(principal=principal, gateway=gateway)).board_add(
        "compute", wing="fixture"
    )
    pairing.ensure(db)
    owner_now = sessions.person_principal(db, owner, wid)
    if enabled:
        enrollment.set_enabled(db, owner_now, True)
    server = gateway.build_server("127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fx = Fixture()
    fx.db, fx.gateway, fx.principal, fx.owner, fx.workspace, fx.key = (
        db,
        gateway,
        owner_now,
        owner,
        wid,
        key,
    )
    fx.origin, fx.cookie, fx.member_cookie = origin, cookie, member_cookie
    fx.counter = iter(range(1, 1000))
    try:
        yield fx
    finally:
        server.shutdown()
        server.server_close()
        db.close()


@pytest.fixture
def served(tmp_path, monkeypatch, machine_key):
    yield from _serve(tmp_path, monkeypatch, machine_key)


@pytest.fixture
def served_off(tmp_path, monkeypatch, machine_key):
    yield from _serve(tmp_path, monkeypatch, machine_key, enabled=False)


def _open(browser, fx, ui, size, cookie=None, viewport=None, touch=None, clipboard=True):
    width, height = viewport or SIZES[size]
    phone = width < 700
    touch = phone if touch is None else touch
    context = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch
    )
    if clipboard:
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=fx.origin)
    context.add_cookies([{"name": sessions.COOKIE, "value": cookie or fx.cookie, "url": fx.origin}])
    page = context.new_page()
    page.set_default_timeout(45000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "response",
        lambda r: (
            errors.append(f"{r.status} {r.url}")
            if "/api/compute/pairings" in r.url and r.status >= 500
            else None
        ),
    )
    route = "#/compute/management" if ui == "deck" else "#/compute"
    page.goto(f"{fx.origin}/app/?ui={ui}{route}")
    try:
        page.locator("#compute-pairing").wait_for()
    except playwright_api.TimeoutError:
        raise AssertionError(
            "the Add a machine section did not appear: " + page.inner_text("body")[:800]
        ) from None
    expect(page.get_by_role("button", name="Refresh")).to_be_enabled()
    return context, page, errors


def _run(fx, ui, size, body, cookie=None, viewport=None, touch=None, clipboard=True):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size, cookie, viewport, touch, clipboard)
            body(page, f"{ui}-{size}", context)
            assert errors == []
            context.close()
        finally:
            browser.close()


def _section(page):
    return page.locator(".compute-pr")


def _assert_touch_targets(page):
    """Every control is at least 44px tall; a tick box or radio is a target through its whole label."""
    controls = page.locator(
        ".compute-pr button:visible, .compute-pr input:visible:not([type=checkbox]):not([type=radio]),"
        " .compute-pr select:visible, .compute-pr summary:visible, .compute-pr .compute-check:visible"
    )
    heights = controls.evaluate_all("els => els.map(el => el.getBoundingClientRect().height)")
    assert heights and min(heights) >= TOUCH, (
        f"touch targets under {TOUCH}px: {sorted(heights)[:3]}"
    )
    boxes = page.locator(
        ".compute-pr input[type=checkbox]:visible, .compute-pr input[type=radio]:visible"
    )
    sizes = boxes.evaluate_all("els => els.map(el => el.getBoundingClientRect().height)")
    assert not sizes or min(sizes) >= 24, (
        f"tick boxes are 24px on touch screens: {sorted(sizes)[:3]}"
    )
    fonts = page.locator(
        ".compute-pr input:visible:not([type=checkbox]):not([type=radio]), .compute-pr select:visible"
    ).evaluate_all("els => els.map(el => parseFloat(getComputedStyle(el).fontSize))")
    assert not fonts or min(fonts) >= 16, (
        "inputs are 16px on touch screens so the browser does not zoom"
    )


def _assert_nothing_under_a_fixed_bar(page):
    """The page has no sticky or fixed bar of its own: nothing in the section is positioned over a field."""
    bad = page.evaluate(
        "() => [...document.querySelectorAll('.compute-pr *')].filter(el =>"
        " ['sticky', 'fixed'].includes(getComputedStyle(el).position)).length"
    )
    assert bad == 0


def _fill_form(page, label="build-box", projects="demo", topics="worker", wing="Fixture"):
    page.get_by_label("Name", exact=False).first.fill(label)
    page.locator("#pr_projects").fill(projects)
    page.locator("#pr_topics").fill(topics)
    if wing:
        page.get_by_label(wing, exact=True).check()


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_owner_goes_from_add_machine_to_ready_with_one_command_and_one_approval(served, ui, size):
    fx = served

    def body(page, label, context):
        section = _section(page)
        expect(section).to_contain_text("No machine has been added this way yet")
        assert (
            section.get_by_text("does not install the runner program itself", exact=False).count()
            == 1
        )
        _shot(page, f"{label}-01-empty")
        page.get_by_role("button", name="Add a machine").click()
        expect(page.locator("#compute-pr-heading")).to_be_focused()
        # The second kind is shown and says why it cannot be chosen.
        expect(page.get_by_role("radio", name="Script target")).to_be_disabled()
        expect(section).to_contain_text("Not available yet")
        _shot(page, f"{label}-02-form")
        # Nothing is announced or marked while typing; Create names every missing field once.
        page.get_by_role("button", name="Create the command").click()
        expect(page.locator("[data-pr-summary]")).to_be_focused()
        expect(page.locator("[data-pr-summary]")).to_contain_text("4 things need fixing")
        expect(page.locator("#pr_label")).to_have_attribute("aria-invalid", "true")
        expect(page.locator("#pr-error-label")).to_contain_text("Give the machine a name.")
        _shot(page, f"{label}-03-form-errors")
        if size == "phone":
            _assert_touch_targets(page)
        _assert_nothing_under_a_fixed_bar(page)
        _no_sideways_scroll(page)
        _fill_form(page)
        expect(page.locator("#pr_label")).not_to_have_attribute("aria-invalid", "true")
        page.get_by_role("button", name="Create the command").click()
        expect(page.locator("#compute-pr-heading")).to_be_focused()
        expect(page.locator("[data-pr-command]")).to_contain_text("machine-setup.sh")
        expect(section).to_contain_text("Waiting for the machine")
        command = page.locator("[data-pr-command]").inner_text()
        assert command.startswith(" printf '%s' '") and "| sh -c" in command
        _shot(page, f"{label}-04-waiting-command")
        page.get_by_role("button", name="Copy the command").click()
        expect(page.locator("[data-pr-copy-note]")).to_have_text("Copied. Paste it on the machine.")
        assert page.evaluate("navigator.clipboard.readText()") == command
        _no_sideways_scroll(page)
        if size == "phone":
            _assert_touch_targets(page)
        # The machine connects; the page notices by itself and asks for one approval.
        credentials = fx.connect(setup_of(command))
        expect(section).to_contain_text(
            "Machine connected, waiting for your approval", timeout=20000
        )
        expect(page.locator("[data-pr-command]")).to_have_count(0)
        sheet = page.locator(".compute-pr-sheet")
        expect(sheet).to_contain_text("build-box")
        groups = page.locator(".compute-pr-fingerprint").inner_text().split()
        assert len(groups) == FINGERPRINT_GROUPS and all(len(g) == 8 for g in groups)
        expect(sheet).to_contain_text("Fixture")
        _shot(page, f"{label}-05-connected-approve")
        _no_sideways_scroll(page)
        if size == "phone":
            _assert_touch_targets(page)
        page.get_by_role("button", name="Approve this machine").click()
        expect(section).to_contain_text("Approved")
        expect(page.get_by_role("button", name="Approve this machine")).to_have_count(0)
        _shot(page, f"{label}-06-approved")
        fx.steps(credentials)
        expect(section).to_contain_text("Set up and checked", timeout=20000)
        expect(section).to_contain_text("Provider and attestation: not done yet")
        expect(section).to_contain_text("only after a provider is connected")
        assert "running" not in section.inner_text().lower().replace("keep services running", "")
        _shot(page, f"{label}-07-ready")
        _no_sideways_scroll(page)
        # Back to the list: the machine is one row with its state.
        page.get_by_role("button", name="Back to the list").click()
        expect(section.locator(".compute-pr-row")).to_contain_text("Set up and checked")
        _shot(page, f"{label}-08-list")

    _run(fx, ui, size, body)


def setup_of(command):
    """The code and envelope inside the command exactly as the page showed it."""
    code = re.search(r"printf '%s' '([A-Za-z0-9_-]{43})'", command)
    envelope = re.search(r"machine-setup '([A-Za-z0-9_-]+)'$", command)
    assert code and envelope, "the command has the documented shape"
    return {"code": code.group(1), "envelope": envelope.group(1)}


def _open_pairing(page, label="build-box"):
    page.get_by_role("button", name=f"Open {label}").click()
    expect(page.locator("#compute-pr-heading")).to_be_focused()


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_setup_that_is_off_is_turned_on_by_the_owner_and_read_by_a_member(served_off, ui, size):
    fx = served_off

    def member_off(page, label, context):
        section = _section(page)
        expect(section).to_contain_text(
            "Machine setup is off for this workspace. Ask the workspace owner to turn it on."
        )
        for name in ("Turn on machine setup", "Add a machine"):
            expect(section.get_by_role("button", name=name)).to_have_count(0)
        _shot(page, f"{label}-member-setup-off")

    _run(fx, ui, size, member_off, cookie=fx.member_cookie)

    def owner(page, label, context):
        section = _section(page)
        expect(section).to_contain_text("Machine setup is off for this workspace")
        expect(section.get_by_role("button", name="Add a machine")).to_have_count(0)
        _shot(page, f"{label}-00-setup-off")
        page.get_by_role("button", name="Turn on machine setup").click()
        expect(section.get_by_role("button", name="Add a machine")).to_be_visible()
        expect(page.locator("[data-pr-notice]")).to_have_text(
            "Machine setup is on. You can add a machine now."
        )

    _run(fx, ui, size, owner)
    fx.add()
    _run(
        fx,
        ui,
        size,
        lambda page, label, context: _member_checks(page, label),
        cookie=fx.member_cookie,
    )


def _member_checks(page, label):
    section = _section(page)
    expect(section).to_contain_text("Waiting for the machine")
    expect(section).to_contain_text("The owner has started adding this machine")
    for name in (
        "Add a machine",
        "Turn on machine setup",
        "Copy the command",
        "Approve this machine",
    ):
        expect(section.get_by_role("button", name=name)).to_have_count(0)
    assert "printf" not in section.inner_text() and "Fingerprint" not in section.inner_text()
    _shot(page, f"{label}-member-waiting")
    _no_sideways_scroll(page)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_member_reads_every_state_without_codes_fingerprints_or_controls(served, ui, size):
    fx = served
    item, setup = fx.add()
    credentials = fx.connect(setup)
    other, other_setup = fx.add(label="second-box")
    fx.connect(other_setup)

    def body(page, label, context):
        section = _section(page)
        expect(section.locator(".compute-pr-row")).to_have_count(2)
        expect(section).to_contain_text("The machine connected. The owner has to approve it.")
        for name in (
            "Add a machine",
            "Approve this machine",
            "This is not my machine",
            "Open build-box",
        ):
            if name.startswith("Open"):
                continue
            expect(section.get_by_role("button", name=name)).to_have_count(0)
        _shot(page, f"{label}-member-connected")
        page.get_by_role("button", name="Open build-box").click()
        expect(page.locator("#compute-pr-heading")).to_be_focused()
        text = section.inner_text()
        for hidden in ("Fingerprint", "May read", "Approve this machine", "printf"):
            assert hidden not in text, hidden
        fx.report(credentials, "service", "failed", "service_failed")
        try:
            expect(section).to_contain_text(
                "A setup step failed on this machine. The owner can retry it.", timeout=20000
            )
        except AssertionError as failure:
            seen = fx.call("GET", "/api/compute/pairings", cookie=fx.member_cookie)
            raise AssertionError(f"member read: {seen[0]} {str(seen[1])[:300]}") from failure
        expect(section).to_contain_text("Ask the workspace owner to retry it.")
        _shot(page, f"{label}-member-failed")
        _no_sideways_scroll(page)

    _run(fx, ui, size, body, cookie=fx.member_cookie)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_failed_step_shows_its_cause_and_next_step_and_recovers(served, ui, size):
    fx = served
    item, setup = fx.add()
    credentials = fx.connect(setup)
    fx.report(credentials, "prerequisites")
    fx.report(credentials, "files")

    def body(page, label, context):
        section = _section(page)
        _open_pairing(page)
        expect(section).to_contain_text("Machine connected, waiting for your approval")
        fx.report(credentials, "service", "failed", "service_failed")
        expect(section).to_contain_text("A step failed", timeout=20000)
        expect(section).to_contain_text(
            "Background service started failed. The background service did not start."
        )
        expect(section).to_contain_text(
            "Run the command again. If it fails again, set the machine up again."
        )
        expect(section.get_by_role("button", name="Approve this machine")).to_have_count(0)
        _shot(page, f"{label}-failed")
        _no_sideways_scroll(page)
        if size == "phone":
            _assert_touch_targets(page)
        # Running the same command again fixes it, and the page follows without a reload.
        fx.report(credentials, "service")
        fx.report(credentials, "check")
        expect(section).to_contain_text(
            "Machine connected, waiting for your approval", timeout=20000
        )
        expect(section.locator(".compute-pr-cause")).to_have_count(0)

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_an_expired_code_is_explained_and_set_up_again_keeps_the_values(served, ui, size):
    fx = served
    item, setup = fx.add(label="late-box", projects=["alpha", "beta"])
    with fx.db._lock:
        fx.db._conn.execute(
            "UPDATE runner_enrollments SET expires_at=?",
            (control_db._iso(control_db._now() - timedelta(minutes=1)),),
        )

    def body(page, label, context):
        section = _section(page)
        expect(section.locator(".compute-pr-row")).to_contain_text("Code expired")
        _shot(page, f"{label}-expired-list")
        _open_pairing(page, "late-box")
        expect(section).to_contain_text(
            "The code was not used within 15 minutes and no longer works."
        )
        expect(section).to_contain_text("Choose Set up again for a new command.")
        expect(section.get_by_role("button", name="Copy the command")).to_have_count(0)
        _shot(page, f"{label}-expired")
        section.get_by_role("button", name="Set up again").click()
        expect(page.locator("#compute-pr-heading")).to_be_focused()
        expect(page.locator("#pr_label")).to_have_value("late-box")
        expect(page.locator("#pr_projects")).to_have_value("alpha, beta")
        expect(page.get_by_label("Fixture", exact=True)).to_be_checked()
        _no_sideways_scroll(page)

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_this_is_not_my_machine_asks_first_and_removes_its_access(served, ui, size):
    fx = served
    item, setup = fx.add()
    credentials = fx.connect(setup)

    def body(page, label, context):
        section = _section(page)
        _open_pairing(page)
        page.get_by_role("button", name="This is not my machine").click()
        confirm = page.get_by_role("group", name="Remove this machine's access?")
        expect(confirm).to_be_visible()
        expect(page.get_by_role("button", name="Yes, remove its access")).to_be_focused()
        _shot(page, f"{label}-decline-confirm")
        if size == "phone":
            _assert_touch_targets(page)
        page.get_by_role("button", name="Keep it").click()
        expect(confirm).to_have_count(0)
        expect(page.get_by_role("button", name="This is not my machine")).to_be_focused()
        assert fx.alive(credentials), "keeping it leaves the machine's access alone"
        page.keyboard.press("Tab")
        page.get_by_role("button", name="This is not my machine").click()
        page.keyboard.press("Escape")
        expect(confirm).to_have_count(0)
        page.get_by_role("button", name="This is not my machine").click()
        page.get_by_role("button", name="Yes, remove its access").click()
        expect(section).to_contain_text("Not approved")
        assert not fx.alive(credentials), "its credentials stop working"
        _shot(page, f"{label}-declined")

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_an_offline_machine_is_shown_with_when_it_was_last_heard_from(served, ui, size):
    fx = served
    item, credentials = fx.ready()
    fx.report(credentials, "heartbeat")
    old = control_db._iso(control_db._now() - timedelta(minutes=30))
    with fx.db._lock:
        fx.db._conn.execute(
            "UPDATE machine_pairing_steps SET reported_at=? WHERE step='heartbeat'", (old,)
        )

    def body(page, label, context):
        section = _section(page)
        expect(section.locator(".compute-pr-row")).to_contain_text("Offline")
        _open_pairing(page)
        expect(section).to_contain_text("nothing has been heard for over 15 minutes")
        expect(section).to_contain_text("Last seen at")
        _shot(page, f"{label}-offline")
        fx.report(credentials, "heartbeat")
        page.get_by_role("button", name="Refresh").click()
        expect(section).to_contain_text("Set up and checked", timeout=20000)
        _no_sideways_scroll(page)

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
def test_a_copy_that_fails_says_so_and_selects_the_command(served, ui):
    fx = served

    def body(page, label, context):
        page.evaluate(
            "Object.defineProperty(navigator, 'clipboard', {value: {writeText: () => Promise.reject(new Error('no'))}})"
        )
        page.get_by_role("button", name="Add a machine").click()
        _fill_form(page)
        page.get_by_role("button", name="Create the command").click()
        page.get_by_role("button", name="Copy the command").click()
        expect(page.locator("[data-pr-copy-note]")).to_have_text(
            "Copying is not available here. Select the command above and copy it yourself."
        )
        command = page.locator("[data-pr-command]").inner_text()
        assert page.evaluate("window.getSelection().toString().trim()") == command.strip()
        _shot(page, f"{label}-copy-failed")

    _run(fx, ui, "desktop", body, clipboard=False)


@pytest.mark.parametrize("ui", UIS)
def test_the_whole_flow_works_from_the_keyboard(served, ui):
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="Add a machine").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#compute-pr-heading")).to_be_focused()
        page.locator("#pr_label").focus()
        page.keyboard.type("keyboard-box")
        page.keyboard.press("Tab")  # copy-from select
        page.keyboard.press("Tab")  # projects
        page.keyboard.type("demo")
        page.keyboard.press("Tab")
        page.keyboard.type("worker")
        page.keyboard.press("Tab")  # first wing
        page.keyboard.press("Space")
        page.keyboard.press("Escape")
        expect(page.locator("[data-pr-form]")).to_have_count(0)
        expect(page.locator("#compute-pairing")).to_be_focused()
        page.get_by_role("button", name="Add a machine").focus()
        page.keyboard.press("Enter")
        page.locator("#pr_label").focus()
        page.keyboard.type("keyboard-box")
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        page.keyboard.type("demo")
        page.keyboard.press("Tab")
        page.keyboard.type("worker")
        page.keyboard.press("Tab")
        page.keyboard.press("Space")
        page.locator("#pr_label").focus()
        page.keyboard.press("Enter")  # Enter in a field submits the form
        expect(page.locator("[data-pr-command]")).to_be_visible()
        # Every control has a name a screen reader can say.
        unnamed = page.locator(".compute-pr button:visible").evaluate_all(
            "els => els.filter(el => !(el.innerText || el.getAttribute('aria-label') || '').trim()).length"
        )
        assert unnamed == 0

    _run(fx, ui, "desktop", body)


VIEWPORTS = [
    ("320", (320, 640), True),
    ("390", (390, 844), True),
    ("768touch", (768, 1024), True),
    ("1280", (1280, 800), False),
]


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("name,viewport,touch", VIEWPORTS)
def test_every_state_fits_every_real_width_with_44px_targets_on_touch(
    served, ui, name, viewport, touch
):
    fx = served
    _, setup = fx.add(label="wide-box")
    fx.connect(setup)
    fx.ready(label="done-box")
    _, failing_setup = fx.add(label="broken-box")
    failing_credentials = fx.connect(failing_setup)
    fx.report(failing_credentials, "check", "failed", "check_failed")

    def body(page, label, context):
        label = f"{ui}-{name}"
        _shot(page, f"{label}-list")
        for pair in ("wide-box", "done-box", "broken-box"):
            _open_pairing(page, pair)
            _no_sideways_scroll(page)
            if touch:
                _assert_touch_targets(page)
            _shot(page, f"{label}-{pair}")
            page.get_by_role("button", name="Back to the list").click()
        page.get_by_role("button", name="Add a machine").click()
        page.get_by_role("button", name="Create the command").click()
        _no_sideways_scroll(page)
        if touch:
            _assert_touch_targets(page)
        _assert_nothing_under_a_fixed_bar(page)
        # The error summary and the first field are both on screen: no bar covers them.
        expect(page.locator("[data-pr-summary]")).to_be_visible()
        _shot(page, f"{label}-form-errors")
        small = page.locator(
            ".compute-pr small:visible, .compute-pr .compute-quiet:visible"
        ).evaluate_all("els => els.map(el => parseFloat(getComputedStyle(el).fontSize))")
        if touch:
            assert small and min(small) >= 14, (
                f"hints are 14px on touch screens: {sorted(small)[:3]}"
            )
        _shot(page, f"{label}-form")

    _run(fx, ui, "desktop", body, viewport=viewport, touch=touch)


def _swipe(context, page, x, start, end, steps=14):
    """A real touch drag through the browser's input pipeline."""
    cdp = context.new_cdp_session(page)
    cdp.send(
        "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": start}]}
    )
    for step in range(1, steps + 1):
        point = {"x": x, "y": start + (end - start) * step / steps}
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [point]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


def test_the_deck_phone_form_scrolls_by_touch(served):
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="Add a machine").click()
        expect(page.locator("#compute-pr-heading")).to_be_focused()
        before = page.evaluate("document.querySelector('#content').scrollTop")
        _swipe(context, page, 200, 700, 200)
        page.wait_for_function(
            "before => document.querySelector('#content').scrollTop > before", arg=before
        )
        # The last button of the form is reachable and not covered.
        page.get_by_role("button", name="Create the command").scroll_into_view_if_needed()
        expect(page.get_by_role("button", name="Create the command")).to_be_in_viewport()
        _shot(page, "deck-phone-form-scrolled")

    _run(fx, "deck", "phone", body)


@pytest.mark.parametrize("ui", UIS)
def test_a_background_check_keeps_focus_and_does_not_announce_again(served, ui):
    fx = served
    item, setup = fx.add()

    def body(page, label, context):
        _open_pairing(page)
        notice = page.locator("[data-pr-notice]")
        page.get_by_role("button", name="Cancel this setup").focus()
        credentials = fx.connect(setup)
        expect(_section(page)).to_contain_text(
            "Machine connected, waiting for your approval", timeout=20000
        )
        said = notice.inner_text()
        assert "Machine connected" in said
        # More checks arrive; the person is still on a real control and nothing is said twice.
        fx.report(credentials, "prerequisites")
        fx.report(credentials, "files")
        expect(_section(page).locator(".compute-pr-steps li").first).to_contain_text(
            "Done", timeout=20000
        )
        assert notice.inner_text() == said
        focus = page.evaluate(
            "document.activeElement?.dataset?.prAction || document.activeElement?.id || ''"
        )
        assert focus in ("decline", "approve", "compute-pr-heading"), focus

    _run(fx, ui, "desktop", body)
