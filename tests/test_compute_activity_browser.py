"""Real app sign-in, Activity controls and phone layout on an isolated gateway."""

import copy
import importlib
import os
import shutil
from pathlib import Path

from test_compute_activity_api import activity_hub, enrolled, request  # noqa: F401

from commonplace import sessions
from commonplace.compute.activity_collector import identity


def test_activity_real_app_states_phone_keyboard_and_lists(enrolled):  # noqa: F811
    # Browser evidence requires this runtime; missing installation is never a skip.
    sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright

    hub, machine, path, snapshot = enrolled
    template = max(snapshot["threads"], key=lambda row: row["seconds"])
    snapshot["threads"].extend(
        {**copy.deepcopy(template), "id": identity("thread", f"page-{n}")} for n in range(50)
    )
    snapshot["coverage"]["threads_total"] = snapshot["coverage"]["threads_kept"] = 53
    origin = f"http://127.0.0.1:{hub.port}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=shutil.which("google-chrome"))
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.add_init_script(
                "window.caCspViolations=[]; document.addEventListener('securitypolicyviolation',"
                "e => window.caCspViolations.push(e.violatedDirective))"
            )
            token = sessions.create_login_link(hub.db, "activity@example.test", hub.workspace)
            try:
                page.goto(origin + "/app/login?t=" + token)
                page.goto(origin + "/app/?ui=deck#/compute/activity")
            except Exception:
                raise AssertionError(
                    "Normal product sign-in failed; authentication details withheld"
                ) from None
            page.get_by_role("heading", name="Allow aggregate reporting").wait_for()
            page.get_by_role("button", name="Allow aggregates from this host").click()
            page.get_by_role("heading", name="Collector has not reported this window").wait_for()
            assert (
                request(
                    hub,
                    "POST",
                    "/api/runner/activity",
                    snapshot,
                    cookie=False,
                    token=machine["machine_token"],
                )[0]
                == 200
            )
            page.get_by_role("button", name="Refresh", exact=True).click()
            page.get_by_role("heading", name="Daily activity").wait_for()
            assert page.locator(".ca-heat-bar rect").evaluate_all(
                "rows => rows.some(el => Number(el.getAttribute('height')) > 0)"
            )
            assert "Stale snapshot" in page.locator("[data-ca-fresh]").inner_text()
            page.locator("[data-ca-tab=sinks]").click()
            page.get_by_role("heading", name="What eats time").wait_for()
            page.get_by_role("button", name="Trace", exact=True).click()
            assert page.locator("[data-ca-body] [data-ca-thread]").count() == 25
            assert page.locator("button[data-ca-thread], [data-ca-detail-title]").count() == 0
            page.get_by_role("button", name="Next", exact=True).click()
            page.get_by_text("Threads 26–50 of 53 retained", exact=False).wait_for()
            page.get_by_role("button", name="Previous", exact=True).click()
            page.get_by_text("Threads 1–25 of 53 retained", exact=False).wait_for()
            page.locator("[data-ca-model]").select_option("other")
            page.locator("[data-ca-body]").get_by_text("queued", exact=True).wait_for()
            assert page.locator("[data-ca-body]").get_by_text("queued", exact=True).count() == 1
            page.locator("[data-ca-model]").select_option("")
            page.locator("[data-ca-tab=live]").click()
            page.get_by_role("heading", name="Known active work").wait_for()
            page.get_by_role("button", name="Pause updates").click()
            assert (
                page.get_by_role("button", name="Resume updates").get_attribute("aria-pressed")
                == "true"
            )
            page.locator("[data-ca-window]").select_option("7")
            page.get_by_role("heading", name="Collector has not reported this window").wait_for()
            page.locator("[data-ca-window]").select_option("1")
            page.get_by_role("heading", name="Known active work").wait_for()
            page.route(
                "**/api/compute/activity**",
                lambda route: route.fulfill(status=503, json={"error": "activity_unavailable"}),
            )
            page.get_by_role("button", name="Refresh", exact=True).click()
            page.locator("[data-ca-problem]").get_by_text(
                "Activity could not be refreshed.", exact=False
            ).wait_for()
            assert page.get_by_role("heading", name="Known active work").count() == 1
            page.unroute("**/api/compute/activity**")
            page.locator("[data-ca-tab=recap]").click()
            page.get_by_role("heading", name="Daily activity").wait_for()
            page.locator('[data-ca-body][aria-busy="false"]').wait_for()
            for width in (1280, 390, 320):
                page.set_viewport_size({"width": width, "height": 844 if width < 600 else 800})
                page.evaluate("document.querySelector('#content').scrollTop = 0")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                assert page.locator("[data-deck=tabs] a").count() >= 4
                heights = page.locator(".compute-activity").evaluate(
                    "root => [...root.querySelectorAll('button')].filter(el => "
                    "el.getClientRects().length).map(el => el.getBoundingClientRect().height)"
                )
                assert heights and min(heights) >= 44, heights
                target = os.environ.get("ACTIVITY_SHOTS")
                if target:
                    Path(target).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(target) / f"activity-synthetic-{width}.png"))
            assert page.evaluate("window.caCspViolations") == []
            page.get_by_role("button", name="Stop aggregate access").click()
            page.get_by_role("heading", name="Allow aggregate reporting").wait_for()
        finally:
            browser.close()
