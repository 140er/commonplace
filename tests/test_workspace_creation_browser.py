"""Real-session workspace creation, with optional browser availability."""

import json
import os
from pathlib import Path

import pytest

from commonplace import sessions
from tests.test_commonplace_invites import world  # noqa: F401

SETTLE = """(selector) => {
  // Settled: the same element, at the same place, for 400 ms (no redraw replaced or moved it).
  const node = document.querySelector(selector);
  const box = node && node.getBoundingClientRect();
  const key = box ? [box.x, box.y, box.width, box.height].join(",") : "";
  const seen = window.__settle || (window.__settle = {});
  const last = seen[selector];
  const now = performance.now();
  if (!node || !last || last.node !== node || last.key !== key) {
    seen[selector] = { node, key, since: now };
    return false;
  }
  return now - last.since >= 400;
}"""


@pytest.fixture
def browser():
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    with playwright.sync_playwright() as manager:
        try:
            browser = manager.chromium.launch()
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
    monkeypatch.setenv("COMMONPLACE_WORKSPACE_LIMIT", "25")
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
        if look == "deck":
            # A page that has just loaded may still redraw its rail as data arrives; act once the rail
            # is the same element in the same place for 400 ms, so the click never lands mid-redraw.
            page.wait_for_function(SETTLE, arg="#board-nav", polling=100)
            # The Deck palette lists destinations only; the switcher lives in the account menu at the
            # foot of the sidebar, and on a phone in the sheet the header's More button opens.
            if size[0] == 1280:
                # The menu stays open behind the switcher dialog; open it only when it is closed.
                if page.locator(".rail .user-menu[open]").count() == 0:
                    page.locator(".rail .user-menu > summary").click()
            else:
                page.get_by_role(
                    "button", name="More: settings, cloud workspaces and account"
                ).click()
            page.locator('[data-action="switch-workspace"]:visible').first.click()
            expect(page.locator(".workspace-switch")).to_be_visible()
            return
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
                == "rgb(28, 37, 48)"
            )
            assert (
                page.locator(".workspace-switch").evaluate(
                    "el => getComputedStyle(el).borderRadius"
                )
                == "14px"
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
        expect(page.locator(".workspace-switch")).not_to_be_visible()
        expect(page).to_have_url(origin + "/app/#/" if look == "deck" else origin + "/app/#/bots")
        assert len(calls) == 1 and len(site.db.workspaces()) == before + 1
        created = next(row["id"] for row in site.db.workspaces() if row["name"] == "Separate work")
        assert created != site.owner.workspace
        assert site.db.role(created, site.owner.id) == "owner"
        assert site.db.role(created, site.guest.id) is None
        assert context.request.get(origin + "/api/me").json()["workspace"] == created
        boards = context.request.get(origin + "/api/boards").json()["boards"]
        assert [board["slug"] for board in boards] == ["commonplace"]
        assert all(not any(board["counts"].values()) for board in boards)
        expect(page.locator(".shell")).to_be_visible()
        if look == "deck":
            # Home is Waiting on you; a new workspace has nothing waiting.
            expect(page.get_by_role("region", name="Waiting on you")).to_be_visible()
        else:
            expect(page.get_by_role("heading", name="Bots", exact=True)).to_be_visible()
        target = os.environ.get("WORKSPACE_SHOTS")
        if target:
            directory = Path(target)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(directory / f"577-home-{look}-{size[0]}.png"))
        switcher()
        page.locator(f'[data-workspace="{site.owner.workspace}"]').click()
        expect(page.locator(".workspace-switch")).not_to_be_visible()
        assert context.request.get(origin + "/api/me").json()["workspace"] == site.owner.workspace
        page.goto(f"{origin}/app/?ui={look}#/board/work")
        expect(page.locator(".shell")).to_be_visible()

        def refuse(name, message):
            switcher()
            confirm(name)
            page.get_by_role("button", name="Create workspace", exact=True).click()
            expect(
                page.locator(
                    "[data-workspace-refusal]" if look == "deck" else "[data-workspace-error]"
                )
            ).to_have_text(message)
            assert len(site.db.workspaces()) == before + 1
            assert (
                context.request.get(origin + "/api/me").json()["workspace"] == site.owner.workspace
            )
            shot("refusal-" + name.lower())
            page.get_by_role(
                "button", name="Close" if look == "deck" else "Cancel", exact=True
            ).click()
            if look != "deck":
                page.get_by_role("button", name="Close", exact=True).click()

        monkeypatch.setenv("COMMONPLACE_SIGNUP", "closed")
        refuse(
            "Closed",
            "New workspaces are turned off on this server."
            if look == "deck"
            else "This instance is closed to new workspaces.",
        )
        monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
        with monkeypatch.context() as patch:
            patch.setenv("COMMONPLACE_WORKSPACE_LIMIT", "1")
            refuse("Cap", "You can have at most 1 owned workspaces.")
        from commonplace import plans
        from mempalace import storage_guard

        with monkeypatch.context() as patch:
            patch.setenv("MEMPALACE_MIN_FREE_BYTES", "100")
            patch.setattr(storage_guard, "free_bytes", lambda path: 1)
            refuse("Disk", storage_guard.MESSAGE)
        site.gateway.supervisor.pause(drain_s=0)
        try:
            refuse("Backup", "Workspaces are paused for a backup. Try again shortly.")
        finally:
            site.gateway.supervisor.resume()
        with monkeypatch.context() as patch:

            def plan_refusal(*args, **kwargs):
                raise plans.LimitReached("Your plan does not allow another workspace.")

            patch.setattr(plans, "assign_signup_plan", plan_refusal)
            refuse("Plan", "Your plan does not allow another workspace.")
        with monkeypatch.context() as patch:
            patch.setenv("COMMONPLACE_WORKSPACE_CREATIONS_PER_HOUR", "1")
            refuse("Rate", "Too many new workspaces. Try again in 60 minutes.")
    finally:
        context.close()
