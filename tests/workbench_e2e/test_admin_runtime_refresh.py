"""Real Admin reads keep configured limits and keyboard position clear."""

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_admin_runtime_api import machine, runtime as runtime


@contextmanager
def _admin(hub, chromium, look, width=1440):
    from playwright.sync_api import expect

    hub.db._conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (hub.admin,))
    machine(hub, binding_state="owner_attested", enabled=False)
    link = sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    session = sessions.redeem_login_link(hub.db, link)
    host, port = hub.server.server_address
    origin = f"http://{host}:{port}"
    context = chromium.new_context(viewport={"width": width, "height": 900})
    context.add_cookies([{"name": sessions.COOKIE, "value": session, "url": origin}])
    try:
        page = context.new_page()
        page.goto(origin + f"/app/?ui={look}#/admin", wait_until="domcontentloaded")
        panel = page.locator('[data-admin-panel="runtime"]')
        refresh = panel.get_by_role("button", name="Refresh Runtime", exact=True)
        expect(refresh).to_be_visible()
        expect(page.locator('.admin [aria-busy="true"]')).to_have_count(0)
        yield page, panel, refresh
    finally:
        page.unroute_all(behavior="ignoreErrors")
        context.close()


def _shot(page, name):
    directory = os.environ.get("ADMIN_REFRESH_SHOTS")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / f"{name}.png"))


@pytest.mark.parametrize("look", ["classic", "deck"])
def test_admin_limits_are_configuration_not_active_work(hub, runtime, chromium, look):
    from playwright.sync_api import expect

    with _admin(hub, chromium, look) as (page, panel, _):
        row = panel.get_by_role("row").filter(has_text="Machine 1")
        expect(row).to_contain_text("Limit: 1 run at once; 6 starts/hour")
        runner = page.locator('article.runner-machine[aria-label="Machine 1"]')
        expect(runner).to_contain_text("Limit: 1 run at once")
        expect(runner).not_to_contain_text("1 in flight")
        _shot(page, f"{look}-configured-limits")


@pytest.mark.parametrize("look", ["classic", "deck"])
@pytest.mark.parametrize("outcome", ["success", "error"])
def test_runtime_refresh_keeps_focus_without_stealing_it(hub, runtime, chromium, look, outcome):
    from playwright.sync_api import expect

    with _admin(hub, chromium, look) as (page, panel, refresh):
        held = []

        def hold(route):
            held.append(route)
            page.evaluate("window.adminReadHeld = true")

        page.route("**/api/admin/**/runtime", hold)

        def begin():
            page.evaluate("window.adminReadHeld = false")
            refresh.focus()
            page.keyboard.press("Enter")
            page.wait_for_function("window.adminReadHeld === true")
            expect(panel).to_have_attribute("aria-busy", "true")

        def finish():
            route = held.pop()
            if outcome == "error":
                route.fulfill(status=503, json={"error": "Synthetic read failure"})
            else:
                route.fulfill(response=route.fetch())
            expect(panel).to_have_attribute("aria-busy", "false")

        begin()
        expect(refresh).to_be_focused()
        expect(refresh).to_have_attribute("aria-disabled", "true")
        page.keyboard.press("Enter")
        assert len(held) == 1, "a busy refresh must not issue another read"
        finish()
        expect(refresh).to_be_focused()
        if outcome == "error":
            expect(panel.get_by_role("status")).to_have_text("Runtime unavailable. Retry.")
        _shot(page, f"{look}-refresh-{outcome}-focused")

        begin()
        elsewhere = page.get_by_role("link", name="Manage connected apps and workspace settings")
        elsewhere.focus()
        expect(elsewhere).to_be_focused()
        finish()
        expect(elsewhere).to_be_focused()
        assert len(held) == 0


@pytest.mark.parametrize("look", ["classic", "deck"])
@pytest.mark.parametrize("width", [320, 390])
def test_runtime_refresh_has_a_phone_hit_target(hub, runtime, chromium, look, width):
    with _admin(hub, chromium, look, width) as (page, _, refresh):
        refresh.scroll_into_view_if_needed()
        box = refresh.bounding_box()
        assert box is not None
        assert box["width"] >= 44
        assert box["height"] >= 44
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        _shot(page, f"{look}-{width}-refresh-target")
