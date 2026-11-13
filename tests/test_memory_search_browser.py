"""Typed Memory searches and filters in both looks, with real source permissions."""

import os
from pathlib import Path

import pytest

from tests._chat_helpers import chat_environment, chat_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service


@pytest.mark.parametrize("look", ["deck", "classic"])
@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
@pytest.mark.parametrize("role", ["owner", "member"])
def test_typed_memory_search_and_filters(chat_hub, look, size, role):
    pytest.importorskip("playwright.sync_api", reason="optional browser regression dependency")
    from playwright.sync_api import expect, sync_playwright

    h = chat_hub
    owner = service(h, h.person(h.admin))
    owner.board_add(slug="private-work", wing="private")
    owner.space_post("work", "rate limit shared matching source")
    owner.space_post("private-work", "rate limit private matching source")
    auth = h.owner if role == "owner" else h.member
    status, boards, _ = h.get(auth, "/api/boards")
    assert status == 200
    assert ("private-work" in {b["slug"] for b in boards["boards"]}) == (role == "owner")
    # Direct requests keep the same permission boundary as the board inventory.
    assert h.get(auth, "/api/chat/private-work/search?q=rate")[0] == (
        200 if role == "owner" else 404
    )
    origin = f"http://{h.server.server_address[0]}:{h.port}"
    name, value = auth["Cookie"].split("=", 1)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(
                viewport={"width": size[0], "height": size[1]}, device_scale_factor=2
            )
            context.add_cookies([{"name": name, "value": value, "url": origin}])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{origin}/app/?ui={look}#/memory")
            query = page.get_by_role("searchbox", name="Search memory")
            query.fill("rate limit")
            query.press("Enter")
            results = page.locator(".memory-results")
            expect(results).to_contain_text("shared matching source")
            expect(query).to_have_value("rate limit")
            expect(query).to_be_focused()
            expect(page.locator(".memory-empty[role=alert]")).to_have_count(0)
            if role == "owner":
                expect(results).to_contain_text("private matching source")
            else:
                expect(results).not_to_contain_text("private matching source")
            for field, option in (
                ("project", "work"),
                ("source", "chat"),
                ("from", f"user:{h.admin}"),
                ("when", "7"),
            ):
                with page.expect_response(
                    lambda response: "/api/chat/work/search?" in response.url
                ) as response:
                    page.locator(f'[data-memory-search] [name="{field}"]').select_option(option)
                assert "q=rate+limit" in response.value.url
                expect(results).to_have_attribute("aria-busy", "false")
                expect(query).to_have_value("rate limit")
                expect(results).to_contain_text("shared matching source")
                expect(results).not_to_contain_text("private matching source")
            shots = os.environ.get("CP_MEMORY_SHOTS")
            if shots:
                target = Path(shots) / f"492-{role}-{look}-{size[0]}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(target), full_page=True)
            query.fill("")
            query.press("Enter")
            expect(page.locator(".memory-empty[role=alert] p")).to_have_text(
                "Use 1–8 words, up to 200 characters."
            )
            assert errors == []
        finally:
            browser.close()
