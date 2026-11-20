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


def _shot_of(page, selector, name):
    """A capture of one section (the overview above the guide), for the evidence folder only."""
    target = os.environ.get("COMPUTE_PAIRING_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.locator(selector).first.screenshot(path=str(Path(target) / f"{name}.png"))


def _no_sideways_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (
        "page scrolls sideways"
    )


def _nothing_past_the_edge(page):
    """Reflow (WCAG 1.4.10): no control or line of text reaches past the screen, even where the page does not scroll."""
    wide = page.evaluate(
        """() => [...document.querySelectorAll('.compute-pr button, .compute-pr a, .compute-pr input, .compute-pr select, .compute-pr label, .compute-pr p')]
            .filter(el => el.getClientRects().length)
            .map(el => [el.textContent.trim().slice(0, 40) || el.name || el.id, el.getBoundingClientRect()])
            .filter(([, r]) => r.right > window.innerWidth + 0.5 || r.left < -0.5)
            .map(([name, r]) => name + ' ' + Math.round(r.left) + '-' + Math.round(r.right))"""
    )
    assert not wide, f"past the edge of the screen: {wide}"


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
        self.steps(
            credentials
        )  # the server approves only a machine that has finished all four steps
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
        assert (
            command.startswith(" S=$(curl -fsS '")
            and "&& printf '%s' '" in command
            and "| sh -c" in command
        )
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
        # The machine has not finished and printed its fingerprint: the sheet waits, and Approve says why it waits.
        expect(sheet).to_contain_text("Waiting for the machine to print its fingerprint.")
        _shot(page, f"{label}-05-connected-waiting")
        # The first machine arrived while the page was open: the overview already shows it (0 -> 1).
        expect(page.locator("[data-ov-summary]")).to_contain_text("1 runner", timeout=20000)
        _shot_of(page, "[data-ov]", f"{label}-05c-overview-first-machine")
        fx.steps(credentials)
        expect(page.locator(".compute-pr-fingerprint")).to_be_visible(timeout=20000)
        groups = page.locator(".compute-pr-fingerprint").inner_text().split()
        assert len(groups) == FINGERPRINT_GROUPS and all(len(g) == 8 for g in groups)
        expect(sheet).to_contain_text("Fixture")
        _shot(page, f"{label}-05b-connected-approve")
        _no_sideways_scroll(page)
        if size == "phone":
            _assert_touch_targets(page)
        page.get_by_role("button", name="Approve this machine").click()
        expect(section).to_contain_text("Set up and checked", timeout=20000)
        expect(page.get_by_role("button", name="Approve this machine")).to_have_count(0)
        expect(section).to_contain_text("Provider and attestation: not done yet")
        expect(section).to_contain_text("only after a provider is connected")
        assert "running" not in section.inner_text().lower().replace("keep services running", "")
        _shot(page, f"{label}-07-ready")
        _no_sideways_scroll(page)
        # Back to the list: the machine is one row with its state.
        page.get_by_role("button", name="Back to the list").click()
        expect(section.locator(".compute-pr-row")).to_contain_text("Set up and checked")
        # The overview reads again when the machine changes state: no "No machine is set up yet" beside a machine row.
        expect(page.locator("[data-ov-summary]")).to_contain_text("1 runner", timeout=20000)
        expect(page.locator('[data-ov-slot="state"]')).not_to_contain_text(
            "No machine is set up yet"
        )
        expect(page.locator("[data-ov]")).not_to_contain_text("No machine is set up yet")
        _shot(page, f"{label}-08-list")
        _shot_of(page, "[data-ov]", f"{label}-09-overview-setup-only-row")

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
        expect(section).to_contain_text(pairing.STATES["connected"][3])
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
            expect(section).to_contain_text(pairing.STATES["failed"][3], timeout=20000)
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
        expect(section).to_contain_text(pairing.CAUSES["service_failed"][1])
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
def test_a_machine_that_connected_and_never_finished_reads_stalled_for_owner_and_member(
    served, ui, size
):
    fx = served
    _, setup = fx.add(label="stuck-box")
    credentials = fx.connect(setup)
    fx.report(credentials, "prerequisites")
    # The code was spent a quarter of an hour ago and three of the four steps never came.
    with fx.db._lock:
        fx.db._conn.execute(
            "UPDATE runner_enrollments SET consumed_at=?",
            (control_db._iso(control_db._now() - timedelta(minutes=16)),),
        )

    def owner(page, label, context):
        section = _section(page)
        expect(section.locator(".compute-pr-row")).to_contain_text("This setup has not finished.")
        _shot(page, f"{label}-stalled-list")
        _open_pairing(page, "stuck-box")
        expect(section).to_contain_text("This setup has not finished.")
        expect(section).to_contain_text("Remove this machine and set it up again.")
        expect(section.get_by_role("button", name="Approve this machine")).to_have_count(0)
        expect(section.get_by_role("button", name="Set up again")).to_be_visible()
        expect(section.get_by_role("button", name="Remove this machine")).to_be_visible()
        _shot(page, f"{label}-stalled-owner")
        _no_sideways_scroll(page)
        _nothing_past_the_edge(page)
        if size == "phone":
            _assert_touch_targets(page)

    def member(page, label, context):
        section = _section(page)
        _open_pairing(page, "stuck-box")
        expect(section).to_contain_text("This setup has not finished.")
        expect(section).to_contain_text("The owner can remove this machine and set it up again.")
        for name in ("Approve this machine", "Set up again", "Remove this machine"):
            expect(section.get_by_role("button", name=name)).to_have_count(0)
        _shot(page, f"{label}-stalled-member")
        _no_sideways_scroll(page)
        _nothing_past_the_edge(page)

    _run(fx, ui, size, owner)
    _run(fx, ui, size, member, cookie=fx.member_cookie)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_machine_without_the_runner_program_says_so_before_its_code_is_spent(served, ui, size):
    fx = served
    item, setup = fx.add(label="bare-box")
    values = json.loads(
        base64.urlsafe_b64decode(setup["envelope"] + "=" * (-len(setup["envelope"]) % 4))
    )
    status, result = fx.call(
        "POST",
        "/api/runner-pairing/check",
        {
            "workspace_id": values["workspace_id"],
            "pairing_id": values["pairing_id"],
            "code": setup["code"],
            "report": {
                "step": "prerequisites",
                "state": "failed",
                "cause": "missing_runner_program",
            },
        },
    )
    assert status == 200, result

    def body(page, label, context):
        section = _section(page)
        _open_pairing(page, "bare-box")
        expect(section).to_contain_text("A step failed")
        expect(section).to_contain_text(pairing.CAUSES["missing_runner_program"][0])
        expect(section).to_contain_text(pairing.CAUSES["missing_runner_program"][1])
        expect(section.get_by_role("button", name="Approve this machine")).to_have_count(0)
        _shot(page, f"{label}-missing-runner-program")
        _no_sideways_scroll(page)
        _nothing_past_the_edge(page)
        if size == "phone":
            _assert_touch_targets(page)

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
        confirm = page.get_by_role("group", name="Is this not your machine?")
        expect(confirm).to_be_visible()
        # The safe answer takes focus: a second Enter must never remove access.
        expect(page.get_by_role("button", name="Go back")).to_be_focused()
        expect(section).not_to_contain_text("Approve this machine?")
        _shot(page, f"{label}-decline-confirm")
        if size == "phone":
            _assert_touch_targets(page)
        page.keyboard.press("Enter")
        expect(confirm).to_have_count(0)
        assert fx.alive(credentials), (
            "Enter on the default answer leaves the machine's access alone"
        )
        expect(page.get_by_role("button", name="This is not my machine")).to_be_focused()
        assert fx.alive(credentials), "keeping it leaves the machine's access alone"
        page.keyboard.press("Tab")
        page.get_by_role("button", name="This is not my machine").click()
        page.keyboard.press("Escape")
        expect(confirm).to_have_count(0)
        page.get_by_role("button", name="This is not my machine").click()
        page.get_by_role("button", name="Yes, this is not my machine").click()
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
            "Copying is not available here. The command above is selected: copy it with your keyboard or the long-press menu."
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
    fx.add(label="waiting-box")

    def body(page, label, context):
        label = f"{ui}-{name}"
        _shot(page, f"{label}-list")
        for pair in ("wide-box", "done-box", "broken-box", "waiting-box"):
            _open_pairing(page, pair)
            _no_sideways_scroll(page)
            _nothing_past_the_edge(page)
            if touch:
                _assert_touch_targets(page)
            _shot(page, f"{label}-{pair}")
            # The two confirmations a machine can reach from here.
            confirming = {
                "wide-box": ("This is not my machine", "decline"),
                "done-box": ("Remove this machine", "remove"),
                "broken-box": ("Remove this machine", "remove"),
                "waiting-box": None,
            }[pair]
            if confirming:
                page.get_by_role("button", name=confirming[0]).click()
                expect(page.get_by_role("button", name="Go back")).to_be_focused()
                _no_sideways_scroll(page)
                _nothing_past_the_edge(page)
                if touch:
                    _assert_touch_targets(page)
                _shot(page, f"{label}-{pair}-{confirming[1]}-confirm")
                page.get_by_role("button", name="Go back").click()
            page.get_by_role("button", name="Back to the list").click()
        page.get_by_role("button", name="Add a machine").click()
        expect(page.locator("#compute-pr-heading")).to_be_focused()
        # The clean form first (nothing typed, nothing refused), then the same form after a refusal.
        expect(page.locator("[data-pr-summary]")).to_be_empty()
        _shot(page, f"{label}-form")
        small = page.locator(
            ".compute-pr small:visible, .compute-pr .compute-quiet:visible"
        ).evaluate_all("els => els.map(el => parseFloat(getComputedStyle(el).fontSize))")
        if touch:
            assert small and min(small) >= 14, (
                f"hints are 14px on touch screens: {sorted(small)[:3]}"
            )
        page.get_by_role("button", name="Create the command").click()
        _no_sideways_scroll(page)
        if touch:
            _assert_touch_targets(page)
        _assert_nothing_under_a_fixed_bar(page)
        # The error summary and the first field are both on screen: no bar covers them.
        expect(page.locator("[data-pr-summary]")).to_be_visible()
        _shot(page, f"{label}-form-errors")

    _run(fx, ui, "desktop", body, viewport=viewport, touch=touch)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("name,viewport,touch", VIEWPORTS)
def test_a_members_states_fit_every_real_width(served, ui, name, viewport, touch):
    fx = served
    _, setup = fx.add(label="wide-box")
    fx.connect(setup)
    fx.ready(label="done-box")
    fx.add(label="waiting-box")

    def body(page, label, context):
        label = f"{ui}-{name}"
        _shot(page, f"{label}-member-list")
        for pair in ("wide-box", "done-box", "waiting-box"):
            _open_pairing(page, pair)
            _no_sideways_scroll(page)
            _nothing_past_the_edge(page)
            if touch:
                _assert_touch_targets(page)
            _shot(page, f"{label}-member-{pair}")
            page.get_by_role("button", name="Back to the list").click()

    _run(fx, ui, "desktop", body, viewport=viewport, touch=touch, cookie=fx.member_cookie)


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


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_the_page_says_where_to_run_the_command_and_what_approval_grants(served, ui, size):
    """M1 and M2: three numbered lines above the command; the sheet says what approval grants and what it does not."""
    fx = served

    def body(page, label, context):
        section = _section(page)
        page.get_by_role("button", name="Add a machine").click()
        _fill_form(page)
        page.get_by_role("button", name="Create the command").click()
        steps = section.locator(".compute-pr-run li")
        expect(steps).to_have_count(3)
        expect(steps.nth(0)).to_contain_text("Open a terminal on that machine")
        expect(steps.nth(1)).to_contain_text("Ctrl+Shift+V")
        expect(steps.nth(1)).to_contain_text("Do not type sudo in front of it")
        expect(steps.nth(2)).to_contain_text("This page updates by itself")
        above = page.evaluate(
            "() => document.querySelector('.compute-pr-run').getBoundingClientRect().bottom"
            " <= document.querySelector('#compute-pr-command').getBoundingClientRect().top"
        )
        assert above, "the numbered lines are above the command"
        _shot(page, f"{label}-04b-run-steps")
        credentials = fx.connect(setup_of(page.locator("[data-pr-command]").inner_text()))
        expect(section).to_contain_text(
            "Machine connected, waiting for your approval", timeout=20000
        )
        # The machine has not finished: the fingerprint is not offered yet and Approve says why it waits.
        expect(section).to_contain_text("Waiting for the machine to print its fingerprint.")
        approve = page.get_by_role("button", name="Approve this machine")
        expect(approve).to_have_attribute("aria-disabled", "true")
        expect(section).to_contain_text("You can approve once the machine has finished its setup")
        approve.click(force=True)  # a press while it waits changes nothing
        expect(section).to_contain_text("Machine connected, waiting for your approval")
        _shot(page, f"{label}-05a-connected-waiting-fingerprint")
        fx.steps(credentials)
        expect(page.locator(".compute-pr-fingerprint")).to_be_visible(timeout=20000)
        expect(approve).not_to_have_attribute("aria-disabled", "true")
        text = section.locator(".compute-pr-detail").inner_text()
        assert "Approving lets this machine use the access listed above." in text
        assert "does not connect a provider" in text and "an owner attests it" in text
        grants = page.evaluate(
            "() => document.querySelector('[data-pr-grants]').getBoundingClientRect().bottom"
            " <= document.querySelector('[data-pr-action=approve]').getBoundingClientRect().top"
        )
        assert grants, "the sentence is above the buttons"
        for old in (
            "May start work on it",
            "Cards at once",
            "What it may do alone",
            "No agent yet",
        ):
            assert old not in text, old
        expect(section).to_contain_text("Who can start work on it")
        expect(section).to_contain_text("nobody yet")
        _shot(page, f"{label}-05b-approve-sheet")

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_remove_asks_first_and_the_second_enter_removes_nothing(served, ui, size):
    """M7 for the Remove dialog (the decline dialog is in the test above)."""
    fx = served
    item, credentials = fx.ready()

    def body(page, label, context):
        section = _section(page)
        _open_pairing(page)
        page.get_by_role("button", name="Remove this machine").click()
        expect(page.get_by_role("group", name="Remove this machine?")).to_be_visible()
        expect(page.get_by_role("button", name="Go back")).to_be_focused()
        _shot(page, f"{label}-remove-confirm")
        page.keyboard.press("Enter")  # the default answer is Go back
        expect(page.get_by_role("group", name="Remove this machine?")).to_have_count(0)
        expect(page.get_by_role("button", name="Remove this machine")).to_be_focused()
        assert fx.alive(credentials), "Enter on the default answer removed nothing"
        expect(section).to_contain_text("Set up and checked")
        page.get_by_role("button", name="Remove this machine").click()
        page.get_by_role("button", name="Yes, remove it").click()
        expect(section).to_contain_text("Access removed")
        assert not fx.alive(credentials)

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_set_up_again_after_a_failure_cancels_the_old_setup_and_makes_a_new_command(
    served, ui, size
):
    """M3: for a failed machine Set up again is the single next step, and it works (the old name is freed first)."""
    fx = served
    item, setup = fx.add()
    credentials = fx.connect(setup)
    fx.report(credentials, "service", "failed", "service_failed")

    def body(page, label, context):
        section = _section(page)
        _open_pairing(page)
        expect(section).to_contain_text("A step failed")
        expect(section).to_contain_text(pairing.CAUSES["service_failed"][1])
        again = section.get_by_role("button", name="Set up again")
        remove = section.get_by_role("button", name="Remove this machine")
        first = again.evaluate(
            "(a, b) => Boolean(a.compareDocumentPosition(document.querySelector('[data-pr-action=remove]')) & Node.DOCUMENT_POSITION_FOLLOWING)"
        )
        assert first and remove.count() == 1, "Set up again comes before Remove this machine"
        steps = section.locator(".compute-pr-steps li")
        expect(steps.nth(3)).to_contain_text("Not run")
        _shot(page, f"{label}-failed-set-up-again")
        again.click()
        expect(page.locator("#compute-pr-heading")).to_be_focused()
        expect(page.locator("#pr_label")).to_have_value("build-box")
        assert not fx.alive(credentials), "the old machine's access was removed first"
        page.get_by_role("button", name="Create the command").click()
        expect(page.locator("[data-pr-command]")).to_be_visible()
        expect(section).to_contain_text("Waiting for the machine")
        _shot(page, f"{label}-set-up-again-new-command")

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
def test_a_check_that_finds_nothing_new_keeps_the_commands_focus_and_selection(served, ui):
    """M6: wait through one no-change poll, then the command block is the same node, still focused and selected."""
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="Add a machine").click()
        _fill_form(page)
        page.get_by_role("button", name="Create the command").click()
        command = page.locator("#compute-pr-command")
        expect(command).to_be_visible()
        page.evaluate(
            "() => { const pre = document.querySelector('#compute-pr-command'); window.__pre = pre; pre.focus();"
            " const range = document.createRange(); range.selectNodeContents(pre);"
            " const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); }"
        )
        clock = page.locator("[data-pr-checked]")
        before = clock.inner_text()
        # The page checks every three seconds; the clock line changes when a check has come back (no fixed wait).
        expect(clock).not_to_have_text(before, timeout=20000)
        assert page.evaluate("document.querySelector('#compute-pr-command') === window.__pre"), (
            "the command was rebuilt"
        )
        assert (
            page.evaluate("document.activeElement && document.activeElement.id")
            == "compute-pr-command"
        )
        assert page.evaluate("getSelection().toString().trim()") == command.inner_text().strip()
        # A second check, still nothing new.
        second = clock.inner_text()
        expect(clock).not_to_have_text(second, timeout=20000)
        assert page.evaluate("document.querySelector('#compute-pr-command') === window.__pre")
        assert (
            page.evaluate("document.activeElement && document.activeElement.id")
            == "compute-pr-command"
        )

    _run(fx, ui, "desktop", body)


