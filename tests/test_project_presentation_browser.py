"""G24 in a browser: project settings edit the panel and replace the coordinator, with synthetic HTTP."""

import copy
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"
PANEL = {
    "format": "commonplace.panel/1",
    "title": "Atlas",
    "highlights": ["Beta ships Friday"],
    "links": [{"label": "Runbook", "url": "https://example.com/runbook"}],
}
CANDIDATES = [
    {"name": "alpha", "actor": "conn:alpha", "current": True},
    {"name": "beta", "actor": "conn:beta", "current": False},
]


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch()
        yield browser
        browser.close()


def make_state(owner=True):
    return {
        "owner": owner,
        "panel": copy.deepcopy(PANEL),
        "coordinator": "conn:alpha",
        "writes": [],
        "fail": {},
        "effect": {"open_cards": 4, "waiting_asks": 2},
    }


def project_for(state):
    main = {
        "name": "alpha",
        "source": "coordinator",
        "connected": True,
        "seen": True,
        "connectable": state["owner"],
    }
    return {
        "board": "ledger",
        "name": "Ledger",
        "can_configure": state["owner"],
        "can_edit_panel": state["owner"],
        "main": main,
        "memory": {"shared": False, "wing": "project_ledger"},
        "models": {},
    }


def respond(route, state):
    request = route.request
    path, method = urlsplit(request.url).path, request.method
    if path.startswith("/app/"):
        asset = STATIC / path.removeprefix("/app/")
        route.fulfill(
            body=asset.read_bytes(),
            content_type=mimetypes.guess_type(asset.name)[0] or "application/octet-stream",
        )
    elif path == "/":
        styles = "".join(
            f'<link rel="stylesheet" href="/app/{name}">'
            for name in ("app.css", "ui.css", "workbench.css")
        )
        route.fulfill(
            content_type="text/html", body=f"<!doctype html>{styles}<main id='project'></main>"
        )
    elif path == "/api/projects/ledger":
        route.fulfill(json=project_for(state))
    elif path == "/api/projects/ledger/panel":
        if method == "PUT":
            doc = request.post_data_json["panel"]
            state["writes"].append(("PUT", path, doc))
            if state["fail"].get("panel"):
                route.fulfill(status=400, json=state["fail"]["panel"])
                return
            state["panel"] = None if doc is None else {**doc, "links": doc.get("links", [])}
        route.fulfill(json={"panel": state["panel"]})
    elif path == "/api/boards/ledger/coordinator":
        if state["fail"].get("read"):
            route.fulfill(status=500, json={"error": "boom"})
            return
        if not state["owner"]:
            route.fulfill(status=403, json={"error": "only a workspace owner can do that"})
            return
        route.fulfill(
            json={
                "board": "ledger",
                "coordinator": state["coordinator"],
                "candidates": [
                    {**c, "current": c["actor"] == state["coordinator"]} for c in CANDIDATES
                ],
                "effect": state["effect"],
            }
        )
    elif path == "/api/boards/ledger" and method == "PATCH":
        body = request.post_data_json
        state["writes"].append(("PATCH", path, body))
        if state["fail"].get("coordinator"):
            route.fulfill(status=500, json={"error": "database is locked"})
            return
        state["coordinator"] = body["coordinator"]
        route.fulfill(json={"board": "ledger", "coordinator": body["coordinator"]})
    elif path == "/api/connections":
        route.fulfill(json={"connections": []})
    elif path == "/api/boards/ledger/cards":
        route.fulfill(json={"cards": []})
    elif path == "/api/orch/boards/ledger/configs":
        route.fulfill(json={"versions": [], "accepted": None})
    elif path == "/api/orch/boards/ledger/roles/main/checkpoint":
        route.fulfill(json={"lease": None, "config": None})
    elif path == "/api/chat-settings":
        route.fulfill(json={"front": {"provider": "none"}})
    else:
        route.fulfill(status=404, json={"error": "not found"})


