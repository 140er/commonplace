"""The Compute overview in a real browser, Classic and Deck, desktop and phone, owner and member.

Synthetic local data only: no remote connection and nothing is started. Needs the Playwright package and a
Chromium cache, exactly like tests/test_compute_workspaces_browser.py (whose fixtures and helpers this file
reuses; see its docstring for the command). Without Playwright every case is reported as skipped with a reason;
with ``CP_BROWSER_E2E=1`` a missing Playwright fails instead, so the cases can never be skipped by accident.
Set COMPUTE_OVERVIEW_SHOTS to a directory to save a viewport-sized screenshot of every state
(desktop 1280x800, phone 390x844).
"""

import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from commonplace import sessions
from commonplace.compute import workspace_lifecycle as lifecycle
from commonplace.runner import drain
from tests.compute_overview_helpers import FakeExecutor, Fleet, iso, revoke_machine
from tests.test_compute_workspaces_browser import (  # noqa: F401 -- shared real-gateway fixtures and helpers
    SIZES,
    TOUCH,
    UIS,
    _no_sideways_scroll,
    _serve,
    _swipe,
    pytestmark,
)

if TYPE_CHECKING:
    from playwright.sync_api import Route, expect, sync_playwright
else:
    from tests.test_compute_workspaces_browser import expect, sync_playwright

SCREENS = ["desktop", "phone"]


@pytest.fixture
def served(tmp_path, monkeypatch):
    yield from _serve(tmp_path, monkeypatch)


@pytest.fixture
def served_empty(tmp_path, monkeypatch):
    yield from _serve(tmp_path, monkeypatch, machine=False)


OVERVIEW = "/api/compute/overview"
ROOT = "[data-ov-root]"


