"""Signed-in file journey with real import writes and a lost-response retry."""

import asyncio
import os
from pathlib import Path

import pytest

from commonplace import sessions
from tests.test_commonplace_import import surface as surface
from tests.test_commonplace_import_files import CONFIRM, file_body


@pytest.mark.parametrize("viewport", [(1280, 800), (390, 844)])
def test_file_journey_cancel_progress_report_and_safe_retry(surface, viewport):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch()
            origin = f"http://127.0.0.1:{surface.port}"
            context = await browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=2
            )
            await context.add_cookies(
                [{"name": sessions.COOKIE, "value": surface.cookie, "url": origin}]
            )
            external = []

            async def local_only(route):
                if route.request.url.startswith(origin + "/"):
                    await route.continue_()
                else:
                    external.append(route.request.url)
                    await route.abort()

            await context.route("**/*", local_only)
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def shot(name):
                output = os.environ.get("IMPORT_SCREENSHOT_DIR")
                if output:
                    directory = Path(output)
                    directory.mkdir(parents=True, exist_ok=True)
                    size = "desktop" if viewport[0] == 1280 else "phone"
                    await page.screenshot(path=str(directory / f"memory-import-{name}-{size}.png"))
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert (
                    await page.locator("#memory-import-title").evaluate(
                        "el => getComputedStyle(el).fontSize"
                    )
                    == "24px"
                )

            async def choose():
                await page.click('[data-memory-action="import"]')
                await page.select_option('[data-import-choose] [name="format"]', "2")
                body = file_body()
                await page.set_input_files(
                    '[name="file"]',
                    {
                        "name": "conversation.json",
                        "mimeType": "application/json",
                        "buffer": body["transcript"].encode("utf-8"),
                    },
                )
                await page.click('[data-import-choose] button[type="submit"]')
                await page.wait_for_selector('[data-import-action="confirm"]', timeout=10000)

            try:
                await page.goto(origin + "/app/?ui=deck#/memory")
                await page.wait_for_selector('[data-memory-action="import"]', timeout=15000)
                await page.click('[data-memory-action="import"]')
                await shot("choose")
                await page.click('[data-import-action="close"]')
                await choose()
                assert (
                    "1 file · 1 message"
                    in await page.locator(".memory-import-summary").inner_text()
                )
                await shot("preview")
                await page.click('[data-import-action="close"]')
                assert not surface.gateway.handoff.has_threads(surface.workspace)
                await choose()
                release = asyncio.Event()

                async def lost_response(route):
                    await release.wait()
                    response = await route.fetch()
                    assert response.status == 200
                    # The write succeeded; the browser gets an interrupted response.
                    await route.fulfill(
                        status=503, json={"error": "The connection was interrupted. Retry safely."}
                    )

                await page.route("**" + CONFIRM, lost_response, times=1)
                await page.click('[data-import-action="confirm"]')
                await page.wait_for_selector(".memory-import progress")
                await shot("progress")
                release.set()
                await page.wait_for_selector('[data-import-action="retry"]', timeout=10000)
                await shot("error")
                await page.click('[data-import-action="retry"]')
                await page.wait_for_selector('[data-import-action="close"]', timeout=10000)
                assert (
                    "Already imported. No new copy was added."
                    in await page.locator(".memory-import").inner_text()
                )
                await shot("report")
                await page.click('[data-import-action="close"]')
                assert await page.locator('[data-memory-action="import"]').evaluate(
                    "el => el === document.activeElement"
                )
                assert not errors, errors
                assert not external, external
            finally:
                await browser.close()

    asyncio.run(run())


def test_member_can_choose_and_import_without_private_scope(surface):
    from playwright.async_api import async_playwright

    user = surface.db.create_user("member@example.test")
    surface.db.add_member(surface.workspace, user, "member", actor=surface.user)
    cookie = sessions.start_session(surface.db, user, surface.workspace)

    async def run():
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch()
            try:
                origin = f"http://127.0.0.1:{surface.port}"
                context = await browser.new_context(
                    viewport={"width": 390, "height": 844}, device_scale_factor=2
                )
                await context.add_cookies(
                    [{"name": sessions.COOKIE, "value": cookie, "url": origin}]
                )
                page = await context.new_page()
                await page.goto(origin + "/app/?ui=deck#/memory")
                await page.wait_for_selector('[data-memory-action="import"]', timeout=15000)
                await page.click('[data-memory-action="import"]')
                assert await page.locator('[name="scope"] option[value="private"]').count() == 0
                await page.select_option('[name="format"]', "2")
                body = file_body()
                await page.set_input_files(
                    '[name="file"]',
                    {
                        "name": "conversation.json",
                        "mimeType": "application/json",
                        "buffer": body["transcript"].encode(),
                    },
                )
                await page.click('[data-import-choose] button[type="submit"]')
                await page.wait_for_selector('[data-import-action="confirm"]')
                await page.click('[data-import-action="confirm"]')
                await page.wait_for_selector('[data-import-action="close"]')
                assert "Import complete." in await page.locator(".memory-import").inner_text()
            finally:
                await browser.close()

    asyncio.run(run())
