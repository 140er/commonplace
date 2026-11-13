"""Real join button under the shipped CSP and referrer policy."""

import os
from datetime import timedelta
from pathlib import Path

import pytest

from commonplace import invites, sessions
from tests.test_commonplace_invites import invite, world as world


@pytest.mark.parametrize("viewport", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_invite_join_with_shipped_policies(chromium, world, viewport):
    from playwright.sync_api import expect

    origin = f"http://127.0.0.1:{world.port}"
    context = chromium.new_context(
        viewport=dict(zip(("width", "height"), viewport)), device_scale_factor=2
    )
    context.add_cookies(
        [
            {
                "name": sessions.COOKIE,
                "value": world.guest.browser.cookies[sessions.COOKIE],
                "url": origin,
            }
        ]
    )
    context.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(origin + "/") else route.abort()
        ),
    )
    page = context.new_page()
    page.set_default_timeout(5000)
    shot_dir = os.environ.get("CP_INVITE_SHOTS")

    def shot(state):
        if shot_dir:
            dest = Path(shot_dir)
            dest.mkdir(parents=True, exist_ok=True)
            suffix = "desktop" if viewport[0] == 1280 else "phone"
            page.screenshot(path=str(dest / f"invite-{state}-{suffix}.png"))

    try:
        made = invite(world)
        response = page.goto(origin + "/app/join?t=" + made["secret"])
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "form-action 'self'" in response.headers["content-security-policy"]
        expect(page.get_by_role("button", name="Join workspace")).to_be_visible()
        shot("confirm")
        with page.expect_request(
            lambda request: request.method == "POST" and request.url == origin + "/app/join"
        ) as submitted:
            page.get_by_role("button", name="Join workspace").click()
        headers = submitted.value.all_headers()
        assert headers["origin"] == origin
        assert "referer" not in headers
        page.wait_for_url(origin + "/app/")
        assert world.db.role(world.owner.workspace, world.guest.id) == "member"
        me = context.request.get(origin + "/api/me").json()
        assert me["workspace"] == world.owner.workspace and not me["can_admin"]
        # The already-member page is a stable invite-screen view of success.
        another = invite(world)
        page.goto(origin + "/app/join?t=" + another["secret"])
        expect(
            page.get_by_role("heading", name="You are already in this workspace")
        ).to_be_visible()
        shot("joined")

        page.goto(origin + "/app/join?t=" + made["secret"])
        expect(page.get_by_role("heading", name="This invite does not work")).to_be_visible()
        expect(
            page.get_by_text("Ask the workspace owner for a new invite.", exact=False)
        ).to_be_visible()
        shot("replay")
        rows = {row["id"]: row for row in world.owner.browser.get("/api/invites").json()["invites"]}
        assert rows[another["invite"]["id"]]["state"] == "pending"
    finally:
        context.close()


@pytest.mark.parametrize("kind", ["expired", "wrong-person"])
@pytest.mark.parametrize("viewport", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_invite_changes_before_confirmation(chromium, world, monkeypatch, kind, viewport):
    from playwright.sync_api import expect

    origin = f"http://127.0.0.1:{world.port}"
    context = chromium.new_context(
        viewport=dict(zip(("width", "height"), viewport)), device_scale_factor=2
    )
    context.add_cookies(
        [
            {
                "name": sessions.COOKIE,
                "value": world.guest.browser.cookies[sessions.COOKIE],
                "url": origin,
            }
        ]
    )
    context.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(origin + "/") else route.abort()
        ),
    )
    page = context.new_page()
    page.set_default_timeout(5000)
    try:
        made = invite(world, email=world.guest.email)
        page.goto(origin + "/app/join?t=" + made["secret"])
        expect(page.get_by_role("button", name="Join workspace")).to_be_visible()
        if kind == "expired":
            now = invites._now()
            monkeypatch.setattr(invites, "_now", lambda: now + timedelta(days=8))
            title, next_step, status = (
                "This invite does not work",
                "Ask the workspace owner for a new invite.",
                404,
            )
        else:
            other = world.person("other@example.test", "Other Home")
            context.add_cookies(
                [
                    {
                        "name": sessions.COOKIE,
                        "value": other.browser.cookies[sessions.COOKIE],
                        "url": origin,
                    }
                ]
            )
            title, next_step, status = (
                "This invite is for a different account",
                "Sign out, then sign in",
                403,
            )
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url == origin + "/app/join"
        ) as submitted:
            page.get_by_role("button", name="Join workspace").click()
        assert submitted.value.status == status
        expect(page.get_by_role("heading", name=title)).to_be_visible()
        expect(page.get_by_text(next_step, exact=False)).to_be_visible()
        assert world.db.role(world.owner.workspace, world.guest.id) is None
        if kind == "wrong-person":
            assert world.db.role(world.owner.workspace, other.id) is None
        shot_dir = os.environ.get("CP_INVITE_SHOTS")
        if shot_dir:
            Path(shot_dir).mkdir(parents=True, exist_ok=True)
            suffix = "desktop" if viewport[0] == 1280 else "phone"
            page.screenshot(path=str(Path(shot_dir) / f"invite-{kind}-{suffix}.png"))
    finally:
        context.close()