def _shot(page, name):
    target = os.environ.get("COMPUTE_OVERVIEW_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f"{name}.png"))


def _shot_of(locator, name):
    """A capture of one element at its full height (a row, a whole panel), not of the viewport."""
    target = os.environ.get("COMPUTE_OVERVIEW_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        locator.screenshot(path=str(Path(target) / f"{name}.png"))


class _Clock:
    now = property(lambda self: time.time())


def _env(fx):
    """What the unit-test Fleet expects, built from the browser fixture (a real clock, the real gateway)."""
    gateway = fx.store.gateway
    return SimpleNamespace(
        db=gateway.db,
        gateway=gateway,
        principal=fx.principal,
        workspace=fx.store.workspace_id,
        store=fx.store,
        clock=_Clock(),
    )


def _fleet(fx):
    env = _env(fx)
    fleet = Fleet(env)
    fx.env, fx.fleet = env, fleet
    return fleet


def _populate(fx):
    """A fleet in every state a person has to read: online and busy, slow, offline, never connected, draining,
    revoked; a target machine; cloud workspaces running, stopped, unknown, failed, requested, not started."""
    fleet = _fleet(fx)
    env = fx.env
    wings = {"wings": ["fixture"]}
    busy = fleet.enroll("build-runner", max_in_flight=3, **wings)
    fleet.seen(busy, 40)
    fleet.job(busy, "started", task=fleet.work(busy))  # its card is being worked on: in progress
    # A run that finished: reported ended, so it is not in progress, not full and not an unreported end.
    fleet.end(fleet.job(busy, "started", minutes_ago=300, task=fleet.work(busy)), minutes_ago=290)
    fleet.resources(busy, seconds_ago=90)
    fleet.spend(busy, micros=1_250_000, job="s1")
    fleet.spend(busy, unknown=True, job="s2")
    slow = fleet.enroll("review-runner", **wings)
    fleet.seen(slow, 400)
    offline = fleet.enroll("edge-runner", **wings)
    fleet.seen(offline, 1500)
    fleet.enroll("spare-runner", **wings)
    draining = fleet.enroll("night-runner", **wings)
    fleet.seen(draining, 20)
    fleet.job(draining, "started", task=fleet.work(draining), minutes_ago=5)  # drained and busy
    idle = fleet.enroll("idle-runner", **wings)
    fleet.seen(idle, 15)
    gone = fleet.enroll("retired-runner", **wings)
    fleet.seen(gone, 20)
    revoke_machine(env, gone)
    with fleet.store().transaction() as conn:
        for machine in (draining, idle):
            drain.change(
                conn,
                machine["machine_id"],
                draining=True,
                expected_seq=0,
                by="owner",
                now=iso(time.time() - 60),  # after the run it left alone started, not before it
            )
    node = fx.node["id"]
    executor = FakeExecutor(env)
    names = {}

    def ask(name, action, **changes):
        names[name] = fx.make(name=name, node_id=node, **changes)["id"]
        return names[name]

    def request(workspace_id, action):
        with fx.store._journal() as conn:
            life = lifecycle.life_of(conn, workspace_id, time.time())
            row = conn.execute(
                "SELECT version FROM cloud_workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
        lifecycle.request(
            fx.store,
            fx.principal,
            workspace_id,
            action,
            session_hash=fx.auth["session_hash"],
            expected_seq=life["seq"],
            expected_version=row["version"],
        )

    for name in ("Build box", "Docs box", "Old box", "Broken box"):
        ask(name, "start")
        if name == "Old box":
            # Everything about this one happened 1000 s ago, the audit trail included (it is append-only): the
            # request, the claim and the report agree, so no row says "just now" beside "last report 16 minutes ago".
            fx.store._now = lambda: time.time() - 1000
        request(names[name], "start")
        claim = executor.claim()
        if name == "Build box":
            executor.run(claim, "running")
        elif name == "Docs box":
            executor.run(claim, "running")
            request(names[name], "stop")
            executor.run(executor.claim(), "stopped")
        elif name == "Old box":
            executor.run(claim, "running")
            del fx.store._now
        else:
            executor.report(claim, "failed", reason="out_of_memory")
    ask("Queued box", "start")
    request(names["Queued box"], "start")
    ask("New box", "start")
    names["Unplaced box"] = fx.make(name="Unplaced box")["id"]
    # No executor is registered in production: the screen must say a request waits, so unregister it here.
    del fx.store.gateway._compute_workspace_executor
    return names


def _fold(page):
    """On a phone the overview starts folded to its summary sentence; a person opens it with one tap."""
    fold = page.locator("details[data-ov-fold]")
    # Wait for the first answer: the fold settles when the data arrives (open for a handful of items, closed for more).
    expect(page.locator("[data-ov-summary]")).not_to_have_text(
        "Loading the overview…", timeout=20000
    )
    if page.locator("details[data-ov-fold]").count() == 0:
        return  # nothing to fold (an empty or failed overview is plain text)
    if fold.get_attribute("open") is None:
        page.locator("[data-ov-fold-summary]").click()
        expect(fold).to_have_attribute("open", "")


def _go(page, fx, ui="deck"):  # the list has painted
    _fold(page)
    expect(page.locator(f"{ROOT} .compute-ov-row").first).to_be_visible(
        timeout=20000
    )  # a cold server is slow to answer


def _shown(page):
    return page.locator(ROOT).inner_text()


def _rows(page):
    return page.locator(f"{ROOT} .compute-ov-row")


def _row(page, name):
    return page.locator(
        f"{ROOT} .compute-ov-row", has=page.get_by_role("heading", name=name, exact=True)
    )


def _assert_fits(page):
    """Nothing in the overview reaches past the right edge of the screen (the Deck scroller would hide it
    instead of scrolling, so the document's own width proves nothing)."""
    over = page.evaluate(
        "() => [...document.querySelectorAll('.compute-ov, .compute-ov *')].filter(el => {"
        " const r = el.getBoundingClientRect(); if (!r.width) return false;"
        " let node = el.parentElement; while (node && !node.classList.contains('compute-ov')) {"
        "  if (getComputedStyle(node).overflowX !== 'visible') return false; node = node.parentElement; }"
        " return r.right > innerWidth + 1; }).map(el => el.className || el.tagName)"
    )
    assert over == [], f"reaches past the right edge: {over[:4]}"


def _assert_ov_phone(page):
    """44px targets and 16px controls inside the overview; nothing scrolls sideways."""
    controls = page.locator(".compute-ov button:visible, .compute-ov select:visible")
    heights = controls.evaluate_all("els => els.map(el => el.getBoundingClientRect().height)")
    assert heights and min(heights) >= TOUCH, (
        f"touch targets under {TOUCH}px: {sorted(heights)[:3]}"
    )
    sizes = page.locator(".compute-ov select:visible").evaluate_all(
        "els => els.map(el => parseFloat(getComputedStyle(el).fontSize))"
    )
    assert not sizes or min(sizes) >= 16, "the status filter is 16px on the phone"
    text_sizes = page.locator(
        ".compute-ov-row p:visible, .compute-ov-list li:visible"
    ).evaluate_all("els => els.map(el => parseFloat(getComputedStyle(el).fontSize))")
    assert not text_sizes or min(text_sizes) >= 14, (
        f"overview body text is 14px or more on a phone: {sorted(set(text_sizes))}"
    )
    _no_sideways_scroll(page)
    _assert_fits(page)


def _open(browser, fx, ui, size, cookie=None, viewport=None, touch=False):
    width, height = viewport or SIZES[size]
    phone = width < 700 or touch
    context = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=phone, is_mobile=phone
    )
    context.add_cookies([{"name": sessions.COOKIE, "value": cookie or fx.cookie, "url": fx.origin}])
    page = context.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "response",
        lambda r: (
            errors.append(f"{r.status} {r.url}")
            if "/api/compute/overview" in r.url and r.status >= 500
            else None
        ),
    )
    return context, page, errors


def _visit(page, fx, ui):
    route = "#/compute/management" if ui == "deck" else "#/compute"
    page.goto(f"{fx.origin}/app/?ui={ui}{route}")
    page.locator("#compute-overview").wait_for()


def _run(
    fx, ui, size, body, cookie=None, viewport=None, populate=True, expect_errors=False, touch=False
):
    if populate and not hasattr(fx, "env"):
        fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size, cookie, viewport, touch)
            _visit(page, fx, ui)
            body(page, f"{ui}-{size}", context)
            if not expect_errors:
                assert errors == []
            context.close()
        finally:
            browser.close()


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_every_state_reads_truthfully_and_nothing_says_running_on_old_data(served, ui, size):
    fx = served

    def body(page, label, context):
        _go(page, fx, ui)
        shown = _shown(page)
        assert "5 runners" not in shown  # six were enrolled; the sentence counts them all
        expect(page.locator("[data-ov-summary]")).to_contain_text(
            "7 runners, 1 target machine, 7 cloud workspaces. 3 need attention: "
        )
        # Runners: each state says what it is and how old the signal is.
        expect(_row(page, "build-runner")).to_contain_text("Online · Busy")
        expect(_row(page, "build-runner")).to_contain_text(
            "2 of 3 runs at once are in use: 1 in progress; 1 finished run still counts until its card moves out of Doing."
        )
        expect(_row(page, "build-runner")).to_contain_text(
            re.compile(r"Last seen \d+ (seconds?|minutes?) ago")
        )
        expect(_row(page, "build-runner")).to_contain_text("2 of 3 runs at once in use")
        expect(_row(page, "review-runner")).to_contain_text("Slow to report")
        expect(_row(page, "edge-runner")).to_contain_text("Offline")
        expect(_row(page, "edge-runner")).to_contain_text("Last seen 25 minutes ago")
        expect(_row(page, "edge-runner")).to_contain_text("1 thing blocks new work: offline")
        expect(_row(page, "edge-runner")).to_contain_text("Next step: Check that the machine is on")
        expect(_row(page, "edge-runner")).to_contain_text("0 runs recorded in progress")
        expect(_row(page, "spare-runner")).to_contain_text("Never connected")
        expect(_row(page, "spare-runner")).to_contain_text("No signal yet")
        expect(_row(page, "night-runner")).to_contain_text("Draining")
        expect(_row(page, "idle-runner")).to_contain_text("Drained")
        expect(_row(page, "idle-runner")).to_contain_text("None is in progress")
        expect(_row(page, "retired-runner")).to_contain_text("Revoked")
        # An offline runner is never green and never says online or running.
        offline = _row(page, "edge-runner")
        assert (
            "Online" not in offline.inner_text()
            and "running"
            not in offline.inner_text()
            .lower()
            .replace("runs it already started may still be going", "")
            .replace("the runner program is running there", "")
            .replace("more may be running while it is offline", "")
        )
        # Cloud workspaces: a stale "running" is unknown; a failure says why; a request is only a request.
        expect(_row(page, "Build box")).to_contain_text("Running")
        expect(_row(page, "Old box")).to_contain_text("Unknown")
        expect(_row(page, "Old box")).to_contain_text("nothing newer has arrived")
        expect(_row(page, "Broken box")).to_contain_text("It ran out of memory")
        expect(_row(page, "Docs box")).to_contain_text("Stopped")
        expect(_row(page, "Queued box")).to_contain_text("Start requested")
        expect(_row(page, "Queued box")).to_contain_text("nothing has started yet")
        # One vocabulary with the workspaces list: a record nothing has started is "Saved, not started", with or without a machine.
        expect(_row(page, "New box")).to_contain_text("Saved, not started")
        expect(_row(page, "Unplaced box")).to_contain_text("Saved, not started")
        expect(_row(page, "Unplaced box")).to_contain_text(
            "Choose a machine for it with Change settings."
        )
        assert "No machine chosen" not in _shown(page)
        expect(_row(page, "Build machine")).to_contain_text("Not verified")
        expect(_row(page, "Build machine")).to_contain_text("1 thing blocks new work: not verified")
        expect(_row(page, "Build machine")).to_contain_text(
            "Verification is not available in this version of the app."
        )
        assert "Next step" not in _row(page, "Build machine").inner_text()
        assert "Advanced" not in _row(page, "Build machine").inner_text()
        # What is not known says so: no zero stands in for a missing report.
        expect(_row(page, "build-runner")).to_contain_text("2 runs of unknown cost")
        expect(page.locator(f"{ROOT}")).to_contain_text(
            "Starting and stopping cloud workspaces on a machine is not available in this version"
        )
        assert re.search(r"\b0\.00\b", _shown(page)) is None
        _shot(page, f"{label}-10-fleet")
        if size == "phone":
            _assert_ov_phone(page)
        _no_sideways_scroll(page)
        _assert_fits(page)

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_filters_narrow_the_list_and_an_empty_result_says_so(served, ui, size):
    fx = served

    def body(page, label, context):
        _go(page, fx, ui)
        expect(_rows(page)).to_have_count(15)
        page.get_by_role("button", name=re.compile(r"^Runners \(7\)")).click()
        expect(_rows(page)).to_have_count(7)
        expect(page.get_by_role("button", name=re.compile(r"^Runners"))).to_have_attribute(
            "aria-pressed", "true"
        )
        expect(page.locator("[data-ov-announce]")).to_have_text("Showing 7 of 15.")
        expect(page.locator(".compute-ov-showing")).to_have_text("Showing 7 of 15")
        expect(page.get_by_role("button", name=re.compile(r"^Runners"))).to_be_focused()
        page.get_by_label("Filter by status").select_option(label="Offline (1)")
        expect(_rows(page)).to_have_count(1)
        expect(_row(page, "edge-runner")).to_be_visible()
        _shot(page, f"{label}-11-filtered")
        # A status that does not exist for the chosen kind is not offered, so no combination matches nothing.
        page.get_by_role("button", name=re.compile(r"^Cloud workspaces \(7\)")).click()
        expect(_rows(page)).to_have_count(7)
        expect(page.get_by_label("Filter by status")).to_have_value("all")
        assert (
            page.get_by_label("Filter by status").locator("option", has_text="Offline").count() == 0
        )
        _shot(page, f"{label}-12-kind-resets-status")
        assert page.get_by_role("button", name="Clear filters").count() == 1
        page.get_by_role("button", name="Clear filters").click()
        expect(_rows(page)).to_have_count(15)
        expect(page.get_by_label("Filter by status")).to_be_focused()
        # "6 need attention" is a button that sets the same filter, with a filled pressed state.
        page.get_by_role("button", name="3 need attention").click()
        expect(_rows(page)).to_have_count(3)
        expect(page.get_by_role("button", name="3 need attention")).to_have_attribute(
            "aria-pressed", "true"
        )
        expect(page.get_by_label("Filter by status")).to_have_value("attention")
        pressed = page.get_by_role("button", name="3 need attention").evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        idle = page.get_by_role("button", name="Runners (7)").evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        assert pressed != idle, "a pressed filter is filled, not only outlined"
        # The problems come first inside each group.
        first = page.locator(f"{ROOT} .compute-ov-group").first.locator(".compute-ov-row").first
        expect(first).to_have_attribute("data-status", re.compile("offline|slow|disabled|busy"))
        if size == "phone":
            _assert_ov_phone(page)

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_details_open_in_place_with_focus_and_escape(served, ui, size):
    fx = served

    def body(page, label, context):
        _go(page, fx, ui)
        page.get_by_role("button", name="Status and activity of edge-runner").click()
        heading = page.locator("#compute-ov-heading")
        expect(heading).to_be_focused()
        panel = page.locator(".compute-ov-panel")
        expect(panel.locator("[data-ov-chip]")).to_have_text("Offline")
        expect(panel).to_contain_text("Blocking new work")
        expect(panel).to_contain_text("Offline: nothing heard for over 15 minutes")
        expect(panel).to_contain_text("Next step: Check that the machine is on")
        assert panel.locator("header").get_by_role("button", name="Close").count() == 1, (
            "Close is in the header"
        )
        assert panel.get_by_role("button", name="Close").count() == 1
        expect(panel).to_contain_text("Machine resources")
        expect(panel).to_contain_text("Not reported: this machine has not sent a resource report.")
        expect(panel).to_contain_text("Logs")
        expect(panel).to_contain_text("Not collected")
        assert page.locator('.compute-ov [role="alert"]:visible').count() == 0
        assert page.locator(f"{ROOT} .compute-ov-row").count() == 0, (
            "the list gives way to the panel"
        )
        _shot(page, f"{label}-20-detail-offline")
        page.get_by_role("button", name="Close").click()
        expect(
            page.get_by_role("button", name="Status and activity of edge-runner")
        ).to_be_focused()
        page.get_by_role("button", name="Status and activity of Old box").click()
        expect(page.locator(".compute-ov-panel [data-ov-chip]")).to_have_text("Unknown")
        expect(page.locator(".compute-ov-panel")).to_contain_text("Last report")
        _shot(page, f"{label}-21-detail-unknown")
        page.keyboard.press("Escape")
        expect(page.get_by_role("button", name="Status and activity of Old box")).to_be_focused()
        page.get_by_role("button", name="Status and activity of Broken box").click()
        expect(page.locator(".compute-ov-panel")).to_contain_text("It ran out of memory")
        _shot(page, f"{label}-22-detail-failed")
        page.get_by_role("button", name="Close").click()
        # Busy, draining, requested and a target machine each explain themselves in the detail too.
        for name, texts, shot in (
            (
                "build-runner",
                [
                    "2 of 3 runs at once are in use: 1 in progress; 1 finished run still counts until its card moves out of Doing.",
                    "demo card",
                    "Move it out of Doing (to Done, Review or Todo) to free its place.",
                    "2 of 3 runs at once in use",
                    "6.0 GiB memory free",
                    "2 runs with unknown cost",
                ],
                "23-detail-busy",
            ),
            (
                "night-runner",
                [
                    "No new card runs start here. Chat and bot sessions can still start here. 1 run in progress will finish.",
                    "Draining: No new card runs start here. Chat and bot sessions can still start here.",
                ],
                "24-detail-draining",
            ),
            (
                "idle-runner",
                [
                    "No new card runs start here. Chat and bot sessions can still start here. None is in progress.",
                    "Resuming is not available in this version.",
                    "Drain started. No new card runs start here.",
                ],
                "27-detail-drained",
            ),
            (
                "Queued box",
                [
                    "Commonplace saved the request",
                    "It expires after 24 hours if no machine picks it up; there is nothing to do meanwhile.",
                ],
                "25-detail-requested",
            ),
            (
                "Build machine",
                [
                    "Not checked live",
                    "Commonplace does not probe target machines.",
                    "Not verified: cloud workspaces placed here cannot start until it is verified.",
                    "Verification is not available in this version of the app.",
                ],
                "26-detail-machine",
            ),
        ):
            page.get_by_role("button", name=f"Status and activity of {name}").click()
            for text in texts:
                expect(page.locator(".compute-ov-panel")).to_contain_text(text)
            _shot(page, f"{label}-{shot}")
            page.get_by_role("button", name="Close").click()
        page.get_by_role("button", name="Status and activity of Broken box").click()
        page.get_by_role("button", name="Change settings in Cloud workspaces").click()
        expect(page.locator("#compute-workspaces")).to_be_focused()
        page.get_by_role("button", name="Close").first.click() if page.locator(
            ".compute-ov-panel"
        ).count() else None
        if size == "phone":
            _assert_ov_phone(page)

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_member_reads_cloud_workspaces_only_and_is_offered_no_action(served, ui, size):
    fx = served

    def body(page, label, context):
        _go(page, fx, ui)
        expect(_rows(page)).to_have_count(7)
        expect(page.locator(ROOT)).to_contain_text(
            "Runner machines in Admin and target machines are visible to the workspace owner."
        )
        _shot(page, f"{label}-29-member-list")
        text = page.locator(".compute").inner_text()
        for hidden in (
            "build-runner",
            "edge-runner",
            "Build machine",
            "192.0.2",
            "Starting and stopping cloud workspaces",
        ):
            assert hidden not in text, hidden
        assert (
            page.get_by_role(
                "button", name=re.compile(r"^(Start|Stop|Restart|Drain|Resume)")
            ).count()
            == 0
        )
        page.get_by_role("button", name="Status and activity of Build box").click()
        expect(page.locator(".compute-ov-panel")).to_contain_text("A machine is assigned")
        assert page.get_by_role("button", name="Change settings in Cloud workspaces").count() == 0
        assert "Build machine" not in page.locator(".compute-ov-panel").inner_text()
        _shot(page, f"{label}-30-member")
        page.get_by_role("button", name="Close").click()
        if size == "phone":
            _assert_ov_phone(page)

    _run(fx, ui, size, body, cookie=fx.member_cookie)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_with_no_machine_the_overview_lists_the_record_and_the_form_says_why(
    served_empty, ui, size
):
    fx = served_empty
    fx.make(name="Machine-less")

    def body(page, label, context):
        _fold(page)
        row = _row(page, "Machine-less")
        expect(row).to_be_visible()
        expect(row.locator("[data-ov-chip]")).to_have_text("Saved, not started")
        # The same words as the workspaces list below, and an instruction only where it can be followed.
        expect(row).to_contain_text(
            "No machine is set up yet. Starting will need one, which is not available yet."
        )
        assert "Choose a machine for it with Change settings" not in row.inner_text()
        assert "Nothing here yet" not in page.locator(".compute-ov").inner_text()
        assert "waiting for a machine" in page.locator("[data-ov-summary]").inner_text()
        assert "No machine chosen" not in page.locator(".compute").inner_text()
        expect(page.locator("[data-ov-summary]")).to_have_text(
            "1 cloud workspace. Nothing needs attention. 1 cloud workspace is waiting for a machine."
        )
        expect(page.locator(".compute-ws-row, .compute-ws .compute-row").first).to_contain_text(
            "Saved, not started"
        )
        _shot(page, f"{label}-60-no-machine-row")
        page.get_by_role("button", name=re.compile("^New cloud workspace")).click()
        machine = page.locator("#ws-field-machine")
        expect(machine).to_be_disabled()
        expect(machine.locator("option:checked")).to_have_text("No machines set up yet")
        _shot(page, f"{label}-61-no-machines-field")
        if size == "phone":
            _assert_ov_phone(page)

    _run(fx, ui, size, body, populate=False)


