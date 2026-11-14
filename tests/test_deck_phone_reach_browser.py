"""Phone Deck: every destination is reachable and a card menu never leaves the viewport."""

import asyncio
import json
import os
import shutil

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as authority_fresh
from tests.test_deck_phone_navigation_browser import open_app

DESTINATIONS = {
    "Board": "#/board",
    "Memory": "#/memory",
    "Needs me": "#/needs",
    "Projects": "#/boards",
    "Console": "#/console",
    "Rules": "#/rules",
}
# The page forbids string evaluation, so the predicate is spelled out in each script.
INSIDE = """sel => {
  const e = document.querySelector(sel);
  const r = e?.getBoundingClientRect();
  return !!r && r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 &&
    r.right <= innerWidth && r.bottom <= innerHeight;
}"""


async def inside(page, selector):
    """Poll one live node: the shell may redraw its chrome once while a screen settles."""
    await page.wait_for_function(INSIDE, arg=selector)


@pytest.fixture
def fresh(hub):
    return authority_fresh.__wrapped__(hub)


async def launch(playwright):
    try:
        return await playwright.chromium.launch(executable_path=shutil.which("google-chrome"))
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc) or "None" in str(exc):
            pytest.skip("optional browser executable unavailable")
        raise


def browser_gate():
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    return pytest.importorskip("playwright.async_api", reason="optional browser dependency")


def test_phone_more_sheet_reaches_every_destination(hub, fresh):
    playwright = browser_gate()
    hub.gateway.rate_limits = False
    fresh.owner.task_add(board="work", title="Check the release checklist", labels=["plan"])

    async def check():
        async with playwright.async_playwright() as manager:
            browser = await launch(manager)
            context, page, url = await open_app(browser, hub, fresh)
            try:
                for start in ["#/today", "#/bots", "#/settings", "#/memory"]:
                    await page.goto(url + start)
                    await page.wait_for_selector('[data-deck="tabs"] .deck-tab-more')
                    # The four sections stay in the bar next to the More tab, all inside the screen.
                    # Measure the live nodes in one browser task: the shell may re-render the bar once.
                    await page.wait_for_function(
                        """() => {
                          const tabs = [...document.querySelectorAll(
                            '[data-deck="tabs"] :is(a, .deck-tab-more)')];
                          return tabs.length === 5 && tabs.every(e => {
                            const r = e.getBoundingClientRect();
                            return r.width > 0 && r.left >= 0 && r.right <= innerWidth &&
                              r.top >= 0 && r.bottom <= innerHeight;
                          });
                        }"""
                    )
                    for label, href in DESTINATIONS.items():
                        # Two taps from any screen: More, then the destination.
                        await page.locator(".deck-tab-more").click()
                        sheet = page.locator("#deck-more")
                        await sheet.wait_for(state="visible")
                        entry = sheet.locator(f'a[href="{href}"]')
                        assert (await entry.text_content()).strip().startswith(label)
                        await inside(page, f'#deck-more a[href="{href}"]')
                        await inside(page, "#deck-more .deck-more-panel")
                        await entry.click()
                        await page.wait_for_function("h => location.hash.startsWith(h)", arg=href)
                        assert await sheet.is_hidden(), "a tap on an entry closes the sheet"
                    # Escape closes the sheet without navigating.
                    await page.locator(".deck-tab-more").click()
                    await page.keyboard.press("Escape")
                    assert await page.locator("#deck-more").is_hidden()
                    assert await page.evaluate("document.documentElement.scrollWidth") == 390
            finally:
                await context.close()
                await browser.close()

    asyncio.run(check())


async def offer_turn_down(route):
    """Let every waiting card offer Turn down, so the card menu exists on the phone."""
    response = await route.fetch()
    body = await response.json()
    for bucket in body.values():
        for card in bucket if isinstance(bucket, list) else []:
            if isinstance(card, dict):
                card["actions"] = sorted({*card.get("actions", []), "reject"})
    await route.fulfill(response=response, body=json.dumps(body))


def test_phone_today_card_actions_stay_inside_the_viewport(hub, fresh):
    playwright = browser_gate()
    hub.gateway.rate_limits = False
    for title in ["Ship consent for the phone feed", "Agree the action list the phone reads"]:
        fresh.owner.task_add(
            board="work", title=title, body="Ready for your decision.", labels=["plan"]
        )

    async def check():
        async with playwright.async_playwright() as manager:
            browser = await launch(manager)
            context, page, url = await open_app(browser, hub, fresh)
            try:
                await page.route("**/api/needs-me*", offer_turn_down)
                await page.goto(url + "#/today")
                await page.wait_for_selector(".bt-card")
                await page.wait_for_load_state("networkidle")
                numbers = await page.eval_on_selector_all(
                    ".bt-card", "es => es.map(e => e.dataset.btCard)"
                )
                assert len(numbers) >= 2
                for number in numbers:
                    card = f'[data-bt-card="{number}"]'
                    await page.evaluate(
                        "sel => document.querySelector(sel)?.scrollIntoView({block: 'center'})",
                        card,
                    )
                    reply = f"{card} .bt-desktop-reply"
                    await inside(page, reply)  # Reply is an inline button, fully on screen
                    box = await page.locator(reply).bounding_box()
                    assert box["height"] >= 44
                    assert (await page.locator(reply).text_content()).strip() == "Reply"
                    more = f"{card} .bt-more summary"
                    assert await page.locator(more).is_visible(), "the menu offers Turn down"
                    await page.locator(more).click()
                    await inside(page, f'{card} .bt-more [role="menu"]')
                    await inside(page, f'{card} .bt-more [data-bt-action="reject"]')
                    assert await page.locator(
                        f'{card} .bt-more [data-bt-action="reply"]'
                    ).is_hidden()
                    await page.locator(more).click()
                # Reply works from the inline button: a dialog for a plain question, else the card page.
                await page.locator(f'[data-bt-card="{numbers[0]}"] .bt-desktop-reply').click()
                await page.wait_for_function(
                    "document.querySelector('dialog[open]') || location.hash.startsWith('#/card/')"
                )
            finally:
                await context.close()
                await browser.close()

    asyncio.run(check())