@pytest.mark.parametrize("ui", UIS)
def test_the_copy_fallback_keeps_its_selection_through_a_poll(served, ui):
    """F: with no clipboard, Copy selects the command; a check that finds nothing new must not wipe that selection."""
    fx = served

    def body(page, label, context):
        page.evaluate(
            "Object.defineProperty(navigator, 'clipboard', {value: {writeText: () => Promise.reject(new Error('no'))}})"
        )
        page.get_by_role("button", name="Add a machine").click()
        _fill_form(page)
        page.get_by_role("button", name="Create the command").click()
        page.get_by_role("button", name="Copy the command").click()
        note = page.locator("[data-pr-copy-note]")
        expect(note).to_have_text(
            "Copying is not available here. The command above is selected: copy it with your keyboard or the long-press menu."
        )
        page.evaluate(
            "() => { window.__pre = document.querySelector('#compute-pr-command');"
            " window.__note = document.querySelector('[data-pr-copy-note]'); }"
        )
        clock = page.locator("[data-pr-checked]")
        # Three checks come back with nothing new (the clock line changes each time; nothing waits a fixed time).
        for _ in range(3):
            before = clock.inner_text()
            expect(clock).not_to_have_text(before, timeout=20000)
        command = page.locator("#compute-pr-command")
        assert page.evaluate("document.querySelector('#compute-pr-command') === window.__pre"), (
            "rebuilt"
        )
        assert (
            page.evaluate("document.activeElement && document.activeElement.id")
            == "compute-pr-command"
        )
        assert page.evaluate("getSelection().toString().trim()") == command.inner_text().strip()
        assert page.evaluate("document.querySelector('[data-pr-copy-note]') === window.__note"), (
            "the status region was not re-inserted"
        )
        expect(note).to_have_text(
            "Copying is not available here. The command above is selected: copy it with your keyboard or the long-press menu."
        )
        _shot(page, f"{label}-copy-fallback-selected")

    _run(fx, ui, "desktop", body, clipboard=False)


