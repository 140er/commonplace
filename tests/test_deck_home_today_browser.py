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
                if width == 390:
                    assert (await page.locator(".bh-composer").bounding_box())["x"] == 16
                    assert (await page.locator(".bh-bot").bounding_box())["x"] == 16
                    await expect(page.locator("#phone-bar .m-top > b")).to_have_text("Home")
                await expect(page.locator('.bh-toolbar [aria-label="Tools"]')).to_be_visible()
                send_style = await page.locator(".bh-send").evaluate(
                    "e=>({bg:getComputedStyle(e).backgroundColor,opacity:getComputedStyle(e).opacity})"
                )
                assert send_style == {"bg": "rgb(157, 92, 255)", "opacity": "0.4"}

                async def send_measurement():
                    return await page.locator(".bh-send").evaluate("""button => {
                      const box = button.getBoundingClientRect(), arrow = button.querySelector('path').getBoundingClientRect();
                      const style = getComputedStyle(button), ink = getComputedStyle(button.querySelector('svg'));
                      return { dx: arrow.x + arrow.width / 2 - box.x - box.width / 2,
                        dy: arrow.y + arrow.height / 2 - box.y - box.height / 2,
                        background: style.backgroundColor, opacity: style.opacity, filter: style.filter, ink: ink.color };
                    }""")

                send_measurements = [await send_measurement()]
                await capture("hero-empty")
                await page.locator(".bh-composer textarea").fill("Check this release")
                send_measurements.append(await send_measurement())
                await page.locator(".bh-send").hover()
                send_measurements.append(await send_measurement())
                await page.mouse.move(0, 0)
                await capture("hero")
                await page.locator(".bh-composer textarea").fill("/")
                await page.wait_for_selector(".workbench-palette[open]")
                assert (
                    await page.locator(".workbench-palette .gh").first.text_content() == "Actions"
                )
                if width == 390:
                    assert await page.locator(".workbench-palette .pfoot").is_hidden()
                    await expect(page.locator(".palette-close")).to_be_visible()
                await capture("commands")
                if width == 390:
                    await page.keyboard.press("Shift+Tab")
                    await expect(page.locator(".palette-close")).to_be_focused()
                    await page.locator(".palette-close").click()
                else:
                    await page.keyboard.press("Escape")
                await page.locator(".bh-composer textarea").fill("@")
                await page.locator(".bh-mentions button").first.click()
                assert "@" in await page.locator(".bh-composer textarea").input_value()
                await page.locator(".bh-composer textarea").fill("Check this release")
                original_bot = await page.locator('select[name="bot"]').input_value()
                await page.locator(".bh-send").click()
                await expect(page.locator(".bh-run-state")).to_have_text("Could not complete")
                await expect(page.locator(".bh-run-reason")).to_contain_text(
                    "No chat model is set up for this bot yet."
                )
                await expect(page.locator(".bh-retry")).to_have_text("Try again")
                await expect(page.locator(".bh-run-reason a")).to_have_text("Choose one")
                assert (
                    await page.locator(".bh-run-state").evaluate("e=>getComputedStyle(e).color")
                    == "rgb(251, 113, 133)"
                )
                assert (
                    await page.locator(".bh-run").evaluate("e=>getComputedStyle(e).backgroundColor")
                    == "rgba(20, 19, 25, 0.94)"
                )
                await capture("run")
                # A different current selector must never reroute an earlier run's retry.
                bot_values = await page.locator('select[name="bot"] option').evaluate_all(
                    "es=>es.map(e=>e.value)"
                )
                await page.locator('select[name="bot"]').select_option(
                    next(value for value in bot_values if value != original_bot)
                )
                async with page.expect_response(
                    lambda response: (
                        response.request.method == "POST"
                        and response.url.endswith(f"/api/bots/{original_bot}/messages")
                    )
                ) as retry_info:
                    await page.locator(".bh-retry").click()
                retry = await retry_info.value
                assert retry.status == 200
                assert retry.request.post_data_json["text"] == "Check this release"
                await expect(page.locator(".bh-run-state")).to_have_text("Could not complete")
                await page.goto(url + "#/today")
                await page.wait_for_selector(".bt-card")
                assert await page.locator(".bt-card").count() == 3
                assert await page.locator(".deck-stage").get_attribute("data-dimmed") == "true"
                if width == 390:
                    assert await page.locator(".bt-title h1").is_hidden()
                    assert await page.locator(".bt-fact").count() == 0
                    # Reply is an inline button on a phone; the menu only holds what has no button.
                    await page.locator(".bt-desktop-reply").first.wait_for(state="visible")
                    await capture("more")
                    sizes = (
                        await page.locator(".bt-card")
                        .first.locator(".bt-approve, .bt-hold, .bt-desktop-reply")
                        .evaluate_all("es=>es.map(e=>e.getBoundingClientRect().height)")
                    )
                    assert all(size >= 44 for size in sizes)
                assert "Awaiting plan review" not in await page.locator(".bt-page").text_content()
                heights = await page.locator(".bt-bots li a").evaluate_all(
                    "es=>es.map(e=>e.getBoundingClientRect().height)"
                )
                assert heights and all(height == 56 for height in heights)
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
                animation = await page.locator(".bt-new-activity").evaluate(
                    "e=>getComputedStyle(e).animationDuration"
                )
                assert animation == "4s"
                assert (
                    "work asked 3 things"
                    in await page.locator('[data-bt="activity"]').text_content()
                )
                # Leave the pointer where the removed card's Approve button was.
                # The next primary must retain its white rest appearance, even under hover.
                primary = page.locator(".bt-approve").first
                await expect(primary).to_be_enabled()
                primary_style = await primary.evaluate("""button => {
                  const style = getComputedStyle(button);
                  return { background: style.backgroundColor, opacity: style.opacity, filter: style.filter };
                }""")
                await capture("approved")
                issues = []
                for index, measurement in enumerate(send_measurements):
                    if abs(measurement["dx"]) > 1 or abs(measurement["dy"]) > 1:
                        issues.append(f"send state {index}: arrow centre {measurement}")
                    expected_opacity = "0.4" if index == 0 else "1"
                    expected = {
                        "background": "rgb(157, 92, 255)",
                        "opacity": expected_opacity,
                        "filter": "none",
                        "ink": "rgb(255, 255, 255)",
                    }
                    if {key: measurement[key] for key in expected} != expected:
                        issues.append(f"send state {index}: colour {measurement}")
                if primary_style != {
                    "background": "rgb(237, 237, 242)",
                    "opacity": "1",
                    "filter": "none",
                }:
                    issues.append(f"next Approve must rest white: {primary_style}")
                assert not issues, issues
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