@pytest.mark.parametrize("ui", UIS)
def test_a_workspace_created_below_shows_up_in_the_overview_without_a_reload(served_empty, ui):
    fx = served_empty

    def body(page, label, context):
        _fold(page)
        expect(page.locator("[data-ov-summary]")).to_have_text("Nothing here yet.")
        fx.make(
            name="Late arrival"
        )  # saved behind the page's back, as another tab or a person would
        page.get_by_role("button", **REFRESH).first.click()
        expect(_row(page, "Late arrival")).to_be_visible()
        expect(page.locator("[data-ov-summary]")).not_to_contain_text("Nothing here yet.")

    _run(fx, ui, "desktop", body, populate=False)


@pytest.mark.parametrize("ui", UIS)
def test_on_a_phone_the_overview_is_folded_and_the_create_button_is_within_reach(served, ui):
    fx = served

    def body(page, label, context):
        fold = page.locator("details[data-ov-fold]")
        assert fold.get_attribute("open") is None, (
            "the overview starts as one summary line on a phone"
        )
        expect(page.locator("[data-ov-summary]")).to_contain_text(
            "7 runners, 1 target machine, 7 cloud workspaces"
        )
        create = page.get_by_role("button", name=re.compile("^New cloud workspace"))
        box = create.bounding_box()
        assert box and box["y"] + box["height"] < 844 * 1.6, (
            f"the create button is {box['y']}px down"
        )
        pause = page.get_by_role("button", name="Pause automatic updates")
        assert pause.bounding_box()["width"] < 300, "no full-width box that looks like a text field"
        assert page.get_by_role("button", name="Update overview").count() == 0
        _shot(page, f"{label}-62-folded")
        _fold(page)
        _shot(page, f"{label}-63-unfolded")
        _assert_ov_phone(page)
        # A person's choice survives the screen's own re-render.
        page.get_by_role("button", **REFRESH).first.click()
        expect(fold).to_have_attribute("open", "")

    _run(fx, ui, "phone", body)


