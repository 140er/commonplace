"""The cards:decide consent screen and Agents scope UI in a real browser, synthetic data only.

Run with Playwright installed (for example ``uv run --with playwright==1.64.1``) and a
Chromium cache; set PLAYWRIGHT_BROWSERS_PATH when HOME is isolated. Set
DECIDE_SCOPE_SHOTS to a directory to keep screenshots.
"""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_commonplace_oauth import Client

SCOPE = "memory:read memory:write cards:decide"
DECIDE = "cards:decide"
# A literal copy, so the file runs (and fails behaviourally) on a base without the scope.
SCOPE_TEXT = (
    "Allow this app to answer your Needs-me questions as you. It can answer only questions "
    "addressed to you, never another person's, and every answer is recorded with this "
    "connection. You can stop this at any time under Agents."
)


async def _shot(target, name):
    """Screenshot a page or element; no token is on screen when this runs."""
    folder = os.environ.get("DECIDE_SCOPE_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        await target.screenshot(path=str(Path(folder) / name))


async def consent_and_agents(hub):
    from playwright.async_api import async_playwright, expect

    base = f"http://127.0.0.1:{hub.port}"
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    )
    client = Client(hub)
    cid = client.register(client_name="Owner Phone")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(viewport={"width": 1100, "height": 900})
            await context.add_cookies([{"name": sessions.COOKIE, "value": session, "url": base}])
            # The consent POST's redirect to the client is recorded, never followed.
            locations = []

            async def consent_post(route):
                if route.request.method != "POST":
                    await route.continue_()
                    return
                response = await route.fetch(max_redirects=0)
                locations.append(response.headers.get("location", ""))
                await route.fulfill(status=200, body="redirected to client")

            await context.route(f"{base}/oauth/authorize**", consent_post)
            page = await context.new_page()
            page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))

            # Consent screen: the scope is named, unticked by default, and grantable.
            await page.goto(base + client.authorize_path(cid, scope=SCOPE))
            box = page.get_by_label(SCOPE_TEXT)
            await expect(box).to_be_visible()
            await expect(box).not_to_be_checked()
            await _shot(page, "consent.png")
            await box.check()
            await page.get_by_role("button", name="Allow").click()
            await expect(page.get_by_text("redirected to client")).to_be_visible()
            code = parse_qs(urlsplit(locations[-1]).query)["code"][0]
            status, _, raw = client.exchange(cid, code)
            assert status == 200
            access = json.loads(raw)["access_token"]
            app = hub.gateway.authenticate("Bearer " + access)
            assert app.policy.has_scope(DECIDE)

            # Agents settings: the OAuth client shows the grant and a way to stop it.
            await page.goto(base + "/app/#/settings")
            section = page.locator(".agents-settings")
            await expect(section).to_be_visible(timeout=15000)
            row = section.locator(".setting-row", has_text=app.connection)
            await expect(row.locator('[data-agent-decide="on"]')).to_have_text(
                "Answers your Needs-me questions (cards:decide)"
            )
            # The create form offers the same consent for a new token.
            form_box = section.locator(".agent-form").get_by_label(SCOPE_TEXT, exact=False)
            await expect(form_box).not_to_be_checked()
            await section.locator('.agent-form input[name="name"]').fill("phone-ui")
            await form_box.check()
            await section.get_by_role("button", name="Create token").click()
            await expect(section.locator("[data-agent-token]")).to_be_visible()
            pat_row = section.locator(".setting-row", has_text="phone-ui")
            await expect(pat_row.locator('[data-agent-decide="on"]')).to_be_visible()
            await section.get_by_role("button", name="Done").click()
            await expect(section.locator("[data-agent-token]")).to_have_count(0)
            await _shot(section.locator("[data-agent-list]"), "agents-granted.png")
            pat = hub.db.connection("phone-ui", hub.workspace)
            assert DECIDE in json.loads(pat["policy"])["scopes"]

            # Stop answering on the OAuth client: the row updates and the token loses it at once.
            await row.get_by_role("button", name="Stop answering").click()
            await expect(row.locator('[data-agent-decide="on"]')).to_have_count(0)
            assert not hub.gateway.authenticate("Bearer " + access).policy.has_scope(DECIDE)
            # And on the PAT: the row then offers to allow it again.
            await pat_row.get_by_role("button", name="Stop answering").click()
            await expect(
                pat_row.get_by_role("button", name="Allow answering Needs-me")
            ).to_be_visible()
            await _shot(section.locator("[data-agent-list]"), "agents-stopped.png")
            pat = hub.db.connection("phone-ui", hub.workspace)
            assert DECIDE not in json.loads(pat["policy"])["scopes"]
        finally:
            await browser.close()


def test_consent_and_agents_ui_show_and_revoke_cards_decide(hub):
    pytest.importorskip("playwright.async_api", reason="optional browser regression dependency")
    asyncio.run(consent_and_agents(hub))
