"""Optional browser walkthrough of the bot tree: real shell, gateway and bots file.

Desktop rail and the 375 px and 320 px phone layouts: bots open onto their projects, Unassigned, folding
remembered per device, the keyboard, and moving a project with a read-back. Screenshots go to
``CAPY_TREE_SHOTS`` when set.
"""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from commonplace.bots import store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_deck_phone_navigation_browser import open_app

PROJECTS = [("blue-eye", "Blue Eye"), ("halo-avatar", "HALO avatar"), ("loose", "Loose ends")]
TREE = "[data-deck-bots] [role=treeitem]"


def item(key, host="[data-deck-bots]"):
    return f'{host} [data-deck-item="{key}"]'


def test_bot_tree_desktop_and_phone(hub, fresh, tmp_path):
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.async_api", reason="optional browser dependency")
    hub.gateway.rate_limits = False
    for slug, name in PROJECTS:
        fresh.owner.board_add(slug=slug, name=name, wing="shared")
    bots = store.open_bots(hub.gateway, fresh.workspace)
    boards = [("work", "work"), *PROJECTS]
    bots.ensure_defaults(fresh.user, boards)
    atlas = bots.create(fresh.user, "Atlas")["id"]
    bots.move_project(fresh.user, "halo-avatar", atlas)
    loose_bot = bots.project_links(fresh.user)["loose"]
    bots.release_project(fresh.user, loose_bot, "loose")
    output = Path(os.environ.get("CAPY_TREE_SHOTS", str(tmp_path / "tree-shots")))
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
                await desktop(browser)
                for width, name in ((375, "phone-375"), (320, "phone-320")):
                    await phone(browser, width, name)
            finally:
                await browser.close()

    async def desktop(browser):
        context, page, url = await open_app(browser, hub, fresh, width=1440)
        await page.set_viewport_size({"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(url + "#/bots")
        await page.wait_for_selector(item(f"bot:{atlas}"))
        host = page.locator("[data-deck-bots]")
        assert await host.get_attribute("role") == "tree"
        # Nested and indented: the project sits under its bot.
        atlas_box = await page.locator(item(f"bot:{atlas}")).bounding_box()
        child = page.locator(item("project:halo-avatar"))
        await child.wait_for(state="visible")
        child_box = await child.bounding_box()
        assert child_box["x"] > atlas_box["x"] and child_box["y"] > atlas_box["y"]
        assert await page.locator(item("project:blue-eye")).count() == 1  # under its own bot
        # Unassigned holds the project that has no bot; General has none and says so.
        await page.locator(item("unassigned")).wait_for(state="visible")
        assert await page.locator(item("project:loose")).is_visible()
        assert "No projects yet" in await host.inner_text()
        await page.screenshot(path=str(output / "desktop-tree.png"))
        # Fold Atlas with its arrow; the choice survives a reload on this device.
        await page.locator(item(f"bot:{atlas}") + " .deck-node-chevron").click()
        await child.wait_for(state="hidden")
        assert await page.locator(item(f"bot:{atlas}")).get_attribute("aria-expanded") == "false"
        await page.reload()
        await page.wait_for_selector(item(f"bot:{atlas}"))
        assert await page.locator(item(f"bot:{atlas}")).get_attribute("aria-expanded") == "false"
        # Opening the project opens its bot and marks the project as the current page.
        await page.goto(url + "#/board/halo-avatar")
        await child.wait_for(state="visible")
        assert await child.get_attribute("aria-current") == "page"
        assert await page.locator(item(f"bot:{atlas}")).get_attribute("aria-expanded") == "true"
        await page.screenshot(path=str(output / "desktop-current-project.png"))
        # Keyboard: left steps out to the bot, left folds, right opens, down enters, m opens the move chooser.
        await child.focus()
        await page.keyboard.press("ArrowLeft")
        assert await page.evaluate("document.activeElement.dataset.deckItem") == f"bot:{atlas}"
        await page.keyboard.press("ArrowLeft")
        assert await page.locator(item(f"bot:{atlas}")).get_attribute("aria-expanded") == "false"
        await page.keyboard.press("ArrowRight")
        await page.keyboard.press("ArrowRight")
        assert (
            await page.evaluate("document.activeElement.dataset.deckItem") == "project:halo-avatar"
        )
        assert await page.evaluate(
            "getComputedStyle(document.activeElement).outlineStyle !== 'none'"
        )
        await page.keyboard.press("m")
        select = page.locator("select[data-deck-move-select='halo-avatar']")
        await select.wait_for(state="visible")
        await page.screenshot(path=str(output / "desktop-move.png"))
        await select.select_option(label="Unassigned")
        await page.wait_for_function(
            "() => document.querySelector('#deck-tree-rail-unassigned [data-deck-item=\"project:halo-avatar\"]')"
        )
        read = await page.request.get(url.split("/app/")[0] + "/api/bots")
        listed = {b["id"]: b["projects"] for b in (await read.json())["bots"]}
        assert listed[atlas] == [] and "halo-avatar" not in sum(listed.values(), [])
        assert (
            await page.evaluate("document.activeElement.dataset.deckItem") == "project:halo-avatar"
        )
        # Move it back through the pointer.
        await page.locator(item("project:halo-avatar")).hover()
        await page.locator("[data-deck-move='halo-avatar']").click()
        await page.locator("select[data-deck-move-select]").select_option(value=atlas)
        await page.wait_for_function(
            f"() => document.querySelector('[data-deck-item=\"bot:{atlas}\"]')"
            f".closest('.deck-bot-tree').querySelector('[data-deck-item=\"project:halo-avatar\"]')"
        )
        assert errors == []
        await context.close()

    async def phone(browser, width, name):
        context, page, url = await open_app(browser, hub, fresh, width=width)
        await page.set_viewport_size({"width": width, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(url + "#/bots")
        host = "[data-deck-bots-page]"
        await page.wait_for_selector(item(f"bot:{atlas}", host), state="visible")
        assert await page.locator(".rail").is_hidden()
        assert await page.evaluate("document.documentElement.scrollWidth") == width
        assert await page.locator(item("project:halo-avatar", host)).is_visible()
        assert await page.locator(item("unassigned", host)).is_visible()
        # Touch targets are at least 44 px tall.
        box = await page.locator(item("project:halo-avatar", host)).bounding_box()
        assert box["height"] >= 44 and box["x"] + box["width"] <= width
        await page.locator(item(f"bot:{atlas}", host) + " .deck-node-chevron").click()
        await page.locator(item("project:halo-avatar", host)).wait_for(state="hidden")
        await page.locator(item(f"bot:{atlas}", host) + " .deck-node-chevron").click()
        await page.locator(item("project:halo-avatar", host)).wait_for(state="visible")
        await page.locator(item("project:halo-avatar", host)).scroll_into_view_if_needed()
        await page.screenshot(path=str(output / f"{name}-tree.png"))
        assert await page.evaluate("document.documentElement.scrollWidth") == width
        assert errors == []
        await context.close()

    asyncio.run(check())
