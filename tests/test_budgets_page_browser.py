"""The Settings link and the spend and budgets page in a real browser, at desktop and phone widths."""

import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_deck_settings_browser import STATIC, browser as browser_fixture, open_settings

browser = browser_fixture
WINDOW = {
    "start": "2026-11-01T00:00:00Z",
    "end": "2026-12-01T00:00:00Z",
    "window": "calendar_month",
    "days": None,
}


def group(**extra):
    return {
        "runs": 4,
        "cost_known_runs": 3,
        "unknown_cost_runs": 1,
        "token_unreported_runs": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_micros": {"USD": 42_000_000},
        **extra,
    }


COSTS = {
    "window": {**WINDOW, "custom": False},
    "scope": "workspace",
    "workspace": group(runs=6, cost_micros={"USD": 45_500_000, "EUR": 3_000_000}),
    "by_agent": [group(agent="conn_a1")],
    "by_project": [group(project="demo")],
    "by_board": [],
    "by_card": [],
    "unknown_cost": {"runs": 2, "listed": []},
}
BUDGET = {
    "id": "bud_1",
    "scope": {"kind": "project", "key": "demo"},
    "limit_micros": 50_000_000,
    "currency": "USD",
    "unknown_runs_warn": None,
    "window": WINDOW,
    "spent_micros": 42_000_000,
    "percent": 84,
    "state": "warning",
    "unknown_cost_runs": 1,
    "unknown_runs_high": False,
    "other_currency_runs": 0,
    "running_runs": 0,
    "marks": ["warning"],
    "updated_at": "2026-11-18T10:00:00Z",
}
OWNER = {
    "/api/costs": (200, COSTS),
    "/api/settings/budgets": (200, {"window": WINDOW, "budgets": [BUDGET]}),
}


def shot(page, name):
    target = os.environ.get("BUDGET_SHOTS_DIR")
    if target:
        page.screenshot(path=str(Path(target) / f"{name}.png"), full_page=True)


def open_budgets(browser, width, answers, *, put=(200, BUDGET)):
    context = browser.new_context(
        viewport={"width": width, "height": 844 if width == 390 else 800},
        device_scale_factor=2,
        timezone_id="UTC",
    )
    page = context.new_page()
    page.set_default_timeout(10000)
    writes = []

    def serve(route):
        request = route.request
        path = urlsplit(request.url).path
        if path == "/api/settings/budgets" and request.method in ("PUT", "DELETE"):
            writes.append((request.method, urlsplit(request.url).query, request.post_data))
            status, body = put
            route.fulfill(status=status, body=json.dumps(body), content_type="application/json")
        elif path in answers and request.method == "GET":
            status, body = answers[path]
            route.fulfill(status=status, body=json.dumps(body), content_type="application/json")
        elif path.startswith("/api/"):
            route.fulfill(status=404, body="{}", content_type="application/json")
        else:
            name = path.removeprefix("/app/")
            file = STATIC / name
            route.fulfill(
                status=200 if file.is_file() else 404,
                body=file.read_bytes() if file.is_file() else b"",
                content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            )

    page.route("**/*", serve)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://example.test/app/budgets.html")
    page.wait_for_selector(".budget-section")
    page.evaluate("document.fonts.ready")
    return context, page, writes, errors


def no_sideways_scroll(page):
    return page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("look", ["deck", "classic"])