@pytest.mark.parametrize("ui", UIS)
def test_a_successful_copy_does_not_rebuild_the_status_region(served, ui):
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="Add a machine").click()
        _fill_form(page)
        page.get_by_role("button", name="Create the command").click()
        page.get_by_role("button", name="Copy the command").click()
        note = page.locator("[data-pr-copy-note]")
        expect(note).to_have_text("Copied. Paste it on the machine.")
        page.evaluate("() => { window.__note = document.querySelector('[data-pr-copy-note]'); }")
        clock = page.locator("[data-pr-checked]")
        before = clock.inner_text()
        expect(clock).not_to_have_text(before, timeout=20000)
        assert page.evaluate("document.querySelector('[data-pr-copy-note]') === window.__note")
        expect(note).to_have_text("Copied. Paste it on the machine.")

    _run(fx, ui, "desktop", body)


@pytest.mark.parametrize("ui", UIS)
def test_the_run_steps_have_numerals_and_the_command_is_a_named_region(served, ui):
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="Add a machine").click()
        _fill_form(page)
        page.get_by_role("button", name="Create the command").click()
        expect(page.locator(".compute-pr-run")).to_be_visible()
        style = page.evaluate(
            "() => getComputedStyle(document.querySelector('.compute-pr-run')).listStyleType"
        )
        assert style == "decimal", "the numbered lines show their numbers"
        region = page.get_by_role("region", name="Run this on the machine")
        expect(region).to_have_count(1)
        expect(region).to_have_attribute("id", "compute-pr-command")
        _shot(page, f"{label}-run-steps-numerals")

    _run(fx, ui, "desktop", body)


