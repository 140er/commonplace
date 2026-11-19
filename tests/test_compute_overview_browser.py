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

import pytest

from commonplace import sessions
from commonplace.compute import workspace_lifecycle as lifecycle
from commonplace.runner import drain
from tests.compute_overview_helpers import FakeExecutor, Fleet, revoke_machine
from tests.test_compute_workspaces_browser import (  # noqa: F401 -- shared real-gateway fixtures and helpers
    SIZES,
    TOUCH,
    UIS,
    _no_sideways_scroll,
    _serve,
    _swipe,
    expect,
    pytestmark,
    sync_playwright,
)

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
    busy = fleet.enroll("build-runner", max_in_flight=2, **wings)
    fleet.seen(busy, 40)
    fleet.job(busy, "started", task=1)
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
    fleet.job(draining, "started", task=2, minutes_ago=5)
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
                now="2026-11-19T09:00:00Z",
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
            with fx.store._journal() as conn:
                conn.execute(
                    "UPDATE cloud_workspace_lifecycle SET reported_at=reported_at-1000 WHERE workspace_id=?",
                    (names[name],),
                )
        else:
            executor.report(claim, "failed", reason="out_of_memory")
    ask("Queued box", "start")
    request(names["Queued box"], "start")
    ask("New box", "start")
    names["Unplaced box"] = fx.make(name="Unplaced box")["id"]
    # No executor is registered in production: the screen must say a request waits, so unregister it here.
    del fx.store.gateway._compute_workspace_executor
    return names


def _go(page, fx, ui="deck"):  # the list has painted
    expect(page.locator(f"{ROOT} .compute-ov-row").first).to_be_visible()


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
    _no_sideways_scroll(page)
    _assert_fits(page)


def _open(browser, fx, ui, size, cookie=None, viewport=None):
    width, height = viewport or SIZES[size]
    phone = width < 700
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