def test_settings_links_to_the_budgets_page(browser, width, look):
    context, page = open_settings(browser, width, look=look)
    try:
        link = page.get_by_role("link", name="Open spend and budgets")
        link.scroll_into_view_if_needed()
        assert link.is_visible()
        assert link.get_attribute("href") == "/app/budgets.html"
        assert "Only the workspace owner sets budgets" in page.locator("#budgets-link").inner_text()
        shot(page, f"settings-{look}-{width}")
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_owner_sees_spend_badges_and_sets_a_budget_after_confirming(browser, width):
    context, page, writes, errors = open_budgets(browser, width, OWNER)
    try:
        body = page.locator("main").inner_text()
        assert "Window: November 2026 (UTC)." in body
        assert "3.00 EUR · 45.50 USD" in body and "1 run without a cost, not counted" in body
        assert "Warning · 84%" in body
        assert no_sideways_scroll(page)
        shot(page, f"budgets-{width}")
        page.get_by_role("button", name="Set a budget").first.click()
        page.get_by_label("Limit for November 2026 (UTC)").fill("100")
        page.get_by_label("Currency").fill("usd")
        page.get_by_role("button", name="Review budget").click()
        assert (
            "New starts are refused; running work finishes."
            in page.locator("[data-budget-confirm]").inner_text()
        )
        assert writes == [], "nothing is written before the confirm"
        shot(page, f"budgets-confirm-{width}")
        assert no_sideways_scroll(page)
        page.get_by_role("button", name="Confirm and save").click()
        page.wait_for_selector("[data-budget-notice]")
        assert page.locator("[data-budget-notice]").inner_text() == "Budget saved."
        assert [(method, json.loads(sent)) for method, _, sent in writes] == [
            (
                "PUT",
                {
                    "scope": {"kind": "workspace"},
                    "limit_micros": 100_000_000,
                    "currency": "USD",
                    "unknown_runs_warn": None,
                },
            )
        ]
        assert errors == []
    finally:
        context.close()


def test_a_failed_write_keeps_the_old_budget_and_says_so(browser):
    context, page, writes, _ = open_budgets(
        browser, 390, OWNER, put=(500, {"error": "budget request failed"})
    )
    try:
        page.get_by_role("button", name="Change budget").click()
        page.get_by_label("Limit for November 2026 (UTC)").fill("75")
        page.get_by_role("button", name="Review budget").click()
        page.get_by_role("button", name="Confirm and save").click()
        page.wait_for_selector("[role=alert]")
        alert = page.locator("[role=alert]").inner_text()
        assert "Not saved." in alert and "The budget is unchanged." in alert
        assert "42.00 of 50.00 USD spent" in page.locator("main").inner_text()
        assert len(writes) == 1
        shot(page, "budgets-failed-390")
    finally:
        context.close()


def test_clearing_needs_a_confirm(browser):
    context, page, writes, _ = open_budgets(browser, 390, OWNER, put=(200, {"removed": "bud_1"}))
    try:
        page.get_by_role("button", name="Clear budget").click()
        assert "Clear this budget?" in page.locator("[data-budget-confirm]").inner_text()
        assert writes == []
        page.get_by_role("button", name="Confirm and clear").click()
        page.wait_for_selector("[data-budget-notice]")
        assert [(m, q) for m, q, _ in writes] == [("DELETE", "kind=project&key=demo")]
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_a_member_reads_spend_and_gets_no_editor(browser, width):
    answers = {
        "/api/costs": (200, {**COSTS, "scope": "readable"}),
        "/api/settings/budgets": (404, {"error": "not found"}),
    }
    context, page, writes, _ = open_budgets(browser, width, answers)
    try:
        body = page.locator("main").inner_text()
        assert "Only the workspace owner can see budgets." in body and "45.50 USD" in body
        assert page.locator("main button, main input, main select, main form").count() == 0
        assert writes == [] and no_sideways_scroll(page)
        shot(page, f"budgets-member-{width}")
    finally:
        context.close()


def test_a_failed_read_is_not_reported_as_signed_out(browser):
    answers = {
        "/api/costs": (503, {"error": "busy"}),
        "/api/settings/budgets": (503, {"error": "busy"}),
    }
    context, page, _, _ = open_budgets(browser, 390, answers)
    try:
        text = page.locator("main").inner_text()
        assert "Could not read the spend." in text and "does not mean you are signed out" in text
        assert "Sign in to see this" not in text
        assert page.get_by_role("button", name="Try again").first.is_visible()
        shot(page, "budgets-failed-read-390")
    finally:
        context.close()


def test_signed_out_says_so_and_offers_sign_in(browser):
    answers = {
        "/api/costs": (401, {"error": "Sign in"}),
        "/api/settings/budgets": (401, {"error": "Sign in"}),
    }
    context, page, _, _ = open_budgets(browser, 390, answers)
    try:
        assert page.get_by_role("heading", name="Sign in to see this").is_visible()
        assert page.get_by_role("link", name="Sign in").get_attribute("href") == "/app/"
    finally:
        context.close()
