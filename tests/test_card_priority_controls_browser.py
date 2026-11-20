"""Protected priority journeys on an empty local product with ordinary email sign-in.

Only delivery to the reserved synthetic email addresses is captured. Authentication,
memberships, card writes, provenance and optimistic concurrency use production routes.
"""

import importlib
import json
import os
import shutil
import subprocess
import threading
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from commonplace import mailer, urls, webhooks
from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.redact import redact
from commonplace.workers import WorkerSupervisor
from scripts.commonplace_release_support import Mailbox, close_handoff

CAPTURES = os.environ.get("COMMONPLACE_PRIORITY_CAPTURES")


def request(page, origin, path, method="GET", body=None, expected=200, client=None):
    headers = {"Content-Type": "application/json", "Origin": origin}
    response = (client or page.context.request).fetch(
        origin + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers=headers,
    )
    if response.status != expected:
        reason = redact(str(response.json().get("error", "")))[0]
        raise AssertionError(
            f"{method} {path}: expected {expected}, got {response.status}: {reason}"
        )
    return response.json()


def sign_in(page, origin, mailbox, email):
    page.goto(origin + "/app/")
    field = page.get_by_role("textbox", name="Email", exact=True)
    field.fill(email)
    mail_position = len(mailbox.messages)
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/app/auth/email")
    ) as sent:
        field.locator("xpath=ancestor::form").get_by_role("button").click()
    assert sent.value.status == 202
    assert page.context.request.get(origin + "/api/me").status == 401
    link = mailbox.login_link(email, origin, after=mail_position)
    page.goto(link)
    assert page.context.request.get(origin + "/api/me").status == 401
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url(origin + "/app/**")
    me = request(page, origin, "/api/me")
    assert me["email"] == email
    return me


