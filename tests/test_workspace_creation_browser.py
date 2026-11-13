"""Real-session workspace creation, with optional browser availability."""

import os
import shutil
from pathlib import Path

import pytest

from commonplace import sessions
from tests.test_commonplace_invites import world  # noqa: F401


@pytest.fixture
def browser():
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    with playwright.sync_playwright() as manager:
        try:
            browser = manager.chromium.launch(executable_path=shutil.which("google-chrome"))
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional browser executable unavailable")
            raise
        yield browser
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)])
@pytest.mark.parametrize("look", ["deck", "classic"])
def test_workspace_creation_journey(browser, world, monkeypatch, size, look):  # noqa: F811
    from playwright.sync_api import expect

    site = world
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
    assert site.owner.browser.post("/api/boards", {"slug": "work", "name": "Work"}).status == 201
    origin = f"http://127.0.0.1:{site.port}"
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]}, device_scale_factor=2
    )
    context.add_cookies(
        [
            {
                "name": sessions.COOKIE,
                "value": site.owner.browser.cookies[sessions.COOKIE],
                "url": origin,
            }
        ]
    )
    page = context.new_page()
    page.set_default_timeout(10000)
    calls = []
    page.on(
        "request",
        lambda request: (
            calls.append(request.url)
            if request.method == "POST" and request.url == origin + "/api/workspaces"
            else None
        ),
    )
    page.goto(f"{origin}/app/?ui={look}#/board/work")
    expect(page.locator(".shell")).to_be_visible()

    def switcher():
        page.keyboard.press("Control+k")
        page.get_by_role("combobox", name="Search or run an action").fill("Switch workspace")
        page.get_by_role("option", name="Switch workspace", exact=True).click()
        expect(page.locator(".workspace-switch")).to_be_visible()

    def shot(label):
        expect(page.locator(".workspace-switch")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator(".workspace-switch").evaluate("el => el.scrollWidth <= el.clientWidth")
        target = os.environ.get("WORKSPACE_SHOTS")
        if target and look == "deck":
            directory = Path(target)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(directory / f"500-{label}-{'desktop' if size[0] == 1280 else 'phone'}.png")
            )

    def confirm(name):
        page.get_by_role("button", name="Create workspace", exact=True).click()
        page.get_by_label("Workspace name", exact=True).fill(name)
        page.get_by_role("button", name="Continue", exact=True).click()
        expect(
            page.get_by_text(
                "This creates an empty isolated workspace owned by you. Your current workspace is unaffected.",
                exact=True,
            )
        ).to_be_visible()

    try:
        switcher()
        shot("switcher")
        if look == "deck":
            assert (
                page.locator(".workspace-switch").evaluate(
                    "el => getComputedStyle(el).backgroundColor"
                )
                == "rgb(17, 17, 17)"
            )
            assert (
                page.locator(".workspace-switch").evaluate(
                    "el => getComputedStyle(el).borderRadius"
                )
                == "20px"
            )
        before = len(site.db.workspaces())
        confirm("Separate work")
        shot("confirmation")
        page.get_by_role("button", name="Cancel", exact=True).click()
        assert len(site.db.workspaces()) == before and not calls
        confirm("Separate work")
        page.get_by_role("button", name="Create workspace", exact=True).evaluate(
            "el => { el.click(); el.click(); }"
        )
        expect(page.get_by_role("heading", name="Workspace created", exact=True)).to_be_visible()
        assert len(calls) == 1 and len(site.db.workspaces()) == before + 1
        created = site.db.workspaces()[-1]["id"]
        assert created != site.owner.workspace
        assert site.db.role(created, site.owner.id) == "owner"
        assert site.db.role(created, site.guest.id) is None
        me = context.request.get(origin + "/api/me").json()
        assert me["workspace"] == site.owner.workspace
        shot("result")
        page.get_by_role("button", name="Stay here", exact=True).click()
        switcher()
        confirm("Another workspace")
        monkeypatch.setenv("COMMONPLACE_SIGNUP", "closed")
        page.get_by_role("button", name="Create workspace", exact=True).click()
        expect(page.locator("[data-workspace-error]")).to_have_text(
            "This instance is closed to new workspaces."
        )
        assert len(site.db.workspaces()) == before + 1
        shot("refusal")
        page.get_by_role("button", name="Cancel", exact=True).click()
        page.locator(f'[data-workspace="{created}"]').click()
        expect(page.locator(".workspace-switch")).not_to_be_visible()
        assert context.request.get(origin + "/api/me").json()["workspace"] == created
        assert context.request.get(origin + "/api/boards").json() == {"boards": []}
    finally:
        context.close()
