"""Optional real-hub regression checks for the Home and Today design follow-up."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_deck_home_today_browser import fresh as fresh, seed_today
from tests.test_deck_phone_navigation_browser import open_app


@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "phone"])
def test_home_today_design_nits(hub, fresh, width):
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.async_api", reason="optional browser dependency")
    expect = playwright.expect
    hub.gateway.rate_limits = False
    cards = seed_today(fresh)

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
            context, page, url = await open_app(browser, hub, fresh, width=width)
            if width == 1440:
                await page.set_viewport_size({"width": 1440, "height": 900})
            shots = os.environ.get("CAPY_B_NIT_SHOTS")
            issues, errors = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def capture(name):
                if shots:
                    Path(shots).mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(Path(shots) / f"{name}-{width}.png"))

            try:
                await page.goto(url + "#/")
                await page.wait_for_selector(".bh-form textarea:enabled")
                await page.evaluate("document.fonts.ready")
                tools = page.get_by_role("button", name="Tools", exact=True)
                if await tools.locator("svg polygon").count() != 1:
                    issues.append("Tools needs its distinct spark glyph")
                await capture("hero")
                await tools.click()
                await page.wait_for_selector(".workbench-palette[open]")
                selected = page.locator('.workbench-palette [aria-selected="true"]')
                style = await selected.evaluate("""e => {
                  const s = getComputedStyle(e);
                  return { background:s.backgroundColor, shadow:s.boxShadow, outline:s.outlineStyle, border:s.borderWidth };
                }""")
                if style != {
                    "background": "rgb(27, 26, 33)",
                    "shadow": "none",
                    "outline": "none",
                    "border": "0px",
                }:
                    issues.append(f"Selected palette row needs only its neutral fill: {style}")
                hint = page.locator(".workbench-palette .palette-hint")
                if not await hint.count():
                    issues.append("Palette needs its plain move/run hint")
                else:
                    await expect(hint).to_have_text("↑↓ move · ↵ run")
                    assert await hint.locator("kbd").count() == 0
                    await expect(hint).to_have_css("font-size", "12px")
                    await expect(hint).to_have_css("color", "rgb(133, 131, 154)")
                if width == 390:
                    await expect(page.locator(".workbench-palette .pfoot")).to_be_hidden()
                await capture("commands")
                await page.keyboard.press("Escape")
                await page.goto(url + "#/today")
                await expect(page.locator(".bt-card")).to_have_count(3)
                await expect(page.locator(".bt-activity")).to_contain_text("work")
                group = page.locator(".bt-activity details")
                if await group.count() != 1:
                    issues.append("Same-bot request burst needs one expandable Activity group")
                else:
                    await expect(group.locator("summary")).to_have_text("work asked 3 things")
                    await expect(group).not_to_have_attribute("open", "")
                    await capture("today")
                    await group.locator("summary").focus()
                    await page.keyboard.press("Enter")
                    for card in cards:
                        await expect(group.get_by_text(card["title"], exact=True)).to_be_visible()
                    await capture("activity-expanded")
                    await page.locator(".bt-approve").first.click()
                    await expect(page.locator(".bt-card")).to_have_count(2)
                    await expect(group).to_have_attribute("open", "")
                    await expect(page.locator('[data-bt="activity"]')).to_contain_text(
                        "You approved"
                    )
                    await group.locator("summary").click()
                    await expect(group).not_to_have_attribute("open", "")
                assert await page.evaluate("document.documentElement.scrollWidth === innerWidth")
                assert not errors, errors
                assert not issues, issues
            finally:
                await context.close()
                await browser.close()

    asyncio.run(check())