@pytest.fixture(scope="module")
def product(tmp_path_factory):
    directory = tmp_path_factory.mktemp("priority-product")
    mailbox = Mailbox()
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("COMMONPLACE_")
    }
    environment.update(
        COMMONPLACE_SIGNUP="open",
        COMMONPLACE_ALLOWED_HOSTS="127.0.0.1",
        COMMONPLACE_APP_URL="http://127.0.0.1",
        COMMONPLACE_BASE_URL="http://127.0.0.1",
        COMMONPLACE_HANDOFF_ENABLED="off",
    )
    with ExitStack() as stack:
        stack.callback(shutil.rmtree, directory, ignore_errors=True)
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        stack.enter_context(patch.object(mailer, "from_env", return_value=mailbox))
        db = ControlDB(str(directory / "control.sqlite3"))
        stack.callback(db.close)
        supervisor = WorkerSupervisor(str(directory))
        stack.callback(supervisor.close)
        gateway = Gateway(db, supervisor)
        assert gateway.rate_limits is True
        stack.callback(close_handoff, gateway)
        stack.callback(webhooks.stop, gateway)
        server = gateway.build_server("127.0.0.1", 0)
        stack.callback(server.server_close)
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        for name in ("APP_URL", "SITE_URL", "AGENT_URL"):
            stack.enter_context(patch.object(urls, name, origin))
        stack.enter_context(
            patch.dict(os.environ, {"COMMONPLACE_APP_URL": origin, "COMMONPLACE_BASE_URL": origin})
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stack.callback(thread.join)
        stack.callback(server.shutdown)
        playwright = importlib.import_module("playwright.sync_api")
        manager = stack.enter_context(playwright.sync_playwright())
        browser = manager.chromium.launch(executable_path=shutil.which("google-chrome"))
        stack.callback(browser.close)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
        )
        stack.callback(context.close)
        errors, outbound = [], []

        def allow_local(route):
            if urlsplit(route.request.url).hostname != "127.0.0.1":
                outbound.append("Unexpected external request")
                route.abort()
            else:
                route.continue_()

        context.route("**/*", allow_local)
        context.add_init_script(
            "window.priorityCspErrors=[];document.addEventListener('securitypolicyviolation',e=>window.priorityCspErrors.push(e.violatedDirective))"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        assert page.context.request.get(origin + "/healthz").status == 200
        assert page.context.request.get(origin + "/api/boards").status == 401
        me = sign_in(page, origin, mailbox, "priority-owner@example.invalid")
        assert me["can_admin"] is True
        assert db.role(me["workspace"], me["user"]) == "owner"
        assert not db.user(me["user"])["is_admin"]
        board = request(page, origin, "/api/boards")["boards"][0]
        created = request(
            page,
            origin,
            f"/api/projects/{board['slug']}/orchestrator/connect",
            "POST",
            {"allow_shared": True},
            201,
        )
        coworker = manager.request.new_context(
            extra_http_headers={"Authorization": "Bearer " + created["token"]}
        )
        stack.callback(coworker.dispose)
        del created
        yield SimpleNamespace(
            page=page,
            origin=origin,
            board=board,
            coworker=coworker,
            browser=browser,
            mailbox=mailbox,
            errors=errors,
            outbound=outbound,
            me=me,
        )
        assert errors == []
        assert outbound == []


def create_card(product, title, **fields):
    return request(
        product.page,
        product.origin,
        f"/api/boards/{product.board['slug']}/cards",
        "POST",
        {"title": title, "assignee": product.me["actor"], **fields},
        201,
    )


def open_board(product, width=1440, look="deck"):
    page = product.page
    page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
    page.goto(f"{product.origin}/app/?ui={look}#/board/{product.board['slug']}")
    page.get_by_role("region", name="Board", exact=True).wait_for()
    page.evaluate("document.fonts.ready")
    if width < 700 and look == "deck":
        page.locator('[data-board-column="doing"]:visible').click()
    return page


def row(page, card):
    return page.locator(f'.workbench-board .card[data-card="{card["number"]}"]')


def capture(page, name):
    target = CAPTURES
    if target:
        destination = Path(target)
        destination.mkdir(parents=True, exist_ok=True)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        page.screenshot(path=str(destination / f"{head}-{name}.png"), full_page=True)


def assert_layout(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "Page overflows horizontally"
    )
    assert page.evaluate("window.priorityCspErrors") == []
    for selector in (".priority-filter select:visible", ".priority-label:visible"):
        for element in page.locator(selector).all():
            assert element.bounding_box()["width"] > 0


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_owner_priority_filter_attribution_and_undo(product, width):
    plain = create_card(product, f"Review invitation at {width}")
    person = create_card(product, f"Check reply wording at {width}", priority="normal")
    coworker = create_card(product, f"Send the draft at {width}", assignee=None)
    request(
        product.page,
        product.origin,
        f"/api/cards/{coworker['number']}",
        "PATCH",
        {
            "version": coworker["version"],
            "priority": "high",
            "priority_reason": "Blocks the invitation",
        },
        client=product.coworker,
    )
    page = open_board(product, width)
    expect = importlib.import_module("playwright.sync_api").expect
    expect(row(page, plain).locator(".priority-label")).to_have_text("Normal · Unranked")
    expect(row(page, person).locator(".priority-label")).to_have_text("Normal · Person")
    badge = row(page, coworker).locator(".priority-label")
    badge.hover()
    info = row(page, coworker).get_by_role("tooltip")
    expect(info).to_be_visible()
    expect(info).to_contain_text("Blocks the invitation")
    page.mouse.move(0, 0)
    row(page, coworker).locator(".card-more").focus()
    page.keyboard.press("Tab")
    expect(badge).to_be_focused()
    expect(info).to_be_visible()
    expect(info).to_contain_text("Coworker")
    bounds = info.bounding_box()
    assert bounds["x"] >= 0 and bounds["y"] >= 0
    assert bounds["x"] + bounds["width"] <= width
    assert bounds["y"] + bounds["height"] <= page.viewport_size["height"] - (
        80 if width < 700 else 0
    )
    assert badge.evaluate("el=>getComputedStyle(el).outlineStyle") != "none"
    capture(page, f"board-{width}-focus")
    assert_layout(page)
    priority_filter = page.locator("[data-priority-filter]:visible")
    priority_filter.select_option("unranked")
    expect(row(page, person)).to_have_count(0)
    expect(row(page, plain)).to_have_count(1)
    priority_filter.select_option("high")
    expect(row(page, plain)).to_have_count(0)
    expect(row(page, coworker)).to_have_count(1)
    priority_filter.select_option("all")
    opener = row(page, plain).locator(".priority-label")
    opener.focus()
    opener.press("Enter")
    expect(page.get_by_role("dialog", name=plain["title"])).to_be_visible()
    capture(page, f"menu-{width}")
    page.locator('[data-menu-priority="normal"]').click()
    expect(row(page, plain).locator(".priority-label")).to_have_text("Normal · Person")
    toast = page.locator(".priority-toast")
    expect(toast).to_contain_text("Undo restores Normal as your choice")
    toast.get_by_role("button", name="Undo", exact=True).click()
    expect(toast).to_contain_text("Normal priority restored as your choice")
    row(page, plain).locator(".priority-label").click()
    page.locator('[data-menu-priority="urgent"]').click()
    expect(row(page, plain).locator(".priority-label")).to_have_text("Urgent · Person")
    saved = request(page, product.origin, f"/api/cards/{plain['number']}")
    request(
        page,
        product.origin,
        f"/api/cards/{plain['number']}",
        "PATCH",
        {"version": saved["version"], "priority": "low"},
    )
    page.locator(".priority-toast").get_by_role("button", name="Undo", exact=True).click()
    expect(page.locator("[data-priority-error]")).to_contain_text("This card changed")
    assert request(page, product.origin, f"/api/cards/{plain['number']}")["priority"] == "low"
    expect(row(page, plain).locator(".priority-label")).to_have_text("Low · Person")
    capture(page, f"stale-undo-{width}")
    page.locator("[data-priority-dismiss]").click()
    row(page, coworker).locator("h3 a").click()
    expect(page.locator("#card-panel [data-priority-info]").first).to_contain_text(
        "Blocks the invitation"
    )
    capture(page, f"drawer-{width}")
    assert_layout(page)


def test_failed_write_keeps_real_previous_state_and_explains_conflict(product):
    card = create_card(product, "Keep the last accepted priority", priority="high")
    page = open_board(product)
    expect = importlib.import_module("playwright.sync_api").expect
    row(page, card).locator(".priority-label").click()
    request(
        page,
        product.origin,
        f"/api/cards/{card['number']}",
        "PATCH",
        {"version": card["version"], "title": "Keep the newer title too"},
    )
    page.locator('[data-menu-priority="low"]').click()
    expect(page.locator("[data-menu-error]")).to_contain_text("Priority not changed")
    expect(page.locator("[data-menu-error]")).to_contain_text("previous priority is unchanged")
    assert request(page, product.origin, f"/api/cards/{card['number']}")["priority"] == "high"
    assert page.locator(".priority-toast").count() == 0
    capture(page, "failed-write-1440")
    page.get_by_role("button", name="Close", exact=True).click()


def test_explicit_normal_from_coworker_normal_and_classic_details(product):
    card = create_card(product, "Owner decides normal explicitly", assignee=None)
    request(
        product.page,
        product.origin,
        f"/api/cards/{card['number']}",
        "PATCH",
        {"version": card["version"], "priority": "normal", "priority_reason": "No deadline yet"},
        client=product.coworker,
    )
    page = open_board(product, look="classic")
    expect = importlib.import_module("playwright.sync_api").expect
    row(page, card).locator("h3 a").click()
    expect(page.locator("#card-panel [data-priority-info]")).to_contain_text("No deadline yet")
    page.locator('[data-action="panel-priority"]').click()
    expect(page.locator("#card-panel [data-priority-info]")).to_contain_text("Set by You")
    assert (
        request(page, product.origin, f"/api/cards/{card['number']}")["priority_source"] == "person"
    )


def test_invited_member_reads_attribution_but_cannot_set_priority(product):
    card = create_card(product, "A member can read the decision", priority="normal")
    invite = request(
        product.page,
        product.origin,
        "/api/invites",
        "POST",
        {"email": "priority-member@example.invalid"},
        201,
    )
    with product.browser.new_context(viewport={"width": 390, "height": 844}) as context:
        context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if urlsplit(route.request.url).hostname == "127.0.0.1"
                else route.abort()
            ),
        )
        page = context.new_page()
        page.on("pageerror", lambda error: product.errors.append(str(error)))
        sign_in(page, product.origin, product.mailbox, "priority-member@example.invalid")
        page.goto(invite["link"])
        with page.expect_navigation(url=lambda url: urlsplit(url).path == "/app/"):
            page.get_by_role("button", name="Join workspace", exact=True).click()
        me = request(page, product.origin, "/api/me")
        assert me["can_admin"] is False
        page.goto(f"{product.origin}/app/?ui=deck#/board/{product.board['slug']}")
        page.locator('[data-board-column="doing"]:visible').click()
        expect = importlib.import_module("playwright.sync_api").expect
        row(page, card).locator(".priority-label").focus()
        expect(row(page, card).get_by_role("tooltip")).to_contain_text("priority-owner")
        row(page, card).locator(".card-more").click()
        assert page.locator("[data-menu-priority]").count() == 0
        expect(page.locator(".card-menu")).to_contain_text("Only the workspace owner")
        capture(page, "member-readonly-390")
        request(
            page,
            product.origin,
            f"/api/cards/{card['number']}",
            "PATCH",
            {"version": card["version"], "priority": "urgent"},
            expected=403,
        )
        assert request(page, product.origin, f"/api/cards/{card['number']}")["priority"] == "normal"