def _run(fx, ui, size, body, cookie=None, viewport=None, populate=True, expect_errors=False):
    if populate and not hasattr(fx, "env"):
        fx.names = _populate(fx)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size, cookie, viewport)
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
        expect(page.locator(f"{ROOT} .compute-ov-summary")).to_contain_text(
            "7 runners, 1 machine, 7 cloud workspaces"
        )
        # Runners: each state says what it is and how old the signal is.
        expect(_row(page, "build-runner")).to_contain_text("Busy")
        expect(_row(page, "build-runner")).to_contain_text(re.compile(r"Last seen 4\d seconds ago"))
        expect(_row(page, "build-runner")).to_contain_text("1 run in progress (up to 2 at once)")
        expect(_row(page, "review-runner")).to_contain_text("Slow to report")
        expect(_row(page, "edge-runner")).to_contain_text("Offline")
        expect(_row(page, "edge-runner")).to_contain_text("Last seen 25 minutes ago")
        expect(_row(page, "edge-runner")).to_contain_text("1 thing blocks new work")
        expect(_row(page, "spare-runner")).to_contain_text("Never connected")
        expect(_row(page, "spare-runner")).to_contain_text("No signal yet")
        expect(_row(page, "night-runner")).to_contain_text("Draining")
        expect(_row(page, "idle-runner")).to_contain_text("Drained")
        expect(_row(page, "idle-runner")).to_contain_text("none is in progress")
        expect(_row(page, "retired-runner")).to_contain_text("Revoked")
        # An offline runner is never green and never says online or running.
        offline = _row(page, "edge-runner")
        assert (
            "Online" not in offline.inner_text()
            and "running"
            not in offline.inner_text()
            .lower()
            .replace("runs it already started may still be going", "")
        )
        # Cloud workspaces: a stale "running" is unknown; a failure says why; a request is only a request.
        expect(_row(page, "Build box")).to_contain_text("Running")
        expect(_row(page, "Old box")).to_contain_text("Unknown")
        expect(_row(page, "Old box")).to_contain_text("nothing newer has arrived")
        expect(_row(page, "Broken box")).to_contain_text("It ran out of memory")
        expect(_row(page, "Docs box")).to_contain_text("Stopped")
        expect(_row(page, "Queued box")).to_contain_text("Start requested")
        expect(_row(page, "Queued box")).to_contain_text("nothing has started yet")
        expect(_row(page, "New box")).to_contain_text("Not started")
        expect(_row(page, "Unplaced box")).to_contain_text("No machine chosen")
        expect(_row(page, "Build machine")).to_contain_text("Not verified")
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
        expect(page.locator("[data-ov-announce]")).to_have_text("Showing 7 items.")
        expect(page.get_by_role("button", name=re.compile(r"^Runners"))).to_be_focused()
        page.get_by_label("Filter by status").select_option(label="Offline (1)")
        expect(_rows(page)).to_have_count(1)
        expect(_row(page, "edge-runner")).to_be_visible()
        _shot(page, f"{label}-11-filtered")
        page.get_by_role("button", name=re.compile(r"^Cloud workspaces \(7\)")).click()
        expect(page.locator(ROOT)).to_contain_text(
            "No machines, runners or cloud workspaces match these filters."
        )
        _shot(page, f"{label}-12-no-match")
        page.get_by_role("button", name="Clear filters").first.click()
        expect(_rows(page)).to_have_count(15)
        expect(page.get_by_label("Filter by status")).to_be_focused()
        page.get_by_label("Filter by status").select_option(label="Needs attention (6)")
        expect(_rows(page)).to_have_count(6)
        # The problems come first inside each group.
        first = page.locator(f"{ROOT} .compute-ov-group").first.locator(".compute-ov-row").first
        expect(first).to_have_attribute("data-status", re.compile("offline|revoked|slow|disabled"))
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
        expect(panel).to_contain_text("Commonplace has not heard from it for over 15 minutes")
        expect(panel).to_contain_text("Machine resources")
        expect(panel).to_contain_text("Not reported: this machine has not sent a resource report.")
        expect(panel).to_contain_text("Container logs")
        expect(panel).to_contain_text("Not collected")
        assert page.locator('.compute-ov [role="alert"]').count() == 0
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
                    "It has runs in progress.",
                    "1 run in progress (up to 2 at once)",
                    "6.0 GiB memory free",
                    "2 runs with unknown cost",
                ],
                "23-detail-busy",
            ),
            (
                "night-runner",
                [
                    "No new runs start here. 1 run in progress will finish.",
                    "It is draining: no new runs start until you resume it.",
                ],
                "24-detail-draining",
            ),
            (
                "idle-runner",
                [
                    "No new runs start here and none is in progress.",
                    "Resume to use it again.",
                    "Drain started: no new runs start here",
                ],
                "27-detail-drained",
            ),
            (
                "Queued box",
                ["Commonplace saved the request", "You can withdraw the request."],
                "25-detail-requested",
            ),
            (
                "Build machine",
                ["Not checked live", "Commonplace does not probe target machines."],
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
            "Machines and runners are visible to the workspace owner."
        )
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
        expect(page.locator(ROOT)).to_contain_text("Loading the overview…")
        assert page.locator(f"{ROOT} .compute-ov-row").count() == 0
        _shot(page, label)
        gate["route"].continue_()
        # A fresh workspace: nothing is listed, and the empty state says what to do next.
        expect(page.locator(ROOT)).to_contain_text("Nothing here yet.")
        expect(page.locator(ROOT)).to_contain_text(
            "Set up a runner machine, add a target machine under Advanced"
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
        alert = page.locator(f"{ROOT} [role=alert]")
        expect(alert).to_have_text(
            "The overview could not be loaded. Nothing is shown rather than a guess."
        )
        _shot(page, label)
        page.unroute(f"**{OVERVIEW}")
        page.get_by_role("button", name="Try again").click()
        expect(page.locator(ROOT)).to_contain_text("Nothing here yet.")
        assert page.locator(f"{ROOT} [role=alert]").count() == 0

    run_with(broken, "41-failed", failed)


@pytest.mark.parametrize("ui", UIS)
def test_a_refresh_that_fails_keeps_the_old_data_and_says_it_is_old(served, ui):
    fx = served

    def body(page, label, context):
        _go(page, fx, ui)
        before = _shown(page)
        page.route(
            f"**{OVERVIEW}",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body='{"error":"authority_unavailable"}',
            ),
        )
        page.get_by_role("button", name="Update overview").click()
        expect(page.locator(ROOT)).to_contain_text("Could not refresh. Showing the data from")
        expect(page.locator(ROOT)).to_contain_text("it may be out of date")
        expect(page.locator("[data-ov-announce]")).to_contain_text("could not be refreshed")
        assert "edge-runner" in _shown(page) and "edge-runner" in before
        _shot(page, f"{label}-44-stale")
        page.unroute(f"**{OVERVIEW}")
        page.get_by_role("button", name="Update overview").click()
        expect(page.locator(ROOT)).not_to_contain_text("Could not refresh")
        expect(page.get_by_role("button", name="Update overview")).to_be_focused()

    _run(fx, ui, "desktop", body, expect_errors=True)  # the 503 is the point of this case


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
                "It refreshes every 30 seconds"
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
            # Leaving the screen stops the refresh.
            page.evaluate("location.hash = '#/'")
            barrier()
            page.clock.run_for(65000)
            barrier()
            assert len(reads) == 3, "a screen that is gone does not read"
            assert errors == []
            context.close()
        finally:
            browser.close()


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
        page.get_by_role("button", name="Update overview").click()
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
