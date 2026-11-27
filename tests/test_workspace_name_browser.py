"""Inline workspace-name refusals in both looks at desktop and phone widths."""

import json
import os
from pathlib import Path

import pytest

from commonplace import invites, sessions
from tests.test_commonplace_invites import world  # noqa: F401
from tests.test_workspace_creation_browser import browser  # noqa: F401


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)])
@pytest.mark.parametrize("look", ["deck", "classic"])
def test_workspace_name_validation(browser, world, monkeypatch, size, look):  # noqa: F811
    from playwright.sync_api import expect

    site = world
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
    assert site.owner.browser.post("/api/boards", {"slug": "work", "name": "Work"}).status == 201
    origin = f"http://127.0.0.1:{site.port}"
    context = browser.new_context(viewport={"width": size[0], "height": size[1]})
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
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request)
            if request.method == "POST" and request.url == origin + "/api/workspaces"
            else None
        ),
    )
    try:
        page.goto(f"{origin}/app/?ui={look}#/board/work")
        expect(page.locator(".shell")).to_be_visible()
        if look == "deck":
            # The Deck switcher lives in the account menu at the foot of the sidebar, and on a phone
            # in the sheet the header's More button opens.
            if size[0] == 1280:
                page.locator(".rail .user-menu > summary").click()
            else:
                page.get_by_role(
                    "button", name="More: settings, cloud workspaces and account"
                ).click()
            page.locator('[data-action="switch-workspace"]:visible').first.click()
        else:
            page.keyboard.press("Control+k")
            page.get_by_role("combobox", name="Search or run an action").fill("Switch workspace")
            page.get_by_role("option", name="Switch workspace", exact=True).click()
        page.get_by_role("button", name="Create workspace", exact=True).click()
        field = page.get_by_label("Workspace name", exact=True)
        button = page.get_by_role("button", name="Continue", exact=True)
        expect(button).to_be_disabled()
        messages = {}
        for index, value in enumerate(
            [
                "bad/..<script>x</script>",
                "UX-TEST (leave)",
                "[Research]",
                "bad\\name",
                "bad<script>",
                "_leading",
                "trailing_",
                "x" * 129,
            ]
        ):
            field.fill(value)
            expect(button).to_be_disabled()
            error = page.locator("[data-workspace-name-error]")
            expect(error).not_to_be_empty()
            if len(value) <= 128:
                expect(error).to_contain_text("Use 1–128 letters or digits")
                expect(error).to_contain_text("underscores (_)")
            else:
                expect(error).to_have_text("Use 128 characters or fewer.")
            expect(page.locator("[data-workspace-confirm]")).to_have_count(0)
            messages[value] = error.inner_text()
            assert not requests
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            target = os.environ.get("WORKSPACE_SHOTS")
            if target and index < 3:
                directory = Path(target)
                directory.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(directory / f"580-invalid-{index}-{look}-{size[0]}.png"))
        for value in ["UX-TEST leave", "My team", "Anna's space"]:
            field.fill(value)
            expect(button).to_be_enabled()
            expect(page.locator("[data-workspace-name-error]")).to_be_empty()
        button.click()
        expect(page.locator("[data-workspace-confirm]")).to_be_visible()
        assert not requests

        # Even after client acceptance, an authoritative plain server refusal stays visible.
        def refuse(*args, **kwargs):
            raise invites.WorkspaceCreationError(400, "Workspace name contains invalid characters")

        monkeypatch.setattr(invites, "create_workspace", refuse)
        page.get_by_role("button", name="Create workspace", exact=True).click()
        expect(
            page.locator("[data-workspace-refusal]" if look == "deck" else "[data-workspace-error]")
        ).to_have_text("Workspace name contains invalid characters")
        assert len(requests) == 1 and len(site.db.workspaces()) == 2
        assert context.request.get(origin + "/api/me").json()["workspace"] == site.owner.workspace
        if os.environ.get("WORKSPACE_SHOTS"):
            (Path(os.environ["WORKSPACE_SHOTS"]) / f"580-strings-{look}-{size[0]}.json").write_text(
                json.dumps(messages, indent=2) + "\n"
            )
    finally:
        context.close()
