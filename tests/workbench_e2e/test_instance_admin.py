"""Instance console acceptance with synthetic identities and a stalled section."""

import pytest

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_admin_api import agent, member


@pytest.mark.parametrize("width", [1440, 390])
def test_instance_console_member_admin_foreign_target_and_retry(hub, chromium, width):
    from playwright.sync_api import expect

    uid = member(hub)
    hub.db._conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
    other = hub.db.create_user("elsewhere@example.com", "Elsewhere")
    foreign = hub.db.create_workspace("Elsewhere", other)
    agent(hub, "synthetic-connection")
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "member@example.com", hub.workspace)
    )
    origin = f"http://127.0.0.1:{hub.port}"
    context = chromium.new_context(viewport={"width": width, "height": 1000})
    context.add_cookies([{"name": sessions.COOKIE, "value": session, "url": origin}])
    try:
        page = context.new_page()
        errors, reads = [], []
        page.on("pageerror", lambda error: errors.append(type(error).__name__))
        page.on(
            "request",
            lambda req: reads.append(req.method) if "/api/instance-admin/" in req.url else None,
        )
        for path in ("boards", "needs-me", "rules/count", "events?*"):
            page.route(f"**/api/{path}", lambda route: None)
        calls = 0

        def connections(route):
            nonlocal calls
            calls += 1
            if calls == 1:
                return  # The real five-second request deadline must settle this section.
            route.continue_()

        page.route("**/api/instance-admin/connections?*", connections)
        page.goto(origin + "/app/#/instance-admin", wait_until="domcontentloaded")
        people = page.locator('[data-instance-area="people"]')
        expect(people.get_by_text("member@example.com", exact=True)).to_be_visible()
        connection_panel = page.locator('[data-instance-panel="connections"]')
        expect(connection_panel.get_by_role("button", name="Retry", exact=True)).to_be_visible(
            timeout=10000
        )
        connection_panel.get_by_role("button", name="Retry", exact=True).click()
        expect(connection_panel.get_by_text("synthetic-connection", exact=True)).to_be_visible()
        expect(people.get_by_text("member@example.com", exact=True)).to_be_visible()
        page.locator("[data-instance-workspace]").select_option(foreign)
        expect(people.get_by_text("elsewhere@example.com", exact=True)).to_be_visible()
        expect(people.get_by_text("member@example.com", exact=True)).to_have_count(0)
        assert (
            hub.db._conn.execute(
                "SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?", (foreign, uid)
            ).fetchone()
            is None
        )
        assert page.locator(".instance-grid > section").count() == 5
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if width < 700:
            page.get_by_role("button", name="Project: Projects. Switch project", exact=True).click()
            expect(page.get_by_role("option", name="Instance console", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
        else:
            page.locator(".user-menu summary").click()
            expect(page.get_by_role("link", name="Instance console", exact=True)).to_be_visible()
        assert reads and set(reads) == {"GET"}
        assert not errors
    finally:
        context.close()


def test_instance_console_denied_direct_route_has_no_inventory(hub, chromium):
    from playwright.sync_api import expect

    member(hub)
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "member@example.com", hub.workspace)
    )
    origin = f"http://127.0.0.1:{hub.port}"
    context = chromium.new_context(viewport={"width": 390, "height": 844})
    context.add_cookies([{"name": sessions.COOKIE, "value": session, "url": origin}])
    try:
        page = context.new_page()
        reads = []
        page.on(
            "request",
            lambda req: reads.append(req.method) if "/api/instance-admin/" in req.url else None,
        )
        page.goto(origin + "/app/#/instance-admin", wait_until="domcontentloaded")
        expect(
            page.get_by_text("Instance administrator access required", exact=True)
        ).to_be_visible()
        assert reads == []
        assert page.locator('a[href="#/instance-admin"]').count() == 0
    finally:
        context.close()