def test_the_deck_map_tab_stops_saying_checking_when_there_is_nothing_to_check(served_empty):
    fx = served_empty

    def body(page, label, context):
        page.goto(f"{fx.origin}/app/?ui=deck#/compute")
        status = page.locator('[data-compute-region="status"]')
        expect(status).to_be_visible()
        expect(status).not_to_contain_text("Checking")
        expect(status).to_contain_text(
            "No machines on the map yet. Machines added here are checked and shown on the map; bots run their code on a runner machine set up in Admin."
        )
        empty = page.locator('[data-compute-region="empty"]')
        expect(empty).to_contain_text("A cloud workspace can still be saved without a machine")
        _shot(page, f"{label}-64-map-no-machines")

    _run(fx, "deck", "desktop", body, populate=False)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_expired_slow_and_unverified_states_explain_themselves(served, ui, size):
    fx = served
    fx.names = _populate(fx)
    # Created and asked 25 hours ago, audit trail included (it is append-only): the timeline reads in order and nothing
    # says "just now" beside "expired".
    fx.store._now = lambda: time.time() - (lifecycle.REQUEST_TTL_S + 7200)
    stale = fx.make(name="Lapsed box", node_id=fx.node["id"])["id"]
    fx.store._now = lambda: time.time() - (lifecycle.REQUEST_TTL_S + 3600)
    with fx.store._journal() as conn:
        life = lifecycle.life_of(conn, stale, time.time())
        row = conn.execute("SELECT version FROM cloud_workspaces WHERE id=?", (stale,)).fetchone()
    lifecycle.request(
        fx.store,
        fx.principal,
        stale,
        "start",
        session_hash=fx.auth["session_hash"],
        expected_seq=life["seq"],
        expected_version=row["version"],
    )
    del fx.store._now

    def body(page, label, context):
        _go(page, fx, ui)
        page.get_by_role("button", name="Status and activity of Lapsed box").click()
        panel = page.locator(".compute-ov-panel")
        expect(panel).to_contain_text(
            "expired after 24 hours without a machine picking it up, so it was not carried out"
        )
        assert "Ask again" not in panel.inner_text()
        expect(panel).to_contain_text("Not carried out")
        expect(panel).to_contain_text(
            "Starting is not available in this version, so there is nothing to do yet."
        )
        assert "Needs a person" not in panel.inner_text()
        _shot(page, f"{label}-70-detail-expired-request")
        page.get_by_role("button", name="Close").click()
        page.get_by_role("button", name="Status and activity of review-runner").click()
        expect(page.locator(".compute-ov-panel [data-ov-chip]")).to_have_text("Slow to report")
        expect(page.locator(".compute-ov-panel")).to_contain_text("Next step: Wait a little")
        _shot(page, f"{label}-71-detail-slow")
        page.get_by_role("button", name="Close").click()
        page.get_by_role("button", name="Status and activity of build-runner").click()
        page.locator(".compute-ov-panel [data-ov-detail-body]").evaluate(
            "el => el.lastElementChild.scrollIntoView({block: 'end'})"
        )
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        _shot(page, f"{label}-72-detail-bottom")
        page.get_by_role("button", name="Close").click()
        page.get_by_label("Filter by status").focus()
        _shot(page, f"{label}-73-status-list-focused")
        if size == "phone":
            _assert_ov_phone(page)

    _run(fx, ui, size, body, populate=False)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_the_page_shows_a_loading_state_with_the_real_words_while_the_list_loads(served, ui, size):
    fx = served
    fx.names = _populate(fx)
    held: dict[str, Route] = {}

    def body(page, label, context):
        pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size)
            page.route("**/api/compute/workspaces", lambda route: held.setdefault("route", route))
            route = "#/compute/management" if ui == "deck" else "#/compute"
            page.goto(f"{fx.origin}/app/?ui={ui}{route}")
            page.locator("#compute-overview").wait_for()
            expect(page.locator("#compute-workspaces ~ * , .compute-ws").first).to_contain_text(
                "Loading cloud workspaces"
            )
            _shot(page, f"{ui}-{size}-65-loading-cloud-workspaces")
            held["route"].continue_()
            context.close()
        finally:
            browser.close()
    assert errors == []


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize(
    "viewport,touch,name", [((320, 568), True, "320"), ((768, 1024), True, "768-touch")]
)
def test_the_overview_fits_320px_and_a_touch_tablet(served, ui, viewport, touch, name):
    fx = served

    def body(page, label, context):
        _go(page, fx, ui)
        _shot(page, f"{ui}-{name}-80-list")
        _assert_ov_phone(page)
        page.get_by_role("button", name="Status and activity of edge-runner").click()
        expect(page.locator(".compute-ov-panel")).not_to_have_attribute("aria-busy", "true")
        expect(page.locator(".compute-ov-panel")).to_contain_text("Recent activity")
        _shot(page, f"{ui}-{name}-81-detail")
        _assert_ov_phone(page)
        page.get_by_role("button", name="Close").click()

    _run(fx, ui, "phone", body, viewport=viewport, touch=touch)


@pytest.mark.parametrize("ui", UIS)
def test_a_folded_phone_overview_still_shows_a_failed_refresh_and_opens_for_a_failed_first_read(
    served, ui
):
    fx = served
    fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, "phone")
            _visit(page, fx, ui)
            fold = page.locator("details[data-ov-fold]")
            expect(page.locator("[data-ov-summary]")).to_contain_text("7 runners")
            assert fold.get_attribute("open") is None, "the fold is closed, as a phone starts"
            _refuse_overview(page)
            page.get_by_role("button", **REFRESH).first.click()
            # With the fold closed the person still reads that the data is old, three ways, none inside the fold.
            banner = page.locator("[data-ov-stale]")
            expect(banner).to_be_visible()
            expect(banner).to_contain_text("Could not refresh. Showing the data from")
            expect(page.locator("[data-ov-meta]")).not_to_contain_text(
                "Updated just now"
            )  # said once, by the alert
            expect(page.locator("[data-ov-summary]")).to_contain_text(
                "Could not refresh: data from"
            )
            assert fold.get_attribute("open") is None
            _shot(page, f"{ui}-phone-66-stale-fold-closed")
            context.close()
            context, page, errors = _open(browser, fx, ui, "phone")
            page.route(
                f"**{OVERVIEW}",
                lambda route: route.fulfill(
                    status=500, content_type="application/json", body='{"error":"boom"}'
                ),
            )
            _visit(page, fx, ui)
            # A failed first read is plain text (no disclosure, nothing in a fold) and "Try again" is outside it.
            assert page.locator("details[data-ov-fold], summary[data-ov-fold-summary]").count() == 0
            expect(page.locator(".compute-ov-headline")).to_be_visible()
            expect(page.get_by_role("button", name="Try again")).to_be_visible()
            expect(page.locator("[data-ov-summary]")).to_have_text("Overview not loaded.")
            _shot(page, f"{ui}-phone-67-failed-first-read")
            context.close()
        finally:
            browser.close()


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_member_sees_a_machine_less_workspace_the_way_the_list_does(served_empty, ui, size):
    fx = served_empty
    fx.make(name="Machine-less")

    def body(page, label, context):
        _fold(page)
        row = _row(page, "Machine-less")
        expect(row.locator("[data-ov-chip]")).to_have_text("Saved, not started")
        expect(row).to_contain_text("The workspace owner can choose a machine.")
        expect(page.locator("[data-ov-summary]")).to_have_text(
            "1 cloud workspace. Nothing needs attention. 1 cloud workspace is waiting for a machine."
        )
        assert "Change settings" not in page.locator(".compute-ov").inner_text()
        _shot(page, f"{label}-68-member-no-machine")

    _run(fx, ui, size, body, cookie=fx.member_cookie, populate=False)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("skew", [1000, -1000])
def test_a_skewed_browser_clock_does_not_change_what_the_overview_says(served, ui, skew):
    fx = served
    fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, "desktop")
            # The browser believes it is 1000 s later (or earlier) than the server: every timestamp on screen is the
            # server's, so the rows must read as the server said, not as the wrong clock would make them.
            page.clock.install(time=int((time.time() + skew) * 1000))
            _visit(page, fx, ui)
            _go(page, fx, ui)
            build = _row(page, "build-runner")
            expect(build.locator("[data-ov-chip]")).to_have_text("Online · Busy")
            expect(build).to_contain_text(re.compile(r"Last seen \d+ (seconds?|minutes?) ago"))
            expect(_row(page, "Build box").locator("[data-ov-chip]")).to_have_text("Running")
            expect(page.locator(".compute-ov-meta")).to_contain_text("Updated just now")
            assert "Slow to report" not in build.inner_text()
            page.get_by_role("button", name="Status and activity of build-runner").click()
            expect(page.locator(".compute-ov-panel")).not_to_have_attribute("aria-busy", "true")
            expect(page.locator(".compute-ov-panel")).to_contain_text("Recent activity")
            expect(page.locator(".compute-ov-panel")).not_to_contain_text("Offline")
            _shot(page, f"{ui}-desktop-46-skew-{skew}")
            context.close()
        finally:
            browser.close()
    assert errors == []