@pytest.fixture(params=[(1280, 1000), (390, 844)], ids=["desktop", "phone"])
def settings_page(browser, request):
    width, height = request.param

    def open_settings(owner=True, **fail):
        state = make_state(owner)
        state["fail"] = fail
        context = browser.new_context(viewport={"width": width, "height": height})
        request.addfinalizer(context.close)
        page = context.new_page()
        page.route("**/*", lambda route: respond(route, state))
        page.goto("http://project.test/")
        page.evaluate(
            """async () => {
                const ui = await import('/app/projects.js');
                const {api, escape: e} = await import('/app/api.js');
                await ui.mountProject(document.querySelector('#project'), {
                    slug: 'ledger', api, e, settings: true, restart: false,
                });
            }"""
        )
        return page, state

    return open_settings


def expect_text(locator, text):
    from playwright.sync_api import expect

    expect(locator).to_contain_text(text)


def test_owner_edits_the_panel_and_sees_the_saved_result(settings_page):
    from playwright.sync_api import expect

    page, state = settings_page()
    form = page.locator("[data-panel-form]")
    expect(form).to_be_visible()
    expect(form.locator('[name="highlights"]')).to_have_value("Beta ships Friday")
    form.locator('[name="highlights"]').fill("Beta ships Friday\nThree risks open")
    form.locator('[name="links"]').fill("Status page https://example.com/status")
    form.get_by_role("button", name="Save panel").click()
    expect_text(page.locator("[data-panel-status]"), "Panel saved")
    assert state["writes"][-1][2] == {
        "format": "commonplace.panel/1",
        "title": "Atlas",
        "highlights": ["Beta ships Friday", "Three risks open"],
        "links": [{"label": "Status page", "url": "https://example.com/status"}],
    }
    expect_text(page.locator("[data-panel-result]"), "Three risks open")
    expect_text(page.locator("[data-panel-result] a"), "Status page")


def test_panel_failure_keeps_the_saved_panel_and_the_typed_text(settings_page):
    from playwright.sync_api import expect

    page, state = settings_page(
        panel={"error": "at most 6 highlights are allowed", "code": "invalid_panel"}
    )
    form = page.locator("[data-panel-form]")
    form.locator('[name="highlights"]').fill("one\ntwo")
    form.get_by_role("button", name="Save panel").click()
    status = page.locator("[data-panel-status]")
    expect_text(status, "Could not save the panel. Nothing was changed.")
    expect_text(status, "at most 6 highlights are allowed")
    expect(form.locator('[name="highlights"]')).to_have_value("one\ntwo")
    assert state["panel"] == PANEL
    expect_text(page.locator("[data-panel-result]"), "Beta ships Friday")


def test_panel_needs_something_to_show_and_asks_before_clearing(settings_page):

    page, state = settings_page()
    form = page.locator("[data-panel-form]")
    form.locator('[name="highlights"]').fill("")
    form.locator('[name="links"]').fill("")
    form.get_by_role("button", name="Save panel").click()
    expect_text(page.locator("[data-panel-status]"), "Add at least one highlight")
    assert state["writes"] == []
    form.get_by_role("button", name="Clear panel").click()
    expect_text(page.locator("[data-panel-confirm]"), "Clear the panel?")
    page.get_by_role("button", name="Keep it").click()
    assert state["writes"] == [] and state["panel"] == PANEL
    form.get_by_role("button", name="Clear panel").click()
    page.locator("[data-panel-clear-ok]").click()
    expect_text(page.locator("[data-panel-status]"), "Panel cleared")
    assert state["writes"] == [("PUT", "/api/projects/ledger/panel", None)]
    expect_text(page.locator("[data-panel-none]"), "no panel")


