"""Real-session workspace creation, with optional browser availability."""

import json
import os
import shutil
import uuid
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

    measurements = {}

    def shot(label):
        expect(page.locator(".workspace-switch")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator(".workspace-switch").evaluate("el => el.scrollWidth <= el.clientWidth")
        if look == "deck":
            measured = page.locator(".workspace-switch").evaluate("""el => {
              const rect = node => node.getBoundingClientRect(), css = node => getComputedStyle(node);
              const title = el.querySelector('h2'), rows = [...el.querySelectorAll('.workspace-row')];
              return { width: rect(el).width, padding: css(el).padding, left: rect(el).left,
                titleLeft: rect(title).left, titleSize: css(title).fontSize,
                rowLefts: rows.map(row => rect(row.querySelector('.workspace-label')).left),
                rowLayouts: rows.map(row => css(row).display),
                currentOpacity: el.querySelector('[aria-current]') ? css(el.querySelector('[aria-current]')).opacity : null,
                confirmButtons: el.querySelectorAll('[data-workspace-confirm]').length,
                buttons: [...el.querySelectorAll('button')].map(button => button.textContent.trim()) };
            }""")
            measurements[label] = measured
            assert measured["width"] == (400 if size[0] == 1280 else size[0] - 32)
            assert measured["padding"] == "24px"
            assert measured["titleSize"] == "24px"
            assert measured["titleLeft"] == measured["left"] + 24
            assert all(left == measured["titleLeft"] for left in measured["rowLefts"])
            assert all(layout == "grid" for layout in measured["rowLayouts"])
            if measured["currentOpacity"] is not None:
                assert measured["currentOpacity"] == "1"
            if label.startswith("refusal"):
                assert measured["confirmButtons"] == 0 and measured["buttons"] == ["Close"]
        target = os.environ.get("WORKSPACE_SHOTS")
        if target and look == "deck":
            directory = Path(target)
            directory.mkdir(parents=True, exist_ok=True)
            suffix = "desktop" if size[0] == 1280 else "phone"
            page.screenshot(path=str(directory / f"500-design-{label}-{suffix}.png"))
            (directory / f"500-design-measurements-{suffix}.json").write_text(
                json.dumps(measurements, indent=2) + "\n"
            )

    def confirm(name):
        page.get_by_role("button", name="Create workspace", exact=True).click()
        page.get_by_label("Workspace name", exact=True).fill(name)
        page.get_by_role("button", name="Continue", exact=True).click()
        expect(
            page.get_by_text(
                "This makes a new, empty workspace that only you can see. Your current workspace does not change."
                if look == "deck"
                else "This creates an empty isolated workspace owned by you. Your current workspace is unaffected.",
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
                == "rgba(20, 19, 25, 0.94)"
            )
            assert (
                page.locator(".workspace-switch").evaluate(
                    "el => getComputedStyle(el).borderRadius"
                )
                == "20px"
            )
        if look == "deck":
            page.get_by_role("button", name="Create workspace", exact=True).click()
            expect(page.get_by_role("button", name="Continue", exact=True)).to_be_disabled()
            shot("name-empty")
            field = page.get_by_label("Workspace name", exact=True)
            field.fill("x" * 129)
            expect(page.locator("[data-workspace-name-error]")).to_have_text(
                "Use 128 characters or fewer."
            )
            expect(page.get_by_role("button", name="Continue", exact=True)).to_be_disabled()
            shot("name-too-long")
            field.fill("Owner Home")
            expect(page.locator("[data-workspace-name-error]")).to_have_text(
                "You already have a workspace with this name. Choose a different name."
            )
            expect(page.get_by_role("button", name="Continue", exact=True)).to_be_disabled()
            shot("name-existing")
            assert not calls
            page.get_by_role("button", name="Cancel", exact=True).click()
            page.get_by_role("button", name="Close", exact=True).click()
            for name in [
                "Research",
                "Planning",
                "Library",
                "A synthetic workspace with a deliberately long name for the switcher layout",
            ]:
                site.db.create_workspace(name, site.owner.id)
            switcher()
            expect(page.locator(".workspace-row")).to_have_count(5)
            shot("switcher-five")
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
        created = next(row["id"] for row in site.db.workspaces() if row["name"] == "Separate work")
        assert created != site.owner.workspace
        assert site.db.role(created, site.owner.id) == "owner"
        assert site.db.role(created, site.guest.id) is None
        me = context.request.get(origin + "/api/me").json()
        assert me["workspace"] == site.owner.workspace
        if look == "deck":
            expect(
                page.get_by_text(
                    '"Separate work" is ready. It is empty and only you are in it.', exact=True
                )
            ).to_be_visible()
        shot("result")
        page.get_by_role("button", name="Stay here", exact=True).click()
        switcher()
        confirm("Another workspace")
        monkeypatch.setenv("COMMONPLACE_SIGNUP", "closed")
        page.get_by_role("button", name="Create workspace", exact=True).click()
        expect(
            page.locator("[data-workspace-refusal]" if look == "deck" else "[data-workspace-error]")
        ).to_have_text(
            "New workspaces are turned off on this server."
            if look == "deck"
            else "This instance is closed to new workspaces."
        )
        assert len(site.db.workspaces()) == before + 1
        shot("refusal")
        if look == "deck":
            page.get_by_role("button", name="Close", exact=True).click()
            monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
            from commonplace import invites, plans

            def refuse(*args, **kwargs):
                raise plans.LimitReached("Your plan does not allow another workspace.")

            switcher()
            confirm("Plan example")
            with monkeypatch.context() as patch:
                patch.setattr(plans, "assign_signup_plan", refuse)
                page.get_by_role("button", name="Create workspace", exact=True).click()
                expect(page.locator("[data-workspace-refusal]")).to_have_text(
                    "Your plan does not allow another workspace."
                )
            shot("refusal-plan")
            page.get_by_role("button", name="Close", exact=True).click()
            for index in range(4):
                invites.create_workspace(
                    site.db,
                    site.owner.id,
                    name=f"Rate example {index}",
                    request_id=str(uuid.uuid4()),
                )
            switcher()
            confirm("Rate example")
            page.get_by_role("button", name="Create workspace", exact=True).click()
            expect(page.locator("[data-workspace-refusal]")).to_have_text(
                "Too many new workspaces. Try again in 60 minutes."
            )
            shot("refusal-rate")
            page.get_by_role("button", name="Close", exact=True).click()
            switcher()
        else:
            page.get_by_role("button", name="Cancel", exact=True).click()
        page.locator(f'[data-workspace="{created}"]').click()
        expect(page.locator(".workspace-switch")).not_to_be_visible()
        assert context.request.get(origin + "/api/me").json()["workspace"] == created
        assert context.request.get(origin + "/api/boards").json() == {"boards": []}
    finally:
        context.close()