@pytest.mark.parametrize("ui", UIS)
def test_a_stepped_wall_clock_moves_no_age_and_a_monotonic_clock_still_does(served, ui):
    fx = served
    fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, "desktop")
            page.clock.install(time=int(time.time() * 1000))
            _visit(page, fx, ui)
            _go(page, fx, ui)
            build = _row(page, "build-runner")
            expect(build.locator("[data-ov-chip]")).to_have_text("Online · Busy")
            _refuse_overview(page)
            # The wall clock jumps forward 1000 s (and nothing has really passed): no row ages.
            page.clock.set_system_time(int((time.time() + 1000) * 1000))
            page.clock.run_for(11000)
            expect(build.locator("[data-ov-chip]")).to_contain_text("Online · Busy")
            expect(page.locator(".compute-ov-meta")).not_to_contain_text(
                "minutes ago"
            )  # nothing aged by 1000 s
            # And back by hours.
            page.clock.set_system_time(int((time.time() - 7200) * 1000))
            page.clock.run_for(11000)
            expect(build.locator("[data-ov-chip]")).to_contain_text("Online · Busy")
            # Real elapsed time still passes: the read is old and the row says so, but it keeps the server's status.
            page.clock.run_for(250000)
            expect(build.locator("[data-ov-chip]")).to_have_text("Last known: Online · Busy")
            _shot(page, f"{ui}-desktop-47-clock-step")
            context.close()
        finally:
            browser.close()
    assert errors == [] or all("503" in e for e in errors)


@pytest.mark.parametrize("ui", UIS)
def test_try_again_says_it_is_trying_and_focus_lands_on_the_heading_when_it_works(served_empty, ui):
    fx = served_empty
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, "phone")
            held: dict[str, Route] = {}
            page.route(
                f"**{OVERVIEW}",
                lambda route: route.fulfill(
                    status=500, content_type="application/json", body='{"error":"boom"}'
                ),
            )
            _visit(page, fx, ui)
            try_again = page.get_by_role("button", name="Try again")
            expect(try_again).to_be_visible()
            try_again.click()
            expect(page.locator("[data-ov-slot=state]")).to_contain_text(
                "Tried 2 times."
            )  # visible feedback
            expect(page.get_by_role("button", name="Try again")).to_be_focused()
            page.unroute(f"**{OVERVIEW}")
            page.get_by_role("button", name="Try again").click()
            expect(page.locator("[data-ov-summary]")).to_have_text("Nothing here yet.")
            # The control that had focus is gone: focus lands on the section heading, not on the page.
            expect(page.locator("#compute-overview")).to_be_focused()
            held.clear()
            context.close()
        finally:
            browser.close()


@pytest.mark.parametrize("ui", UIS)
def test_an_open_item_removed_by_a_refresh_hands_focus_to_the_heading(served, ui):
    fx = served
    fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, "desktop")
            page.clock.install(time=int(time.time() * 1000))
            _visit(page, fx, ui)
            _go(page, fx, ui)
            page.get_by_role("button", name="Status and activity of spare-runner").click()
            expect(page.locator("#compute-ov-heading")).to_be_focused()
            # The runner leaves the registry; the next automatic read no longer lists it.
            with fx.store.gateway.db._lock:
                fx.store.gateway.db._conn.execute(
                    "DELETE FROM runner_machines WHERE label='spare-runner'"
                )
            page.clock.run_for(31000)
            expect(page.locator("[data-ov-announce]")).to_contain_text("no longer")
            assert page.locator(".compute-ov-panel").count() == 0
            expect(page.locator("#compute-overview")).to_be_focused()
            context.close()
        finally:
            browser.close()


def test_one_outage_has_one_alert_on_the_page(served):
    fx = served
    fx.names = _populate(fx)

    def body(page, label, context):
        _go(page, fx, "classic")
        page.route(
            "**/api/compute",
            lambda route: route.fulfill(
                status=500, content_type="application/json", body='{"error":"x"}'
            ),
        )
        _refuse_overview(page)
        page.get_by_role("button", **REFRESH).first.click()
        expect(page.locator("[data-ov-stale]")).to_be_visible()
        assert page.locator(".compute [role=alert]:visible").count() == 1, (
            "one alert: the overview's own"
        )

    _run(fx, "classic", "desktop", body, expect_errors=True)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_fleet_rows_paused_no_match_and_a_whole_runner_detail_are_captured(served, ui, size):
    fx = served

    def body(page, label, context):
        _go(page, fx, ui)
        # The rows a reviewer has to read: busy, draining (and busy) and drained, one capture each.
        for name, number in (("build-runner", 90), ("night-runner", 91), ("idle-runner", 92)):
            row = _row(page, name)
            row.scroll_into_view_if_needed()
            _shot_of(row, f"{label}-{number}-row-{name}")
        expect(_row(page, "night-runner").locator("[data-ov-chip]")).to_have_text("Draining")
        expect(_row(page, "night-runner")).not_to_contain_text("Full")
        expect(_row(page, "idle-runner").locator("[data-ov-chip]")).to_have_text("Drained")
        # The finished run (reported ended, card moved on) is not Busy: the fixture's build-runner shows only the live one.
        expect(_row(page, "build-runner")).to_contain_text(
            "2 of 3 runs at once are in use: 1 in progress; 1 finished run still counts until its card moves out of Doing."
        )
        # Paused: the button and the line say what it stops and what keeps counting.
        page.get_by_role("button", name="Pause automatic updates").click()
        expect(page.locator(".compute-ov-meta")).to_contain_text(
            "statuses and ages stay as of that read"
        )
        page.locator("[data-ov-top]").scroll_into_view_if_needed()
        _shot(page, f"{label}-93-paused")
        page.get_by_role("button", name="Resume automatic updates").click()
        # No match: the status filter holds a status that no longer exists after the next read.
        page.get_by_role("button", name=re.compile(r"need attention$")).click()
        page.route(
            f"**{OVERVIEW}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_without_attention(route.fetch().json()),
            ),
        )
        page.get_by_role("button", **REFRESH).first.click()
        # Nothing needs attention any more: the filter goes back to all statuses, so the person is not left with an empty list.
        expect(page.get_by_label("Filter by status")).to_have_value("all")
        expect(_rows(page)).to_have_count(15)
        _shot(page, f"{label}-94-attention-filter-reset")
        page.unroute(f"**{OVERVIEW}")
        # The whole of a runner's detail, end to end, in one capture.
        page.get_by_role("button", name="Status and activity of build-runner").click()
        panel = page.locator(".compute-ov-panel")
        expect(panel).not_to_have_attribute("aria-busy", "true")
        expect(panel).to_contain_text("Recent activity")
        expect(panel).to_contain_text("Finished: demo card")  # the run that ended is not "Started"
        _shot_of(panel, f"{label}-95-detail-whole")

    _run(fx, ui, size, body)


