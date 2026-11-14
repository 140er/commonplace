"""Optional browser evidence using the real shell, gateway and readable directory."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from commonplace.chat.coworkers.store import open_store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_deck_phone_navigation_browser import open_app


def test_shell_desktop_and_phone_chrome(hub, fresh, tmp_path):
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.async_api", reason="optional browser dependency")
    hub.gateway.rate_limits = False
    store = open_store(hub.gateway, fresh.workspace)
    bot = store.create_bot(fresh.user, "Research")
    store.create_conversation(fresh.user, "dm", title="Plan the next release", bot_ids=[bot["id"]])
    output = Path(os.environ.get("CAPY_SHELL_SHOTS", str(tmp_path / "shell-shots")))
    output.mkdir(parents=True, exist_ok=True)

    async def check():
        async with playwright.async_playwright() as manager:
            try:
                browser = await manager.chromium.launch(
                    executable_path=shutil.which("google-chrome")
                )
            except playwright.Error as exc:
                if "Executable doesn't exist" in str(exc):
                    pytest.skip("optional browser executable unavailable")
                raise
            try:
                for width, height, name in [
                    (1440, 900, "desktop"),
                    (768, 900, "tablet"),
                    (390, 844, "phone"),
                ]:
                    context, page, url = await open_app(browser, hub, fresh, width=width)
                    await page.set_viewport_size({"width": width, "height": height})
                    errors = []
                    page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                    await page.goto(url + "#/today")
                    await page.wait_for_selector('[data-deck-folder="Bots"]', state="attached")
                    await page.wait_for_function(
                        "getComputedStyle(document.querySelector('[data-deck=bar]')).backgroundColor === 'rgb(17, 16, 21)'"
                        if width >= 700
                        else "document.querySelector('[data-deck=top]')?.getBoundingClientRect().height === 52"
                    )
                    await page.evaluate("document.fonts.ready")
                    assert await page.evaluate("document.documentElement.scrollWidth") == width
                    if width >= 700:
                        rail = page.locator('[data-deck="bar"]')
                        assert (await rail.bounding_box())["width"] == 252
                        assert (
                            await rail.evaluate("e=>getComputedStyle(e).backgroundColor")
                            == "rgb(17, 16, 21)"
                        )
                        assert (
                            await page.locator('[data-deck-folder="Bots"] .deck-tree-row').count()
                            == 1
                        )
                        assert (await page.locator(".deck-tree-row").bounding_box())["height"] == 26
                        await page.locator('[data-deck-folder="Bots"] summary').click()
                        await page.locator('.deck-links [data-nav="bots"]').click()
                        await page.wait_for_function(
                            "!document.querySelector('[data-deck-folder=\"Bots\"]').open"
                        )
                        await page.locator('[data-deck-folder="Bots"] summary').click()
                    else:
                        top = page.locator('[data-deck="top"]')
                        assert (await top.bounding_box())["height"] == 52
                        assert await top.locator("b").text_content() == "Today"
                        tabs = page.locator('[data-deck="tabs"]')
                        assert await tabs.locator("a").count() == 4
                        assert await tabs.locator('[data-deck-shell-action="mic"]').is_visible()
                        for entry in ["bots", "compute", "settings", "today"]:
                            await tabs.locator(f'[data-nav="{entry}"]').click()
                            await page.wait_for_function(
                                "hash => location.hash === hash", arg=f"#/{entry}"
                            )
                            await top.wait_for(state="visible")
                            assert (await top.bounding_box())["height"] == 52
                            assert (
                                await page.evaluate("document.documentElement.scrollWidth") == width
                            )
                    if width == 390:
                        roster = await page.request.get(url.split("/app/")[0] + "/api/bots")
                        personal = (await roster.json())["bots"][0]
                        await page.goto(url + "#/bots/" + personal["id"])
                        await page.wait_for_selector('.bots[data-pane="thread"]')
                        await page.locator('[data-deck="top"]').wait_for(state="visible")
                        await page.locator('[data-deck="tabs"]').wait_for(state="visible")
                        await page.goto(url + "#/today")
                        await page.locator('[data-deck="top"]').wait_for(state="visible")
                    await page.screenshot(path=str(output / f"shell-{name}.png"))
                    assert not errors, errors
                    await context.close()
            finally:
                await browser.close()

    asyncio.run(check())
