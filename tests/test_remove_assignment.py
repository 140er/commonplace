"""Reservation removal uses the existing handoff, authority and audit path."""

import os
import shutil
from pathlib import Path

import pytest

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh
from tests.test_commonplace_boards import api
from tests.test_commonplace_origin_holder_release import edit, snapshot
from tests.test_commonplace_stranded_reservations import reserved


def reservation(fresh, refusal=None):
    if refusal == "origin":
        _, receiver, number = reserved(fresh)
        # A historical reservation can remain Todo while origin release is pending.
        edit(fresh.owner, number, status="todo")
    else:
        creator = fresh.connect("creator")
        receiver = fresh.connect("receiver")
        number = creator.work.task_add(
            board="work", title="Prepare release notes", assignee=receiver.principal.actor
        )["number"]
    card = fresh.owner.task_show(number)
    assert card["status"] == "todo" and card["claim_until"] is None
    return card, receiver


def member_cookie(hub, fresh):
    email = "member@example.test"
    member = hub.db.create_user(email)
    hub.db.add_member(fresh.workspace, member, "member", actor=fresh.user)
    cookie = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, email, fresh.workspace)
    )
    return {"Cookie": f"{sessions.COOKIE}={cookie}"}


def test_removal_keeps_the_existing_handoff_audit(hub, fresh):
    card, receiver = reservation(fresh)
    number = card["number"]
    status, result = api(
        hub,
        fresh.cookie,
        "POST",
        f"/api/cards/{number}/handoff",
        {"to": "anyone", "note": "Available for the next person", "version": card["version"]},
    )
    assert status == 200, result
    assert result["status"] == "todo" and result["assignee"] is None
    assert result["claim_until"] is None
    event = fresh.owner.task_show(number)["events"][-1]
    assert event["kind"] == "handoff" and event["target"] is None
    assert event["text"] == "Available for the next person"
    assert event["actor"] == fresh.owner.principal.actor
    assert receiver.work.task_take_next(task=number)["status"] == "doing"


@pytest.mark.parametrize("refusal", ["active", "non-owner", "origin", "blank"])
def test_removal_refusals_leave_card_and_audit_unchanged(hub, fresh, refusal):
    card, receiver = reservation(fresh, refusal)
    number = card["number"]
    auth = member_cookie(hub, fresh) if refusal == "non-owner" else fresh.cookie
    if refusal == "active":
        receiver.work.task_take_next(task=number)
    before = snapshot(fresh.owner, number)
    status, result = api(
        hub,
        auth,
        "POST",
        f"/api/cards/{number}/handoff",
        {
            "to": "anyone",
            "note": "" if refusal == "blank" else "Remove assignment",
            "version": card["version"],
        },
    )
    assert status == (409 if refusal == "active" else 403), result
    assert result["error"]
    assert snapshot(fresh.owner, number) == before


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("CP_BROWSER_E2E") == "0":
        pytest.skip("browser checks disabled")
    required = os.environ.get("CP_BROWSER_E2E") == "1"
    if required:
        from playwright import sync_api as playwright
    else:
        playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as manager:
        try:
            browser = manager.chromium.launch(executable_path=shutil.which("google-chrome"))
        except playwright.Error:
            if required:
                raise
            pytest.skip("browser executable is unavailable")
        yield browser
        browser.close()


def open_drawer(browser, hub, fresh, card, width, look):
    # The same cookie and shell as a normal card visit; the server handles every write.
    from urllib.parse import urlunsplit

    context = browser.new_context(
        viewport={"width": width, "height": 844 if width == 390 else 800},
        device_scale_factor=2,
    )
    host, port = hub.server.server_address
    origin = urlunsplit(("http", f"{host}:{port}", "", "", ""))
    name, value = fresh.cookie["Cookie"].split("=", 1)
    context.add_cookies([{"name": name, "value": value, "url": origin}])
    page = context.new_page()
    page.goto(origin + f"/app/?ui={look}#/board/work")
    page.locator(f'.card[data-card="{card["number"]}"]').click()
    page.locator('[data-action="remove-assignment"]').wait_for()
    page.evaluate("document.fonts.ready")
    return context, page


def shot(page, look, state, width):
    folder = os.environ.get("ASSIGNMENT_SHOTS")
    if folder:
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(target / f"502-{look}-{state}-{'phone' if width == 390 else 'desktop'}.png"),
            animations="disabled",
        )