def _without_attention(payload):
    import json

    for item in payload["items"]:
        item["health"]["state"] = "ok"
    payload["counts"]["attention"] = 0
    return json.dumps(payload)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_runner_at_its_hourly_limit_says_so_everywhere_and_never_says_yes(served, ui, size):
    fx = served
    fleet = _fleet(fx)
    wings = {"wings": ["fixture"]}
    idle = fleet.enroll("limit-idle-runner", max_starts_per_hour=2, **wings)
    fleet.seen(idle, 5)
    for start in range(2):  # two starts in the last hour, both finished, their cards long gone
        job = fleet.job(idle, "started", minutes_ago=30, task=7000 + start)
        fleet.end(job, minutes_ago=25)
    full = fleet.enroll("limit-full-runner", max_in_flight=1, max_starts_per_hour=2, **wings)
    fleet.seen(full, 5)
    fleet.end(fleet.job(full, "started", minutes_ago=30, task=7100), minutes_ago=25)
    fleet.job(full, "started", minutes_ago=1, task=fleet.work(full))

    def body(page, label, context):
        _go(page, fx, ui)
        limit = "It has started 2 runs in the last hour, its limit. New runs wait until an earlier start is more than an hour old."
        for name, number in (("limit-idle-runner", 110), ("limit-full-runner", 111)):
            row = _row(page, name)
            expect(row.locator("[data-ov-chip]")).to_have_text("Online · Limit reached")
            expect(row).to_contain_text(limit)
            assert "Waiting for work" not in row.inner_text()
            row.scroll_into_view_if_needed()
            _shot_of(row, f"{label}-{number}-row-{name}")
            page.get_by_role("button", name=f"Status and activity of {name}").click()
            panel = page.locator(".compute-ov-panel")
            expect(panel).not_to_have_attribute("aria-busy", "true")
            expect(panel.locator("[data-ov-chip]")).to_have_text("Online · Limit reached")
            expect(panel).to_contain_text("Allowed to start card runs")
            expect(panel).to_contain_text("Not right now: hourly limit reached")
            expect(panel).to_contain_text("Hourly limit: " + limit)
            assert "Yes, once a run finishes" not in panel.inner_text()
            _shot_of(panel, f"{label}-{number}-detail-{name}")
            page.get_by_role("button", name="Close").click()

    _run(fx, ui, size, body, populate=False)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_turned_off_removed_offline_and_never_connected_runner_says_what_it_may_do(
    served, ui, size
):
    fx = served
    fleet = _fleet(fx)
    wings = {"wings": ["fixture"]}
    off = fleet.enroll("off-runner", **wings)
    fleet.seen(off, 5)
    fleet.disable(off)
    gone = fleet.enroll("gone-runner", **wings)
    fleet.seen(gone, 5)
    revoke_machine(fx.env, gone)
    quiet = fleet.enroll("quiet-runner", **wings)
    fleet.seen(quiet, 4000)
    fleet.enroll("new-runner", **wings)

    def body(page, label, context):
        _go(page, fx, ui)
        for name, chip, allowed, number in (
            ("off-runner", "Disabled", "No: it is turned off", 100),
            ("gone-runner", "Revoked", "No: it was removed", 101),
            ("quiet-runner", "Offline", "Yes, once it reports", 102),
            ("new-runner", "Never connected", "Yes, once it reports", 103),
        ):
            page.get_by_role("button", name=f"Status and activity of {name}").click()
            panel = page.locator(".compute-ov-panel")
            expect(panel).not_to_have_attribute("aria-busy", "true")
            expect(panel.locator("[data-ov-chip]")).to_have_text(chip)
            expect(panel).to_contain_text("Allowed to start card runs")
            expect(panel).to_contain_text(allowed)
            _shot_of(panel, f"{label}-{number}-detail-{name}")
            page.get_by_role("button", name="Close").click()

    _run(fx, ui, size, body, populate=False)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_quarter_of_an_hour_old_read_changes_no_status_and_every_age_is_as_of_the_read(
    served, ui, size
):
    fx = served
    fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size)
            page.clock.install(time=int(time.time() * 1000))
            _visit(page, fx, ui)
            _go(page, fx, ui)
            row = _row(page, "build-runner")
            status_before = row.locator("[data-ov-chip]").inner_text()
            chips_before = page.locator("[data-ov-chip]").all_inner_texts()
            _refuse_overview(page)
            page.clock.run_for(
                1000000
            )  # the read failed and a quarter of an hour passed with no signal
            # The server's status, unchanged and marked: the browser never decides it is Slow or Offline.
            expect(row.locator("[data-ov-chip]")).to_have_text(f"Last known: {status_before}")
            # Every chip on the page is the one it had, marked: none is a status the clock made up.
            expect(page.locator("[data-ov-chip]")).to_have_text(
                [c if c.startswith("Unknown") else f"Last known: {c}" for c in chips_before]
            )
            expect(row).to_contain_text(re.compile(r"As of \d{1,2}:\d\d:\d\d"))
            expect(row).to_contain_text(
                re.compile(r"Last seen \d+ (seconds?|minutes?) before \d{1,2}:\d\d:\d\d")
            )
            assert "Next step: Check that the machine is on" not in row.inner_text()
            # The banner keeps its own button.
            expect(page.locator("[data-ov-stale]")).to_be_visible()
            expect(page.locator("[data-ov-stale-retry]")).to_have_text("Refresh")
            expect(page.locator("[data-ov-stale-retry]")).to_be_visible()
            row.scroll_into_view_if_needed()
            _shot_of(row, f"{ui}-{size}-104-row-quarter-hour-old-read")
            page.get_by_role("button", name="Status and activity of build-runner").click()
            panel = page.locator(".compute-ov-panel")
            expect(panel).not_to_have_attribute("aria-busy", "true")
            expect(panel.locator("[data-ov-chip]")).to_have_text(f"Last known: {status_before}")
            expect(panel).to_contain_text("Everything here is as of")
            expect(panel).to_contain_text(
                re.compile(r"\d+ (seconds?|minutes?) before \d{1,2}:\d\d:\d\d")
            )
            assert "Offline: nothing heard for" not in panel.inner_text()
            assert " ago" not in panel.locator("[data-ov-seen]").inner_text()
            _shot_of(panel, f"{ui}-{size}-105-detail-quarter-hour-old-read")
            context.close()
        finally:
            browser.close()
    assert errors == [] or all("503" in e for e in errors)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_paused_tab_marks_every_row_as_of_its_last_read_and_offers_refresh(served, ui, size):
    fx = served
    fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size)
            page.clock.install()
            reads = _count_reads(page)
            workspace_reads = []
            page.route("**/api/compute/workspaces", lambda route: workspace_reads.append(route))
            _visit(page, fx, ui)
            _go(page, fx, ui)
            row = _row(page, "build-runner")
            status_before = row.locator("[data-ov-chip]").inner_text()
            page.get_by_role("button", name="Pause automatic updates").click()
            reads_at_pause = len(reads)
            # The other section finishes loading after Pause: its new signature must not trigger an overview read.
            assert workspace_reads
            with page.expect_response(lambda r: r.url.endswith("/api/compute/workspaces")):
                for route in workspace_reads:
                    route.continue_()
            page.unroute("**/api/compute/workspaces")
            expect(page.locator(".compute-ws-row")).to_have_count(7)
            page.clock.run_for(10000)
            assert len(reads) == reads_at_pause, (
                "a changed workspace signature must preserve the paused snapshot"
            )
            expect(page.locator("[data-ov-stale]")).to_be_hidden()
            page.clock.run_for(900000)  # fifteen minutes with no automatic read
            assert len(reads) == reads_at_pause, ("paused: nothing was read", reads)
            expect(row.locator("[data-ov-chip]")).to_have_text(f"Last known: {status_before}")
            expect(_row(page, "Build box").locator("[data-ov-chip]")).to_have_text(
                "Last known: Running"
            )
            expect(page.locator("[data-ov-stale]")).to_contain_text(
                re.compile(
                    r"Automatic updates are paused\. Showing the data from \d{1,2}:\d\d:\d\d"
                )
            )
            expect(row).to_contain_text(
                re.compile(r"Last seen \d+ (seconds?|minutes?) before \d{1,2}:\d\d:\d\d")
            )
            assert " Offline" not in row.inner_text()
            page.locator("[data-ov-top]").scroll_into_view_if_needed()
            _shot(page, f"{ui}-{size}-106-paused-quarter-hour")
            # Not an error: a paused tab is a plain note (red is for a failed read), and its Refresh reads again, drops
            # every mark and takes the button away.
            expect(page.locator("[data-ov-stale]")).not_to_have_class(re.compile("compute-error"))
            with page.expect_request(lambda r: r.url.endswith(OVERVIEW)):
                page.locator("[data-ov-stale-retry]").click()
            expect(row.locator("[data-ov-chip]")).to_have_text(status_before)
            expect(page.locator("[data-ov-stale]")).to_be_hidden()
            expect(page.locator("[data-ov-stale-retry]")).to_be_hidden()
            manual_reads = len(reads)
            page.clock.run_for(10000)
            assert len(reads) == manual_reads, "manual Refresh keeps automatic updates paused"
            with page.expect_request(lambda r: r.url.endswith(OVERVIEW)):
                page.get_by_role("button", name="Resume automatic updates").click()
            expect(page.get_by_role("button", name="Pause automatic updates")).to_be_visible()
            assert len(reads) == manual_reads + 1
            context.close()
        finally:
            browser.close()
    assert errors == []


@pytest.mark.parametrize("ui", UIS)
def test_an_owner_workspace_detail_fits_320px_and_its_change_settings_button_wraps(served, ui):
    fx = served
    fx.names = _populate(fx)

    def body(page, label, context):
        _go(page, fx, ui)
        page.get_by_role("button", name="Status and activity of Build box").click()
        panel = page.locator(".compute-ov-panel")
        expect(panel).not_to_have_attribute("aria-busy", "true")
        button = panel.get_by_role("button", name="Change settings in Cloud workspaces")
        expect(button).to_be_visible()
        box = button.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 320 + 1, (
            box
        )  # inside the screen, not run past it
        assert box["height"] >= 44
        _no_sideways_scroll(page)
        _shot(page, f"{ui}-320-106-owner-workspace-detail")
        _shot_of(panel, f"{ui}-320-107-owner-workspace-detail-whole")

    _run(fx, ui, "phone", body, viewport=(320, 568), touch=True)