@pytest.mark.parametrize("ui", UIS)
def test_the_form_fits_between_700_and_999_pixels(served, ui):
    """R3-4: the long "What it may do without asking" label and its list are not clipped on a tablet width."""
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="Add a machine").click()
        select = page.locator("#pr_mode")
        expect(select).to_be_visible()
        clipped = page.evaluate(
            "() => { const s = document.querySelector('#pr_mode'); const l = document.querySelector('label[for=pr_mode]');"
            " return s.getBoundingClientRect().width < 280 || l.scrollWidth > l.clientWidth + 1; }"
        )
        assert not clipped
        _no_sideways_scroll(page)
        _shot(page, f"{label}-form-tablet")

    _run(fx, ui, "desktop", body, viewport=(820, 1000), touch=True)


@pytest.mark.parametrize("ui", UIS)
def test_a_check_that_cannot_reach_the_server_is_said_once_and_cleared_when_it_recovers(served, ui):
    fx = served
    fx.add()

    def body(page, label, context):
        _open_pairing(page)
        notice = page.locator("[data-pr-notice]")
        page.route("**/api/compute/pairings/pair_*", lambda route: route.abort())
        expect(notice).to_contain_text("Could not check just now", timeout=20000)
        said = notice.inner_text()
        expect(page.locator("[data-pr-checked]")).to_contain_text("Last checked at")
        assert _section(page).inner_text().count("Could not check just now") == 1
        page.unroute("**/api/compute/pairings/pair_*")
        expect(notice).to_have_text("", timeout=20000)
        assert said.startswith("Could not check just now")

    _run(fx, ui, "desktop", body)