@pytest.mark.parametrize("look", ["classic", "deck"])
@pytest.mark.parametrize("width", [390, 1280])
def test_browser_remove_assignment(browser, hub, fresh, look, width):
    hub.gateway.rate_limits = False
    card, _ = reservation(fresh)
    context, page = open_drawer(browser, hub, fresh, card, width, look)
    try:
        action = page.get_by_role("button", name="Remove assignment", exact=True)
        if look == "deck":
            assert (
                action.bounding_box()["y"] + action.bounding_box()["height"]
                <= (page.locator(".workbench-details .ctx-foot").bounding_box()["y"])
            )
            assert (
                page.locator("[data-details-title]").evaluate("el=>getComputedStyle(el).fontSize")
                == "24px"
            )
        shot(page, look, "before", width)
        action.click()
        form = page.locator('[data-form="remove-assignment"]')
        assert form.get_by_label("Reason (required)").get_attribute("required") is not None
        assert form.locator('input[name="to"]').count() == 0
        assert form.get_by_text(
            "Anyone eligible to work on this board can pick it up."
        ).is_visible()
        if look == "deck":
            assert form.locator("h2").evaluate("el=>getComputedStyle(el).fontSize") == "24px"
            assert form.locator("textarea").evaluate("""el=>({
                background:getComputedStyle(el).backgroundColor,
                radius:getComputedStyle(el).borderRadius
            })""") == {"background": "rgb(30, 30, 30)", "radius": "12px"}
            assert (
                form.locator(".confirm-removal").evaluate(
                    "el=>getComputedStyle(el).backgroundColor"
                )
                == "rgb(240, 112, 138)"
            )
            if width == 390:
                assert form.locator("textarea").bounding_box()["x"] == 16
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        form.get_by_label("Reason (required)").fill("Available for the next person")
        shot(page, look, "confirm", width)
        with page.expect_response(lambda response: response.url.endswith("/handoff")) as response:
            form.get_by_role("button", name="Remove assignment", exact=True).click()
        assert response.value.status == 200
        page.locator(".workbench-details .fields").get_by_text(
            "Anyone eligible to work on this board can pick it up.", exact=True
        ).wait_for()
        assert page.locator('[data-action="remove-assignment"]').count() == 0
        page.locator(".activity").get_by_text(
            "Available for the next person", exact=True
        ).wait_for()
        assert fresh.owner.task_show(card["number"])["events"][-1]["kind"] == "handoff"
        shot(page, look, "after", width)
    finally:
        context.close()


@pytest.mark.parametrize("refusal", ["active", "non-owner", "origin"])
@pytest.mark.parametrize("width", [390, 1280])
def test_browser_removal_shows_server_refusal(browser, hub, fresh, refusal, width):
    hub.gateway.rate_limits = False
    card, receiver = reservation(fresh, refusal)
    if refusal == "non-owner":
        fresh.cookie = member_cookie(hub, fresh)
    context, page = open_drawer(browser, hub, fresh, card, width, "deck")
    try:
        page.get_by_role("button", name="Remove assignment", exact=True).click()
        form = page.locator('[data-form="remove-assignment"]')
        form.get_by_label("Reason (required)").fill("Remove this reservation")
        if refusal == "active":
            receiver.work.task_take_next(task=card["number"])
        before = snapshot(fresh.owner, card["number"])
        with page.expect_response(lambda response: response.url.endswith("/handoff")) as response:
            form.get_by_role("button", name="Remove assignment", exact=True).click()
        reply = response.value.json()
        assert response.value.status == (409 if refusal == "active" else 403), reply
        error = page.locator("#card-panel [data-panel-error]")
        error.wait_for()
        assert reply["error"] in error.inner_text()
        if reply.get("next_step"):
            assert reply["next_step"] in error.inner_text()
        assert snapshot(fresh.owner, card["number"]) == before
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        if refusal == "active":
            assert page.locator('[data-action="remove-assignment"]').count() == 0
            footer = page.locator(".workbench-details .ctx-foot").bounding_box()
            assert footer["y"] + footer["height"] <= page.viewport_size["height"]
        assert error.evaluate("el=>getComputedStyle(el).color") == "rgb(255, 255, 255)"
        shot(page, "deck", f"refusal-{refusal}", width)
    finally:
        context.close()
