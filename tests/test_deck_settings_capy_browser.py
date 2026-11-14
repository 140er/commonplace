"""Optional Settings screens with the production shell and browser-free companion tests."""

import os
from pathlib import Path

import pytest

from tests.test_deck_settings_browser import browser as browser_fixture, open_settings

browser = browser_fixture


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_settings_columns_preserve_entries_and_policy_writes(browser, width, height):
    context, page = open_settings(browser, width)
    page.set_viewport_size({"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    writes = []
    actions = [
        {"name": name, "kind": "write", "mode": mode}
        for name, mode in [
            ("shell.exec", "ask"),
            ("git.push", "allowed"),
            ("deploy.preview", "off"),
            ("memory_save", "allowed"),
        ]
    ]

    def policy(route):
        if route.request.method == "POST":
            body = route.request.post_data_json
            writes.append(body)
            for action in actions:
                action["mode"] = body["actions"].get(action["name"], action["mode"])
        route.fulfill(
            json={
                "actions": actions,
                "modes": ["allowed", "ask", "off"],
                "approval": {"approval_ttl_s": 600},
            }
        )

    sharing = {"sharing": False, "can_configure": True}

    def share(route):
        if route.request.method == "PATCH":
            sharing.update(route.request.post_data_json)
            writes.append(route.request.post_data_json)
        route.fulfill(json=sharing)

    page.route("**/api/tool-policy", policy)
    page.route("**/api/bots/settings", share)
    try:
        page.reload()
        page.wait_for_selector(".capy-policy")
        page.wait_for_selector("[data-capy-sharing] input")
        page.evaluate("document.fonts.ready")
        assert page.locator(".settings > .deck-settings-main").count() == 1
        assert page.locator(".settings > :last-child").get_attribute("data-deck-appearance") == ""
        titles = page.locator(".deck-settings-main > section > h2").all_text_contents()
        assert titles[:4] == [
            "Bot sharing",
            "What bots may do without asking",
            "Second review",
            "Connected apps",
        ]
        for title in [
            "Free plan",
            "Coworker management",
            "Chat",
            "Scheduled prompts",
            "Phone notifications",
            "Webhooks",
            "Look",
        ]:
            assert title in titles
        for selector in [
            '[data-form="agent"]',
            "[data-schedule-form]",
            '[data-form="webhook"]',
            '[data-action="logout"]',
            '[data-deck-action="toggle"]',
            "[data-orchestration-enabled]",
            '[data-voice="enabled"]',
        ]:
            entry = page.locator(".settings").locator(selector)
            entry.scroll_into_view_if_needed()
            assert entry.is_visible(), selector
        for option in page.locator(
            '#deck-connections select[name="client"] option'
        ).all_text_contents():
            assert not any(name in option.lower() for name in ["claude", "codex", "cursor", "grok"])
        main = page.locator(".deck-settings-main").bounding_box()
        panel = page.locator(".deck-appearance").bounding_box()
        if width == 1440:
            assert panel["x"] > main["x"] + main["width"]
            assert (
                page.locator(".deck-appearance").evaluate("el=>getComputedStyle(el).position")
                == "sticky"
            )
        else:
            assert panel["y"] >= main["y"] + main["height"]
            assert (
                page.locator(".deck-appearance").evaluate("el=>getComputedStyle(el).position")
                == "static"
            )
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        assert page.locator(".settings").evaluate("el=>el.scrollWidth <= el.clientWidth")
        row = page.locator('[data-tool-policy-row="shell.exec"]')
        row.get_by_role("button", name="Allow", exact=True).click()
        page.wait_for_function(
            "document.querySelector('[data-tool-policy-mode=\"shell.exec\"]').value==='allowed'"
        )
        assert writes[-1] == {"actions": {"shell.exec": "allowed"}}
        row.get_by_role("button", name="Ask", exact=True).click()
        page.wait_for_function(
            "document.querySelector('[data-tool-policy-mode=\"shell.exec\"]').value==='ask'"
        )
        assert writes[-1] == {"actions": {"shell.exec": "ask"}}
        page.wait_for_function(
            "!document.querySelector('[data-tool-policy-mode=\"shell.exec\"]').disabled"
        )
        deny = row.get_by_role("button", name="Deny", exact=True)
        assert deny.evaluate("el=>getComputedStyle(el).color") == "rgb(251, 113, 133)", (
            deny.evaluate(
                "el=>({color:getComputedStyle(el).color,disabled:el.disabled,red:getComputedStyle(el).getPropertyValue('--red')})"
            )
        )
        if width == 390:
            assert deny.bounding_box()["height"] >= 44
        page.get_by_role("switch", name="Allow bot sharing").check()
        assert writes[-1] == {"sharing": True}
        field = page.get_by_role("searchbox", name="Search permissions")
        field.fill("memory_save")
        assert page.locator('[data-tool-policy-row="memory_save"]').count() == 1
        field.fill("")
        page.locator('[data-tool-policy-row="shell.exec"] summary').click()
        assert page.get_by_text("shell.exec", exact=True).is_visible()
        page.locator('[data-scene="palace-gate"]').click()
        assert page.locator("[data-appearance-preview]").evaluate(
            "c=>c.getContext('2d').getImageData(0,0,c.width,c.height).data.some(x=>x!==0)"
        )
        page.locator('[data-scene="capy-cyber"]').click()
        page.locator(".settings").evaluate("el=>el.scrollTop=0")
        output = os.environ.get("CAPY_SETTINGS_SHOTS")
        if output:
            folder = Path(output)
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / f"settings-{width}x{height}.png"))
            page.locator("#tool-policy").scroll_into_view_if_needed()
            page.screenshot(path=str(folder / f"policies-{width}x{height}.png"))
            page.locator(".deck-appearance").scroll_into_view_if_needed()
            page.screenshot(path=str(folder / f"appearance-{width}x{height}.png"))
        assert not errors, errors
    finally:
        context.close()
