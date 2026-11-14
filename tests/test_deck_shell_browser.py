"""Optional browser evidence using the real shell, gateway and readable directory."""

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from commonplace.chat.coworkers.store import open_store
from tests._commonplace_helpers import (
    REVIEW_CONTEXT,
    REVIEW_OPTIONS,
    hub_fixture,  # noqa: F401
)
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_deck_phone_navigation_browser import open_app


def test_shell_desktop_and_phone_chrome(hub, fresh, tmp_path):
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.async_api", reason="optional browser dependency")
    hub.gateway.rate_limits = False
    worker = fresh.connect("release")
    worker.work.ask(
        "Approve the release checklist?",
        board="work",
        context=REVIEW_CONTEXT,
        options=REVIEW_OPTIONS,
    )
    store = open_store(hub.gateway, fresh.workspace)
    bot = store.create_bot(fresh.user, "Research")
    conversation = store.create_conversation(
        fresh.user, "dm", title="Plan the next release", bot_ids=[bot["id"]]
    )
    store.post_message(fresh.user, conversation["id"], "Plan the next release", "shell-fixture")
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
                    await page.wait_for_selector(".deck-bot-tree", state="attached")
                    await page.wait_for_function(
                        "getComputedStyle(document.querySelector('[data-deck=bar]')).backgroundColor === 'rgb(17, 16, 21)'"
                        if width >= 700
                        else "document.querySelector('[data-deck=top]')?.getBoundingClientRect().height === 52"
                    )
                    await page.wait_for_function(
                        "() => document.querySelectorAll('.deck-tree-row').length >= 1"
                    )
                    await page.evaluate("document.fonts.ready")
                    assert await page.evaluate("document.documentElement.scrollWidth") == width
                    if width >= 700:
                        measured = await page.wait_for_function("""() => {
                          const rail=document.querySelector('[data-deck=bar]'), row=document.querySelector('.deck-tree-row');
                          return rail?.getBoundingClientRect().width===252 && getComputedStyle(rail).backgroundColor==='rgb(17, 16, 21)' && row?.getBoundingClientRect().height===26;
                        }""")
                        assert await measured.json_value()
                        stage = page.locator(".deck-stage")
                        await stage.wait_for(state="attached")
                        metrics = await stage.evaluate("""e => {
                          const box=e.getBoundingClientRect(), parent=e.parentElement.getBoundingClientRect(), style=getComputedStyle(e);
                          return {inset:box.left-parent.left,radius:style.borderRadius,border:style.borderTopWidth,dim:e.dataset.dimmed,canvas:!!e.querySelector('canvas')};
                        }""")
                        assert metrics == {
                            "inset": 8,
                            "radius": "12px",
                            "border": "1px",
                            "dim": "true",
                            "canvas": True,
                        }
                        assert await page.locator('[data-deck-folder="Bots"]').count() == 0
                        me = await page.request.get(url.split("/app/")[0] + "/api/me")
                        assert (
                            await page.locator(".deck-owner small").text_content()
                            == "Owner · " + (await me.json())["workspace_name"]
                        )
                        await page.locator('[data-deck=bar] a[href="#/board"]').click()
                        await page.locator(".deck-board .board").wait_for(state="visible")
                        assert await page.evaluate("location.hash") == "#/board/work"
                        await page.locator(".deck-rail-heading.ws").click()
                        await page.locator(".workspace-switch").wait_for(state="visible")
                        assert await page.get_by_role(
                            "button", name="Create workspace", exact=True
                        ).is_visible()
                        await page.keyboard.press("Escape")
                        await page.locator(".deck-brand-row .deck-brand").click()
                        await page.locator(".bh-form textarea:enabled").wait_for(state="visible")
                        assert await page.evaluate("location.hash") == "#/"
                        assert (
                            await page.locator(".deck-stage").get_attribute("data-dimmed")
                            == "false"
                        )
                        await page.locator('.deck-links [data-nav="today"]').click()
                        await page.locator(".bt-card").first.wait_for(state="visible")
                        assert await page.evaluate("location.hash") == "#/today"
                        await page.locator(".deck-new-bot").click()
                        await page.wait_for_selector('dialog[open], [role="dialog"]')
                        await page.keyboard.press("Escape")
                        await page.locator('.deck-links [data-nav="bots"]').click()
                    else:
                        top = page.locator('[data-deck="top"]')
                        await page.wait_for_function(
                            "document.querySelector('[data-deck=top]')?.getBoundingClientRect().height === 52"
                        )
                        assert await top.locator("b").text_content() == "Today"
                        await page.locator('[data-deck-waiting="true"]').wait_for(state="visible")
                        tabs = page.locator('[data-deck="tabs"]')
                        assert await tabs.locator("a").count() == 4
                        await tabs.locator('[data-deck-shell-action="mic"]').wait_for(
                            state="visible"
                        )
                        await top.locator(".deck-brand").click()
                        await page.locator(".bh-form textarea:enabled").wait_for(state="visible")
                        await page.wait_for_function(
                            "() => !document.querySelector('[data-deck=tabs] [data-deck-shell-action=mic]')"
                        )
                        assert await page.locator(".bh-form .deck-composer-mic").is_visible()
                        await page.locator(".bh-form").evaluate(
                            "e => e.insertAdjacentHTML('beforeend', '<button type=button data-voice=mic aria-label=Record></button>')"
                        )
                        await page.wait_for_function(
                            "() => !document.querySelector('.bh-form .deck-composer-mic')"
                        )
                        assert await page.locator(".bh-form [data-voice=mic]").count() == 1
                        await page.locator(".bh-form [data-voice=mic]").evaluate("e => e.remove()")
                        await page.locator(".bh-form .deck-composer-mic").wait_for(state="visible")
                        for entry in ["bots", "compute", "settings", "today"]:
                            await tabs.locator(f'[data-nav="{entry}"]').click()
                            await page.wait_for_function(
                                "hash => location.hash === hash", arg=f"#/{entry}"
                            )
                            await top.wait_for(state="visible")
                            await page.wait_for_function(
                                "document.querySelector('[data-deck=top]')?.getBoundingClientRect().height === 52"
                            )
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
                        await page.wait_for_function(
                            "() => !document.querySelector('[data-deck=tabs] [data-deck-shell-action=mic]')"
                        )
                        await page.goto(url + "#/bots/-/coworkers")
                        await page.locator(".cb-composer").wait_for(state="visible")
                        await page.locator(".cb-composer .deck-composer-mic").wait_for(
                            state="visible"
                        )
                        await page.wait_for_function(
                            "() => !document.querySelector('[data-deck=tabs] [data-deck-shell-action=mic]')"
                        )
                        assert await page.locator(".cb-detail").is_visible()
                        assert await page.evaluate("document.documentElement.scrollWidth") == width
                        await page.goto(url + "#/today")
                        await page.locator('[data-deck="top"]').wait_for(state="visible")
                    if width == 390:
                        await page.wait_for_selector(".bt-card", state="visible")
                    else:
                        await page.wait_for_selector(".bots-list .cb-bot-row", state="visible")
                    if width == 390:
                        await page.wait_for_function("""() => {
                          const tabs=document.querySelector('[data-deck=tabs]');
                          return tabs?.getBoundingClientRect().bottom===innerHeight && document.elementFromPoint(350, innerHeight-30)?.closest('[data-deck=tabs]');
                        }""")
                    await page.screenshot(path=str(output / f"shell-{name}.png"))
                    if width == 1440:
                        # Supplement the live legacy capture with explicit optional-metadata fixtures.
                        await page.evaluate("""async () => {
                          const {renderDeckBar}=await import('/app/deck.js');
                          document.querySelector('[data-deck=bar]').outerHTML=renderDeckBar({hash:'#/bots',user:'Workspace person',role:'member',workspace:'Studio',bots:[
                            {id:'fixture',name:'Release bot',section:'Delivery',unread:2,coworkers:[
                              {name:'Checklist',state:'waiting'},{name:'Preview',state:'healthy'},
                              {name:'Build',state:'busy'},{name:'Notes',state:'parked'}]},
                            {id:'fixture-time',name:'Research bot',last:{at:new Date().toISOString()}}
                          ]});
                        }""")
                        await page.screenshot(path=str(output / "rail-states-fixture.png"))
                    if width == 390:
                        await page.locator(
                            '[data-deck=tabs] [data-deck-shell-action="mic"]'
                        ).click()
                        metrics = await page.evaluate("""() => {
                          const tabs=document.querySelector('#phone-tabs'), error=document.querySelector('#screen-error');
                          return {tabs:tabs?.getBoundingClientRect().toJSON(), tabsStyle:tabs?.getAttribute('style'), error:error?.textContent, errorHidden:error?.hidden, errorBox:error?.getBoundingClientRect().toJSON(), scrollY, height:innerHeight};
                        }""")
                        (output / "phone-chrome.json").write_text(json.dumps(metrics, indent=2))
                        assert "Enable voice input" in metrics["error"], metrics
                        assert metrics["errorBox"]["height"] > 0, metrics
                    assert not errors, errors
                    await context.close()
            finally:
                await browser.close()

    asyncio.run(check())
