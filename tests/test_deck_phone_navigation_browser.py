"""Phone shell continuity through the real board, card and settings controllers."""

import asyncio
import shutil
from urllib.parse import urlunsplit

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh


async def open_app(browser, hub, fresh, *, look="deck", width=390):
    context = await browser.new_context(
        viewport={"width": width, "height": 844 if width == 390 else 800},
        device_scale_factor=2,
    )
    host, port = hub.server.server_address
    origin = urlunsplit(("http", f"{host}:{port}", "", "", ""))
    name, value = fresh.cookie["Cookie"].split("=", 1)
    await context.add_cookies([{"name": name, "value": value, "url": origin}])
    page = await context.new_page()
    return context, page, origin + f"/app/?ui={look}"


async def board_ready(page, url):
    await page.goto(url + "#/board/work")
    await page.wait_for_selector(".workbench-board")
    # The dock callback runs after the initial navigation render.
    await page.wait_for_function("document.querySelector('#chat-toggle')?.isConnected")
    await page.evaluate("document.fonts.ready")


async def assert_tabs(page):
    tabs = page.locator('[data-deck="tabs"]')
    await tabs.wait_for(state="visible")
    assert await tabs.count() == 1
    metrics = await page.evaluate("""() => {
      const e=document.querySelector('[data-deck="tabs"]');
      return {labels:[...e.querySelectorAll('a')].map(a=>a.textContent),
        bottom:e.getBoundingClientRect().bottom, width:document.documentElement.scrollWidth};
    }""")
    assert metrics["labels"] == ["Today", "Bots", "Compute", "Settings"]
    assert metrics["bottom"] == pytest.approx(844)
    assert metrics["width"] == 390


async def assert_gutters(page, selector):
    padding = await page.locator(selector).first.evaluate(
        "e => [getComputedStyle(e).paddingLeft, getComputedStyle(e).paddingRight]"
    )
    assert padding == ["16px", "16px"]


@pytest.mark.parametrize("surface", ["board", "card", "project-settings", "needs"])
def test_phone_destinations_survive_dock_and_drawer_return(hub, fresh, surface):
    playwright = pytest.importorskip("playwright.async_api")
    hub.gateway.rate_limits = False
    card = fresh.owner.task_add(board="work", title="Review the release checklist")

    async def check():
        async with playwright.async_playwright() as manager:
            browser = await manager.chromium.launch(executable_path=shutil.which("google-chrome"))
            try:
                context, page, url = await open_app(browser, hub, fresh)
                await board_ready(page, url)
                for destination in ["today", "bots", "compute", "settings"]:
                    await board_ready(page, url)
                    if surface == "card":
                        await page.locator(f'.card[data-card="{card["number"]}"]').click()
                        await page.wait_for_selector("#card-panel:not([hidden]) .workbench-details")
                        await assert_tabs(page)
                        await assert_gutters(page, ".workbench-details .ctx-body")
                        # Browser Back closes a card and restores its board (one tap).
                        await page.go_back()
                        await page.wait_for_selector("#card-panel[hidden]", state="attached")
                    elif surface == "project-settings":
                        await page.get_by_role(
                            "button", name="Project settings", exact=True
                        ).click()
                        await page.wait_for_selector("[data-project-settings] .workbench-project")
                        await assert_tabs(page)
                        await assert_gutters(page, ".workbench-project .project-top")
                        await assert_gutters(page, ".workbench-project .settings")
                        await page.locator("[data-project-back]").click()
                        await page.wait_for_selector("#card-panel[hidden]", state="attached")
                        await page.get_by_role(
                            "button", name="Project settings", exact=True
                        ).click()
                        await page.wait_for_selector("[data-project-settings] .workbench-project")
                        await page.go_back()
                        await page.wait_for_selector("#card-panel[hidden]", state="attached")
                        assert await page.evaluate("location.hash") == "#/board/work"
                    elif surface == "needs":
                        await page.goto(url + "#/needs")
                        await page.wait_for_selector(".needs-me")
                        await assert_gutters(page, "#content")
                    else:
                        await assert_gutters(page, ".workbench-board .board")
                    await assert_tabs(page)
                    await page.locator(f'[data-deck="tabs"] a[href="#/{destination}"]').click()
                    await page.wait_for_function(
                        "hash => location.hash === hash", arg=f"#/{destination}"
                    )
                    await assert_tabs(page)
                # Workspace tools remain two taps away via Settings.
                await page.wait_for_selector("#deck-tools")
                for href in ["#/boards", "#/memory", "#/needs"]:
                    assert await page.locator(f'#deck-tools a[href="{href}"]').is_visible()
                await page.locator('#deck-tools [data-action="switch-workspace"]').click()
                await page.wait_for_selector("#shell-dialog[open]")
                assert "Switch workspace" in await page.locator("#shell-dialog").inner_text()
                await context.close()
            finally:
                await browser.close()

    asyncio.run(check())


def test_phone_tabs_stay_at_bottom_on_nested_and_workspace_routes(hub, fresh):
    playwright = pytest.importorskip("playwright.async_api")
    hub.gateway.rate_limits = False

    async def check():
        async with playwright.async_playwright() as manager:
            browser = await manager.chromium.launch(executable_path=shutil.which("google-chrome"))
            try:
                context, page, url = await open_app(browser, hub, fresh)
                await board_ready(page, url)
                bots = await page.evaluate("fetch('/api/bots').then(r => r.json())")
                routes = [
                    "#/boards",
                    "#/memory",
                    "#/rules",
                    "#/console",
                    "#/admin",
                    "#/decide",
                    "#/compute/management",
                    "#/bots/-/coworkers",
                    "#/bots/-/coworkers/conversations",
                    "#/board/work/chat",
                ]
                routes.extend(f"#/bots/{bot['id']}" for bot in bots["bots"])
                for route in routes:
                    await page.goto(url + route)
                    await assert_tabs(page)
                await context.close()
            finally:
                await browser.close()

    asyncio.run(check())
