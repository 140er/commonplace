"""Optional Waiting on you checks against a real local hub and its card actions."""

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
                await page.wait_for_selector(".bt-card")
                await expect(page.locator(".bt-sub")).to_have_text("3 items waiting")
                assert await page.evaluate("location.hash") == "#/"
                # The T3 look has no background art: the stage canvas stays hidden.
                assert await page.locator(".deck-stage").is_hidden()
                assert await page.locator(".bh-composer").count() == 0
                await page.evaluate("document.fonts.ready")
                if width == 390:
                    assert (await page.locator(".bt-card").first.bounding_box())["x"] == 14
                    await expect(page.locator("#phone-bar .m-top > b")).to_have_text(
                        "Waiting on you"
                    )
                    assert await page.locator(".bt-bar h1").is_hidden()
                else:
                    await expect(page.locator(".bt-bar h1")).to_have_text("Waiting on you")
                await capture("waiting")
                # #/today and #/needs open the same screen as Home.
                for alias in ["#/today", "#/needs"]:
                    await page.goto(url + alias)
                    await page.wait_for_selector(".bt-card")
                    assert await page.locator(".bt-card").count() == 3
                await page.goto(url + "#/")
                await page.wait_for_selector(".bt-card")
                assert await page.locator(".bt-card").count() == 3
                if width == 390:
                    assert await page.locator(".bt-fact").count() == 0
                    await capture("phone")
                    # Measure once the first card's buttons are laid out (a slow machine can still be rendering).
                    await expect(
                        page.locator(".bt-card")
                        .first.locator(".bt-approve, .bt-secondary, .bt-quiet")
                        .first
                    ).to_be_visible()
                    sizes = (
                        await page.locator(".bt-card")
                        .first.locator(".bt-approve, .bt-secondary, .bt-quiet")
                        .evaluate_all("es=>es.map(e=>e.getBoundingClientRect().height)")
                    )
                    assert sizes and all(size >= 44 for size in sizes)
                assert "Awaiting plan review" not in await page.locator(".bt-col").text_content()
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
                await expect(page.locator(".bt-sub")).to_have_text("2 items waiting")
                for selector in [
                    '[data-deck="bar"] [data-nav="home"] .count',
                    '[data-deck="tabs"] [data-nav="home"] .count',
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
                # The next primary must keep its rest appearance (the primary blue), even under hover.
                primary = page.locator(".bt-approve").first
                await expect(primary).to_be_enabled()
                primary_style = await primary.evaluate("""button => {
                  const style = getComputedStyle(button);
                  return { background: style.backgroundColor, opacity: style.opacity, filter: style.filter };
                }""")
                await capture("approved")
                assert primary_style == {
                    "background": "rgb(155, 187, 224)",
                    "opacity": "1",
                    "filter": "none",
                }, f"next Approve must rest in the primary colour: {primary_style}"
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


def test_today_cards_are_short_and_every_count_agrees(hub, fresh):
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.async_api", reason="optional browser dependency")
    expect = playwright.expect
    hub.gateway.rate_limits = False
    long_body = "A long request that explains every step of the release journey in detail. " * 12
    for title in ("First plan", "Second plan"):
        fresh.owner.task_add(board="work", title=title, body=long_body, labels=["plan"])

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
            context, page, url = await open_app(browser, hub, fresh, width=1440)
            try:
                await page.goto(url + "#/")
                await page.wait_for_selector(".bt-card")
                first = page.locator(".bt-card").first
                # The main answer and Turn down are on the card before anything is opened.
                await expect(first.locator(".bt-primary")).to_be_visible()
                await expect(first.locator(".bt-secondary")).to_be_visible()
                preview = await first.locator(".bt-preview").text_content()
                assert len(preview) <= 141
                await first.locator(".bt-full summary").click()
                await expect(first.locator(".bt-full-text")).to_have_text(long_body.strip())
                await expect(page.locator(".bt-sub")).to_have_text("2 items waiting")
                await expect(
                    page.locator('[data-deck="bar"] [data-nav="home"] .count')
                ).to_have_text("2 waiting on you")
                await page.goto(url + "#/board/work")
                await page.wait_for_selector(".workbench-board")
                await expect(page.locator("#col-todo .col-h .n")).to_have_text("2")
            finally:
                await context.close()
                await browser.close()

    asyncio.run(check())