def _count_reads(page):
    seen = []
    page.on("request", lambda r: seen.append(r.url) if r.url.endswith(OVERVIEW) else None)
    return seen


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_loading_empty_and_failed_states_say_what_is_happening(served_empty, ui, size):
    fx = served_empty

    def run_with(handler, label_part, check, populate=False):
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context, page, errors = _open(browser, fx, ui, size)
                page.route(f"**{OVERVIEW}", handler)
                route = "#/compute/management" if ui == "deck" else "#/compute"
                page.goto(f"{fx.origin}/app/?ui={ui}{route}")
                page.locator("#compute-overview").wait_for()
                check(page, f"{ui}-{size}-{label_part}")
                context.close()
            finally:
                browser.close()

    gate = {}

    def slow(route):
        gate["route"] = route  # held until the check has looked at the loading state

    def loading(page, label):
        expect(page.locator("[data-ov-summary]")).to_have_text("Loading the overview…")
        assert page.locator(f"{ROOT} .compute-ov-row").count() == 0
        assert page.locator(ROOT).count() == 0, (
            "nothing to fold while it loads: plain headline only"
        )
        _shot(page, label)
        gate["route"].continue_()
        # A fresh workspace: nothing is listed, and the empty state says what to do next.
        expect(page.locator("[data-ov-summary]")).to_have_text("Nothing here yet.")
        # The next step is outside the fold (a folded phone must not hide the one thing to do) and the summary has no
        # disclosure marker, since there is nothing to fold.
        expect(page.locator("[data-ov-slot=state]")).to_contain_text(
            "No machine is set up yet. You can still create a cloud workspace below; it waits for a machine."
        )
        assert page.locator("details[data-ov-fold], summary[data-ov-fold-summary]").count() == 0
        expect(page.locator(".compute-ov-headline")).to_be_visible()
        assert "Nothing here yet" not in page.locator("[data-ov-slot=state]").inner_text(), (
            "the headline says it once"
        )
        assert "runner machine" not in page.locator("[data-ov-slot=state]").inner_text()
        assert "A runner machine is a computer for bots" in (
            page.locator(".compute-ov").text_content() or ""
        )
        assert page.locator(f"{ROOT} .compute-ov-row").count() == 0
        assert page.locator("[data-ov-kind-filter]").count() == 0
        _shot(page, f"{ui}-{size}-43-empty")
        if size == "phone":
            _assert_ov_phone(page)

    run_with(slow, "40-loading", loading)

    def broken(route):
        route.fulfill(status=500, content_type="application/json", body='{"error":"boom"}')

    def failed(page, label):
        _fold(page)
        alert = page.locator("[data-ov-slot=state] [role=alert]:visible")
        expect(alert).to_have_text(
            "The overview could not be loaded. Nothing is shown rather than a guess."
        )
        _shot(page, label)
        page.unroute(f"**{OVERVIEW}")
        page.get_by_role("button", name="Try again").click()
        expect(page.locator("[data-ov-summary]")).to_have_text("Nothing here yet.")
        assert page.locator("[data-ov-slot=state] [role=alert]:visible").count() == 0

    run_with(broken, "41-failed", failed)


REFRESH = {"name": "Refresh", "exact": True}


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_pairing_checks_keep_the_paused_overview_snapshot(served, ui, size):
    fx = served

    def body(page, label, context):
        page.clock.install()
        reads = _count_reads(page)
        state = {"value": "waiting"}

        def pairing():
            return {
                "id": "pair_pause_fixture",
                "label": "Pause pairing fixture",
                "state": state["value"],
                "state_label": f"Fixture {state['value']}",
                "sentence": "Synthetic setup for the paused snapshot check.",
                "next": "No real machine is contacted.",
                "steps": [],
                "can_close": True,
            }

        page.route(
            "**/api/compute/pairings",
            lambda route: route.fulfill(
                json={"pairings": [pairing()], "total": 1, "setup_enabled": True}
            ),
        )
        page.route(
            "**/api/compute/pairings/options",
            lambda route: route.fulfill(json={"kinds": [], "wings": []}),
        )
        page.route(
            "**/api/compute/pairings/pair_pause_fixture",
            lambda route: route.fulfill(json={"pairing": pairing()}),
        )
        # Install routes before the screen's real controller starts reading Add a machine.
        page.reload()
        _go(page, fx, ui)
        page.locator('[data-pr-action="open"][data-id="pair_pause_fixture"]').click()
        page.get_by_role("button", name="Pause automatic updates").click()
        count = len(reads)
        meta = page.locator(".compute-ov-meta").inner_text()
        state["value"] = "connected"
        with page.expect_response(
            lambda response: response.url.endswith("/pairings/pair_pause_fixture")
        ):
            page.clock.run_for(3000)
        expect(page.locator(".compute-pr")).to_contain_text("Fixture connected")
        assert len(reads) == count, "the pairing check must not read a paused Overview"
        expect(page.locator(".compute-ov-meta")).to_have_text(meta)
        _shot(page, f"{label}-pause-pairing-check")
        with page.expect_response(lambda response: response.url.endswith(OVERVIEW)):
            page.get_by_role("button", name="Refresh", exact=True).click()
        page.evaluate("async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); }")
        expect(page.get_by_role("button", name="Resume automatic updates")).to_be_visible()
        with page.expect_request(lambda request: request.url.endswith(OVERVIEW)):
            page.get_by_role("button", name="Resume automatic updates").click()

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_late_automatic_reply_keeps_the_paused_overview_snapshot(served, ui, size):
    fx = served

    def body(page, label, context):
        page.clock.install()
        page.reload()
        _go(page, fx, ui)
        held = []

        def hold(route):
            held.append(route)
            page.evaluate("window.__pausedOverviewHeld = true")

        page.route(f"**{OVERVIEW}", hold)
        with page.expect_request(lambda request: request.url.endswith(OVERVIEW)):
            page.clock.run_for(31000)
        page.wait_for_function("window.__pausedOverviewHeld === true")
        assert len(held) == 1
        page.get_by_role("button", name="Pause automatic updates").click()
        before = page.locator("[data-ov-slot='list']").inner_html()
        meta = page.locator(".compute-ov-meta").inner_text()
        route = held.pop()
        payload = route.fetch().json()
        payload["as_of"] = "2026-11-20T18:00:00Z"
        payload["items"][0]["name"] = "Late automatic fixture"
        with page.expect_event(
            "requestfinished", predicate=lambda request: request.url.endswith(OVERVIEW)
        ):
            route.fulfill(json=payload)
        page.evaluate("async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); }")
        expect(page.locator("[data-ov-slot='list']")).to_have_js_property("innerHTML", before)
        expect(page.locator(".compute-ov-meta")).to_have_text(meta)
        _shot(page, f"{label}-pause-late-overview-reply")
        page.unroute(f"**{OVERVIEW}")
        with page.expect_response(lambda response: response.url.endswith(OVERVIEW)):
            page.get_by_role("button", name="Refresh", exact=True).click()
        page.evaluate("async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); }")
        expect(page.get_by_role("button", name="Resume automatic updates")).to_be_visible()
        with page.expect_request(lambda request: request.url.endswith(OVERVIEW)):
            page.get_by_role("button", name="Resume automatic updates").click()

    _run(fx, ui, size, body)


def _refuse_overview(page):
    page.route(
        f"**{OVERVIEW}",
        lambda route: route.fulfill(
            status=503, content_type="application/json", body='{"error":"authority_unavailable"}'
        ),
    )


@pytest.mark.parametrize("ui", UIS)
def test_a_refresh_that_fails_keeps_the_old_data_and_marks_every_row_as_old(served, ui):
    fx = served

    def body(page, label, context):
        _go(page, fx, ui)
        before = _shown(page)
        _refuse_overview(page)
        # The page's one Refresh button reads the overview too.
        page.get_by_role("button", **REFRESH).first.click()
        banner = page.locator("[data-ov-stale]")
        expect(banner).to_contain_text("Could not refresh. Showing the data from")
        expect(banner).to_contain_text("it may be out of date")
        # Said once, by the alert (not twice: the status line stays as it was).
        expect(page.locator("[data-ov-announce]")).not_to_contain_text("could not be refreshed")
        assert "edge-runner" in _shown(page) and "edge-runner" in before
        # No row keeps saying Online, Busy or Running as if it were current: each says "Last known" and "As of".
        expect(_row(page, "build-runner").locator("[data-ov-chip]")).to_have_text(
            "Last known: Online · Busy"
        )
        expect(_row(page, "build-runner")).to_contain_text(re.compile(r"As of \d{1,2}:\d\d:\d\d"))
        expect(_row(page, "Build box").locator("[data-ov-chip]")).to_have_text(
            "Last known: Running"
        )
        expect(
            page.locator("[data-ov-chip]", has_text=re.compile(r"^(Online|Running|Busy)"))
        ).to_have_count(0)
        _shot(page, f"{label}-44-stale")
        page.unroute(f"**{OVERVIEW}")
        page.get_by_role("button", **REFRESH).first.click()
        expect(banner).to_be_hidden()
        expect(_row(page, "build-runner").locator("[data-ov-chip]")).to_have_text("Online · Busy")

    _run(fx, ui, "desktop", body, expect_errors=True)  # the 503 is the point of this case


