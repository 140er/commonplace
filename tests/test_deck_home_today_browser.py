"""Optional Home/Today checks against a real local hub and its card actions."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as authority_fresh
from tests.test_deck_phone_navigation_browser import open_app


@pytest.fixture
def fresh(hub):
    return authority_fresh.__wrapped__(hub)


def seed_today(person):
    cards = []
    for title, body in [
        (
            "Ship consent for the phone feed",
            "Ask before reading activity. The change is ready for your decision.",
        ),
        (
            "Agree the action list the phone reads",
            "Approve and Hold should use the same action list on every device.",
        ),
        ("Check the release checklist", "Review the change and its verification before shipping."),
    ]:
        cards.append(person.owner.task_add(board="work", title=title, body=body, labels=["plan"]))
    return cards


@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "phone"])
def test_home_today_live_actions(hub, fresh, width):
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
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            shots = os.environ.get("CAPY_B_SHOTS")
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)

            async def capture(name):
                if shots:
                    await page.screenshot(path=str(Path(shots) / f"{name}-{width}.png"))

            try:
                await page.goto(url + "#/")
                await page.wait_for_selector(".bh-form textarea:enabled")
                await expect(page.locator(".bh-hint")).to_contain_text("3 waiting")
                assert await page.evaluate("location.hash") == "#/"
                assert await page.locator(".deck-stage").get_attribute("data-dimmed") == "false"
                assert await page.locator(".bh-coworker").is_hidden()
                assert await page.locator(".bh-host").is_hidden()
                await page.evaluate("document.fonts.ready")
                await capture("hero")
                await page.locator(".bh-composer textarea").fill("/")
                await page.wait_for_selector(".workbench-palette[open]")
                await capture("commands")
                await page.keyboard.press("Escape")
                await page.locator(".bh-composer textarea").fill("@")
                await page.locator(".bh-mentions button").first.click()
                assert "@" in await page.locator(".bh-composer textarea").input_value()
                await page.locator(".bh-composer textarea").fill("Check this release")
                await page.locator(".bh-send").click()
                await page.wait_for_selector(".bh-run")
                await capture("run")
                await page.goto(url + "#/today")
                await page.wait_for_selector(".bt-card")
                assert await page.locator(".bt-card").count() == 3
                assert await page.locator(".deck-stage").get_attribute("data-dimmed") == "true"
                if width == 390:
                    assert await page.locator(".bt-title h1").is_hidden()
                    assert await page.locator(".bt-fact").first.is_hidden()
                    await page.locator(".bt-more summary").first.click()
                    await page.locator('[role="menuitem"][data-bt-action="reply"]').first.wait_for(
                        state="visible"
                    )
                    await capture("more")
                    await page.locator(".bt-more summary").first.click()
                    sizes = (
                        await page.locator(".bt-card")
                        .first.locator(".bt-approve, .bt-hold, summary")
                        .evaluate_all("es=>es.map(e=>e.getBoundingClientRect().height)")
                    )
                    assert all(size >= 44 for size in sizes)
                await capture("today")
                response_wait = page.expect_response(
                    lambda response: (
                        response.request.method == "POST"
                        and response.url.endswith(f"/api/cards/{cards[0]['number']}/approve")
                    )
                )
                async with response_wait as info:
                    await page.locator(".bt-approve").first.click()
                response = await info.value
                assert response.status == 200
                assert response.request.headers.get("idempotency-key")
                assert response.request.post_data_json == {
                    "version": cards[0]["version"],
                    "approve": True,
                }
                await expect(page.locator(".bt-card")).to_have_count(2)
                await expect(page.locator(".bt-title p")).to_contain_text("2 waiting")
                for selector in [
                    '[data-deck="bar"] [data-nav="today"] .count',
                    '[data-deck="tabs"] [data-nav="today"] .count',
                ]:
                    assert (await page.locator(selector).text_content()).startswith("2")
                assert "You approved" in await page.locator('[data-bt="activity"]').text_content()
                await capture("approved")
                if width == 1440:
                    await page.keyboard.press("j")
                    assert await page.evaluate("document.activeElement.matches('[data-bt-card]')")
                    await page.keyboard.press("h")
                    assert await page.locator(".bt-held").count() == 1
                assert await page.evaluate("document.documentElement.scrollWidth === innerWidth")
                assert not errors, errors
            finally:
                await context.close()
                await browser.close()

    asyncio.run(check())