def test_owner_replaces_the_coordinator_after_an_effect_preview(settings_page):
    from playwright.sync_api import expect

    page, state = settings_page()
    section = page.locator("[data-project-coordinator]")
    expect_text(section.locator("[data-coordinator-current]"), "alpha")
    expect_text(section.locator("[data-coordinator-distinct]"), "does not change any agent's model")
    expect_text(section.locator("[data-coordinator-distinct]"), "rotate or revoke a token")
    review = section.locator("[data-coordinator-review]")
    expect(review).to_be_disabled()
    section.get_by_label("beta").check()
    review.click()
    preview = section.locator("[data-coordinator-preview]")
    expect_text(preview, "alpha stops coordinating Ledger")
    expect_text(preview, "2 open asks addressed to alpha will come to you")
    expect_text(preview, "beta becomes the coordinator")
    expect_text(preview, "4 open cards keep their status")
    assert state["writes"] == []
    preview.get_by_role("button", name="Confirm change").click()
    expect_text(section.locator("[data-coordinator-status]"), "Coordinator changed to beta")
    expect_text(section.locator("[data-coordinator-current]"), "beta")
    assert state["writes"] == [("PATCH", "/api/boards/ledger", {"coordinator": "conn:beta"})]


def test_owner_clears_the_coordinator_and_may_cancel(settings_page):
    from playwright.sync_api import expect

    page, state = settings_page()
    section = page.locator("[data-project-coordinator]")
    section.get_by_label("No coordinator").check()
    section.locator("[data-coordinator-review]").click()
    expect_text(section.locator("[data-coordinator-preview]"), "come to you")
    section.get_by_role("button", name="Cancel").click()
    assert state["writes"] == [] and state["coordinator"] == "conn:alpha"
    section.get_by_label("No coordinator").check()
    section.locator("[data-coordinator-review]").click()
    section.get_by_role("button", name="Confirm change").click()
    expect_text(section.locator("[data-coordinator-status]"), "Coordinator cleared")
    expect_text(section.locator("[data-coordinator-current]"), "No coordinator is appointed")
    assert state["writes"] == [("PATCH", "/api/boards/ledger", {"coordinator": None})]
    expect(section.locator("[data-coordinator-review]")).to_be_disabled()


def test_coordinator_failure_keeps_the_old_one(settings_page):
    page, state = settings_page(coordinator=True)
    section = page.locator("[data-project-coordinator]")
    section.get_by_label("beta").check()
    section.locator("[data-coordinator-review]").click()
    section.get_by_role("button", name="Confirm change").click()
    status = section.locator("[data-coordinator-status]")
    expect_text(status, "Could not change the coordinator. Nothing was changed.")
    expect_text(status, "alpha is still the coordinator")
    expect_text(section.locator("[data-coordinator-current]"), "alpha")
    assert state["coordinator"] == "conn:alpha"


def test_unreadable_coordinator_is_a_message_not_an_empty_box(settings_page):
    from playwright.sync_api import expect

    page, state = settings_page(read=True)
    section = page.locator("[data-project-coordinator]")
    expect_text(section.locator("[data-coordinator-error]"), "Could not load the coordinator")
    expect(section.get_by_role("button", name="Try again")).to_be_visible()
    expect(section.locator("[data-coordinator-review]")).to_have_count(0)


def test_non_owner_sees_read_only_text_with_the_reason(settings_page):
    from playwright.sync_api import expect

    page, state = settings_page(owner=False)
    panel = page.locator("[data-project-panel-editor]")
    expect_text(panel, "Beta ships Friday")
    expect_text(panel.locator("[data-panel-reason]"), "Only the workspace owner")
    coordinator = page.locator("[data-project-coordinator]")
    expect_text(coordinator.locator("[data-coordinator-current]"), "Coordinator: alpha")
    expect_text(coordinator.locator("[data-coordinator-reason]"), "Only the workspace owner")
    for scope in (panel, coordinator):
        expect(scope.locator("form, input, textarea, [data-coordinator-review]")).to_have_count(0)
    assert state["writes"] == []