@pytest.mark.parametrize("ui", UIS)
def test_an_old_read_keeps_every_status_and_says_every_age_against_the_read(served, ui):
    fx = served
    fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, "desktop")
            page.clock.install()
            _visit(page, fx, ui)
            _go(page, fx, ui)
            _refuse_overview(page)  # nothing newer can arrive: only the clock moves
            expect(_row(page, "build-runner")).to_contain_text(
                re.compile(r"Last seen \d+ (seconds?|minutes?) ago")
            )
            expect(_row(page, "Build box")).to_contain_text("Running")
            counts = page.locator("[data-ov-summary]").inner_text()
            page.clock.run_for(70000)  # the read failed: from here on every row is marked
            expect(_row(page, "build-runner").locator("[data-ov-chip]")).to_contain_text(
                "Last known: Online"
            )
            expect(_row(page, "build-runner")).to_contain_text(
                re.compile(r"Last seen \d+ (seconds?|minutes?) before \d{1,2}:\d\d:\d\d")
            )
            page.clock.run_for(900000)  # a quarter of an hour and more: nothing else changes
            expect(_row(page, "build-runner").locator("[data-ov-chip]")).to_contain_text(
                "Last known: Online"
            )
            expect(_row(page, "Build box").locator("[data-ov-chip]")).to_have_text(
                "Last known: Running"
            )
            expect(_row(page, "Build box")).not_to_contain_text("nothing newer has arrived")
            # Attention follows the server snapshot only: the same counts as before, no runner made Offline by the clock.
            after = page.locator("[data-ov-summary]").inner_text()
            assert re.sub(r" Could not refresh.*", "", after) == counts, (counts, after)
            _shot(page, f"{ui}-desktop-45-old-read")
            context.close()
        finally:
            browser.close()
    assert errors == [] or all("503" in e for e in errors)


@pytest.mark.parametrize("ui", UIS)
def test_the_overview_refreshes_by_itself_and_stops_when_the_page_is_hidden(served, ui):
    fx = served
    fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, "desktop")
            page.clock.install()
            reads = _count_reads(page)
            _visit(page, fx, ui)
            _go(page, fx, ui)
            assert len(reads) == 1, reads

            def barrier():
                # A round trip to the page: every request it made before this call has been reported by now.
                page.evaluate("0")

            page.clock.run_for(29000)
            barrier()
            assert len(reads) == 1, "no read before the interval is over"
            with page.expect_request(lambda r: r.url.endswith(OVERVIEW)):
                page.clock.run_for(2000)
            expect(page.locator(".compute-ov-meta")).to_contain_text(
                "It refreshes every 30 seconds while this tab is visible"
            )
            assert len(reads) == 2, "one read after 30 seconds"
            # A hidden page does not read.
            page.evaluate(
                "Object.defineProperty(document, 'hidden', {value: true, configurable: true})"
            )
            page.clock.run_for(65000)
            barrier()
            assert len(reads) == 2, "a hidden page does not read"
            with page.expect_request(lambda r: r.url.endswith(OVERVIEW)):
                page.evaluate(
                    "Object.defineProperty(document, 'hidden', {value: false, configurable: true});"
                    " document.dispatchEvent(new Event('visibilitychange'))"
                )
            assert len(reads) == 3, "coming back reads at once"
            # Pausing stops the automatic reads (and says so); resuming reads at once.
            page.get_by_role("button", name="Pause automatic updates").click()
            expect(page.locator(".compute-ov-meta")).to_contain_text("Automatic updates are paused")
            page.clock.run_for(95000)
            barrier()
            assert len(reads) == 3, "paused: no automatic read"
            with page.expect_request(lambda r: r.url.endswith(OVERVIEW)):
                page.get_by_role("button", name="Resume automatic updates").click()
            assert len(reads) == 4
            # Leaving the screen stops the refresh.
            page.evaluate("location.hash = '#/'")
            barrier()
            page.clock.run_for(65000)
            barrier()
            assert len(reads) == 4, "a screen that is gone does not read"
            assert errors == []
            context.close()
        finally:
            browser.close()


@pytest.mark.parametrize("ui", UIS)
def test_a_refresh_never_rebuilds_an_open_detail_or_the_status_list(served, ui):
    fx = served
    fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, "desktop")
            page.clock.install()
            reads = _count_reads(page)
            _visit(page, fx, ui)
            _go(page, fx, ui)
            # The status list keeps its node across a refresh (a refresh that rebuilt it would close an open list).
            page.get_by_label("Filter by status").focus()
            page.evaluate("window.__select = document.querySelector('#compute-ov-status')")
            with page.expect_request(lambda r: r.url.endswith(OVERVIEW)):
                page.clock.run_for(31000)
            expect(page.get_by_label("Filter by status")).to_be_focused()
            assert page.evaluate("window.__select === document.querySelector('#compute-ov-status')")
            # An open detail keeps its panel, heading and body nodes across a refresh and a re-read of the item.
            page.get_by_role("button", name="Status and activity of edge-runner").click()
            expect(page.locator("#compute-ov-heading")).to_be_focused()
            page.evaluate(
                "window.__nodes = [document.querySelector('.compute-ov-panel'),"
                " document.querySelector('#compute-ov-heading'),"
                " document.querySelector('[data-ov-detail-body]'),"
                " document.querySelector('.compute-ov-panel [data-ov-chip]')]"
            )
            before = len(reads)
            with page.expect_response(lambda r: "/runner/" in r.url and r.status == 200):
                page.clock.run_for(31000)
            assert len(reads) == before + 1
            same = page.evaluate(
                "[document.querySelector('.compute-ov-panel'), document.querySelector('#compute-ov-heading'),"
                " document.querySelector('[data-ov-detail-body]'),"
                " document.querySelector('.compute-ov-panel [data-ov-chip]')].map((n, i) => n === window.__nodes[i])"
            )
            assert same == [True, True, True, True], same
            expect(page.locator("#compute-ov-heading")).to_be_focused()
            # The page's own Refresh rebuilds the screen around the overview: the open detail is carried across it.
            page.get_by_role("button", **REFRESH).first.click()
            expect(page.get_by_role("button", **REFRESH).first).to_be_focused()
            still = page.evaluate(
                "[document.querySelector('.compute-ov-panel'), document.querySelector('#compute-ov-heading')]"
                ".map((n, i) => n === window.__nodes[i])"
            )
            assert still == [True, True], still
            context.close()
        finally:
            browser.close()
    assert errors == []


@pytest.mark.parametrize("ui", UIS)
def test_a_status_that_changes_while_the_page_is_open_is_announced_once(served, ui):
    fx = served

    def body(page, label, context):
        _go(page, fx, ui)
        expect(page.locator("[data-ov-announce]")).to_have_text("")
        # Move a runner's last signal into the past, as if it stopped reporting, and read again. (The busy
        # runner has a started run, which is a signal of its own, so use the one that is merely slow.)
        with fx.store.gateway.db._lock:
            fx.store.gateway.db._conn.execute(
                "UPDATE connections SET last_used_at='2026-01-01T00:00:00Z' WHERE id IN "
                "(SELECT machine_connection_id FROM runner_machines WHERE label='review-runner')"
            )
        page.get_by_role("button", **REFRESH).first.click()
        expect(page.locator("[data-ov-announce]")).to_have_text("review-runner is now Offline.")
        expect(_row(page, "review-runner")).to_contain_text("Offline")

    _run(fx, ui, "desktop", body)


def test_the_phone_list_scrolls_by_touch_in_deck_and_classic(served):
    fx = served
    for ui in UIS:

        def body(page, label, context, ui=ui):
            _go(page, fx, ui)
            page.evaluate("() => document.querySelector('#content').scrollTo(0, 0)")
            start = page.evaluate(
                "() => document.querySelector('#content').scrollTop + document.scrollingElement.scrollTop"
            )
            _swipe(context, page, 200, 600, 120)
            moved = page.evaluate(
                "() => document.querySelector('#content').scrollTop + document.scrollingElement.scrollTop"
            )
            assert moved - start > 150, (
                f"{ui}: a swipe on the overview scrolls the page ({start} -> {moved})"
            )
            # The last row is reachable and not hidden under the tab bar.
            page.get_by_role(
                "button", name="Status and activity of Unplaced box"
            ).scroll_into_view_if_needed()
            box = page.get_by_role(
                "button", name="Status and activity of Unplaced box"
            ).bounding_box()
            tabs = page.locator("#phone-tabs .m-tabs").bounding_box()
            assert tabs is None or box["y"] + box["height"] <= tabs["y"] + 1
            _shot(page, f"{label}-50-scrolled")

        _run(fx, ui, "phone", body)
