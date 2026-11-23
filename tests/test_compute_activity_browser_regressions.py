"""Real browser regressions on an owned gateway and synthetic retained data."""

import copy
import importlib
import shutil
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from test_compute_activity_api import activity_hub, enrolled, request  # noqa: F401

from commonplace import sessions
from commonplace.compute.activity_collector import identity


@pytest.fixture
def activity_page(enrolled):  # noqa: F811
    hub, machine, path, snapshot = enrolled
    template = max(snapshot["threads"], key=lambda row: row["seconds"])
    snapshot["threads"] = [
        {
            **copy.deepcopy(template),
            "id": identity("thread", f"navigation-{n}"),
            "seconds": 1000 - n,
            "model": "other" if n < 2 or 25 <= n < 38 else "gpt-6.1-sol",
            "status": "running" if n % 5 == 0 else "completed",
        }
        for n in range(130)
    ]
    snapshot["coverage"]["threads_kept"] = snapshot["coverage"]["threads_total"] = 130
    assert request(hub, "POST", path + "/consent", {"enabled": True})[0] == 200
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
    sync_playwright = importlib.import_module("playwright.sync_api").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=shutil.which("google-chrome"))
        try:
            page = browser.new_page()
            origin = "http://localhost:" + str(hub.port)
            token = sessions.create_login_link(hub.db, "activity@example.test", hub.workspace)
            try:
                page.goto(origin + "/app/login?t=" + token)
                page.goto(origin + "/app/?ui=deck#/compute/activity")
            except Exception:
                raise AssertionError(
                    "Synthetic normal sign-in failed; credentials withheld"
                ) from None
            page.get_by_role("heading", name="Daily activity", exact=True).wait_for()
            yield page
        finally:
            browser.close()


@pytest.fixture
def consent_traffic(activity_page):
    page = activity_page
    source = page.locator("[data-ca-host]").input_value()
    origin = page.url.split("/app/")[0]
    consent_url = origin + f"/api/compute/activity/{source}/consent"
    response = page.request.post(consent_url, data={"enabled": False})
    assert response.status == 200
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.get_by_role("heading", name="Allow aggregate reporting", exact=True).wait_for()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    posts, reloads, commits = [], [], []
    flags = {"hold_reload": False}
    page.evaluate("window.caConsentReloadsHeld = 0")

    def post(route):
        posts.append(
            {"route": route, "enabled": route.request.post_data_json["enabled"], "done": False}
        )

    def reload(route):
        if flags["hold_reload"]:
            reloads.append({"route": route, "done": False})
            page.evaluate("count => window.caConsentReloadsHeld = count", len(reloads))
        else:
            route.continue_()

    def commit(item):
        next_reload = len(reloads) + 1
        with page.expect_response(lambda response: response.url == consent_url) as ack:
            item["route"].continue_()
        item["done"] = True
        assert ack.value.status == 200
        assert ack.value.json() == {"enabled": item["enabled"]}
        commits.append(item["enabled"])
        assert enabled() is item["enabled"]
        if flags["hold_reload"]:
            page.wait_for_function("count => window.caConsentReloadsHeld >= count", arg=next_reload)

    def enabled():
        response = page.request.get(origin + "/api/compute/activity")
        assert response.status == 200
        return next(row["enabled"] for row in response.json()["hosts"] if row["id"] == source)

    page.route("**/api/compute/activity/*/consent", post)
    page.route("**/api/compute/activity", reload)
    try:
        yield SimpleNamespace(
            page=page,
            posts=posts,
            reloads=reloads,
            commits=commits,
            flags=flags,
            commit=commit,
            enabled=enabled,
        )
    finally:
        for item in posts + reloads:
            if not item["done"]:
                item["route"].abort("failed")
        page.unroute("**/api/compute/activity/*/consent", post)
        page.unroute("**/api/compute/activity", reload)


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_delayed_overlapping_allow_cannot_write_after_acknowledged_revoke(consent_traffic, width):
    traffic = consent_traffic
    page = traffic.page
    page.set_viewport_size({"width": width, "height": 700 if width < 600 else 844})
    with page.expect_request(
        lambda request: request.method == "POST" and request.url.endswith("/consent")
    ):
        page.locator("[data-ca-consent]").evaluate("button => { button.click(); button.click(); }")
    allow_disabled = page.locator("[data-ca-consent]").is_disabled()
    post_busy = page.locator("[data-ca-body]").get_attribute("aria-busy")
    assert traffic.enabled() is False
    first = traffic.posts[0]
    traffic.flags["hold_reload"] = True
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/compute/activity"):
        traffic.commit(first)
    allow_reload_disabled = page.locator("[data-ca-disable]").is_disabled()
    allow_reload = traffic.reloads[-1]
    traffic.flags["hold_reload"] = False
    allow_reload["route"].continue_()
    allow_reload["done"] = True
    page.get_by_role("heading", name="Daily activity", exact=True).wait_for()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert traffic.enabled() is True
    with page.expect_request(
        lambda request: request.method == "POST" and request.url.endswith("/consent")
    ):
        page.get_by_role("button", name="Stop aggregate access", exact=True).click()
    revoke_post_disabled = page.locator("[data-ca-disable]").is_disabled()
    revoke = next(item for item in traffic.posts if not item["enabled"])
    traffic.flags["hold_reload"] = True
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/compute/activity"):
        traffic.commit(revoke)
    revoke_reload_disabled = page.locator("[data-ca-disable]").is_disabled()
    revoke_reload = traffic.reloads[-1]
    traffic.flags["hold_reload"] = False
    revoke_reload["route"].continue_()
    revoke_reload["done"] = True
    page.get_by_role("heading", name="Allow aggregate reporting", exact=True).wait_for()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert traffic.enabled() is False
    for late in [item for item in traffic.posts if item["enabled"] and item is not first]:
        traffic.commit(late)
        page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert traffic.commits == [True, False], "A delayed Allow performed a real write after Revoke"
    assert traffic.enabled() is False
    assert page.get_by_role("heading", name="Allow aggregate reporting", exact=True).count() == 1
    assert allow_disabled
    assert post_busy == "true"
    assert allow_reload_disabled
    assert revoke_post_disabled
    assert revoke_reload_disabled
    assert page.locator("[data-ca-consent]").is_enabled()


@pytest.mark.parametrize("width", [1440, 390, 320])
@pytest.mark.parametrize("stage", ["POST", "reload"])
@pytest.mark.parametrize("failure", ["503", "network"])
def test_consent_failure_recovers_without_losing_acknowledged_state(
    consent_traffic, width, stage, failure
):
    traffic = consent_traffic
    page = traffic.page
    page.set_viewport_size({"width": width, "height": 700 if width < 600 else 844})
    with page.expect_request(
        lambda request: request.method == "POST" and request.url.endswith("/consent")
    ):
        page.locator("[data-ca-consent]").click()
    first = traffic.posts[0]
    if stage == "POST":
        failed = first
    else:
        traffic.flags["hold_reload"] = True
        with page.expect_request(
            lambda request: urlparse(request.url).path == "/api/compute/activity"
        ):
            traffic.commit(first)
        failed = traffic.reloads[-1]
    if failure == "503":
        failed["route"].fulfill(status=503, json={"error": "synthetic_consent_failure"})
    else:
        failed["route"].abort("failed")
    failed["done"] = True
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert traffic.enabled() is (stage == "reload")
    assert page.locator("[data-ca-disable]").is_enabled()
    traffic.flags["hold_reload"] = False
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    if stage == "POST":
        with page.expect_request(
            lambda request: request.method == "POST" and request.url.endswith("/consent")
        ):
            page.locator("[data-ca-consent]").click()
        traffic.commit(traffic.posts[-1])
        page.get_by_role("heading", name="Daily activity", exact=True).wait_for()
        page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    with page.expect_request(
        lambda request: request.method == "POST" and request.url.endswith("/consent")
    ):
        page.get_by_role("button", name="Stop aggregate access", exact=True).click()
    traffic.commit(traffic.posts[-1])
    page.get_by_role("heading", name="Allow aggregate reporting", exact=True).wait_for()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert traffic.commits == [True, False]
    assert traffic.enabled() is False
    assert page.locator("[data-ca-consent]").is_enabled()


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_filtered_trace_live_recap_trace_roundtrip_keeps_one_query(activity_page, width):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 844 if width != 320 else 700})
    page.locator("[data-ca-tab=trace]").click()
    page.locator("[data-ca-model]").select_option("other")
    page.get_by_text("Threads 1–15 of 15 retained", exact=False).wait_for()
    for tab in ("live", "recap", "trace"):
        page.locator(f".ca-tabs [data-ca-tab={tab}]").click()
        page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert page.locator("[data-ca-model]").input_value() == "other"
    assert page.locator("[data-ca-body] [data-ca-thread]").count() == 15
    assert "Threads 1–15 of 15 retained" in page.locator(".ca-page").inner_text()
    assert page.get_by_role("button", name="Next", exact=True).is_disabled()


@pytest.mark.parametrize("width", [1440, 390, 320])
@pytest.mark.parametrize("failure", ["503", "network"])
@pytest.mark.parametrize("mode", ["ack-list-failure", "ack-read-failure", "external-read-failure"])
def test_inactive_consent_evicts_cached_aggregates_before_failed_reads(
    activity_page, width, failure, mode
):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 700 if width < 600 else 844})
    page.locator("[data-ca-tab=trace]").click()
    page.locator("[data-ca-model]").select_option("gpt-6.1-sol")
    page.get_by_text("Threads 1–25 of 115 retained", exact=False).wait_for()
    page.get_by_role("button", name="Next", exact=True).click()
    page.get_by_text("Threads 26–50 of 115 retained", exact=False).wait_for()
    source = page.locator("[data-ca-host]").input_value()
    consent_url = page.url.split("/app/")[0] + f"/api/compute/activity/{source}/consent"
    waiting = []

    def should_fail(request):
        if request.method != "GET":
            return False
        path = urlparse(request.url).path
        return path == (
            "/api/compute/activity"
            if mode == "ack-list-failure"
            else f"/api/compute/activity/{source}"
        )

    def read(route):
        if should_fail(route.request):
            waiting.append(route)
        else:
            route.continue_()

    if mode == "external-read-failure":
        response = page.request.post(consent_url, data={"enabled": False})
        assert response.status == 200
        assert response.json() == {"enabled": False}
    page.route("**/api/compute/activity**", read)
    with page.expect_request(should_fail):
        if mode == "external-read-failure":
            page.get_by_role("button", name="Refresh", exact=True).click()
        else:
            with page.expect_response(lambda response: response.url == consent_url) as ack:
                page.get_by_role("button", name="Stop aggregate access", exact=True).click()
            assert ack.value.status == 200
            assert ack.value.json() == {"enabled": False}
    page.locator('[data-ca-body][aria-busy="true"]').wait_for()
    assert waiting
    try:
        assert page.locator("[data-ca-body] [data-ca-thread]").count() == 0
        assert page.locator("[data-ca-fresh]").inner_text() == "No aggregate snapshot available."
        assert page.get_by_role("button", name="Stop aggregate access", exact=True).is_hidden()
    finally:
        if failure == "503":
            waiting[0].fulfill(status=503, json={"error": "synthetic_read_failure"})
        else:
            waiting[0].abort("failed")
        page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert page.get_by_role("heading", name="Activity is unavailable", exact=True).count() == 1
    assert page.locator("[data-ca-body] [data-ca-thread]").count() == 0
    assert page.locator("[data-ca-fresh]").inner_text() == "No aggregate snapshot available."
    assert "Retained values below" not in page.locator("[data-ca-problem]").inner_text()
    page.unroute("**/api/compute/activity**", read)
    response = page.request.post(consent_url, data={"enabled": True})
    assert response.status == 200
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.get_by_text("Threads 1–25 of 130 retained", exact=False).wait_for()
    assert page.locator("[data-ca-model]").input_value() == ""
    assert page.locator("[data-ca-body] [data-ca-thread]").count() == 25


@pytest.mark.parametrize("width", [1440, 390, 320])
@pytest.mark.parametrize("failure", ["503", "network"])
def test_active_consent_keeps_same_source_offline_snapshot_and_query(activity_page, width, failure):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 700 if width < 600 else 844})
    page.locator("[data-ca-tab=trace]").click()
    page.locator("[data-ca-model]").select_option("gpt-6.1-sol")
    page.get_by_text("Threads 1–25 of 115 retained", exact=False).wait_for()
    page.get_by_role("button", name="Next", exact=True).click()
    page.get_by_text("Threads 26–50 of 115 retained", exact=False).wait_for()
    source = page.locator("[data-ca-host]").input_value()
    first = page.locator("[data-ca-body] [data-ca-thread]").first.get_attribute("data-ca-thread")
    stamp = page.locator("[data-ca-fresh]").inner_text()

    def offline(route):
        if failure == "503":
            route.fulfill(status=503, json={"error": "synthetic_offline"})
        else:
            route.abort("failed")

    page.route("**/api/compute/activity**", offline)
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert page.locator("[data-ca-host]").input_value() == source
    assert "Threads 26–50 of 115 retained" in page.locator(".ca-page").inner_text()
    assert page.locator("[data-ca-model]").input_value() == "gpt-6.1-sol"
    assert (
        page.locator("[data-ca-body] [data-ca-thread]").first.get_attribute("data-ca-thread")
        == first
    )
    assert page.locator("[data-ca-fresh]").inner_text() == stamp
    assert "Retained values below may be stale" in page.locator("[data-ca-problem]").inner_text()
    assert page.get_by_role("button", name="Stop aggregate access", exact=True).is_visible()


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_keyboard_paging_keeps_visible_focus_and_announces_range(activity_page, width):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 844})
    page.locator("[data-ca-tab=trace]").click()
    page.get_by_text("Threads 1–25 of 130 retained", exact=False).wait_for()
    page.get_by_role("button", name="Next", exact=True).focus()
    page.keyboard.press("Enter")
    page.get_by_text("Threads 26–50 of 130 retained", exact=False).wait_for()
    assert page.locator("[data-ca-next]").evaluate("el => document.activeElement === el")
    assert page.locator("[data-ca-next]").evaluate("el => el.matches(':focus-visible')")
    assert page.locator('.ca-page [role="status"]').count() == 1


@pytest.mark.parametrize("width", [390, 320])
def test_phone_keeps_activity_heading_and_first_trace_result_visible(activity_page, width):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 700})
    heading = page.locator(".compute-activity h1")
    assert heading.is_visible()
    assert 'heading "Activity" [level=1]' in page.locator(".compute-activity").aria_snapshot()
    page.locator("[data-ca-tab=trace]").click()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    first = page.locator("[data-ca-body] [data-ca-thread]").first
    box = first.bounding_box()
    assert box and 0 <= box["y"] < 656


@pytest.mark.parametrize("width", [390, 320])
def test_phone_preserves_every_table_column_header(activity_page, width):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 844})
    for tab in ("recap", "sinks", "trace", "live"):
        page.locator(f".ca-tabs [data-ca-tab={tab}]").click()
        page.locator('[data-ca-body][aria-busy="false"]').wait_for()
        count = page.locator(".compute-activity thead th").count()
        assert count
        assert page.locator(".compute-activity").get_by_role("columnheader").count() == count
        assert page.locator(".compute-activity tbody td:not([colspan])").evaluate_all(
            "cells => cells.every(cell => { const refs = (cell.getAttribute('headers') || '').split(' ').map(id => document.getElementById(id)); return refs.length === 2 && refs.every(Boolean) && refs.some(el => el.scope === 'col') && refs.some(el => el.scope === 'row'); })"
        )


def test_global_sinks_and_trace_refresh_keep_independent_scopes(activity_page):
    page = activity_page

    def tab(name):
        page.locator(f".ca-tabs [data-ca-tab={name}]").click()
        page.locator('[data-ca-body][aria-busy="false"]').wait_for()

    def leader():
        return (
            page.get_by_role("heading", name="Slowest threads and cards", exact=True)
            .locator("..")
            .locator("[data-ca-thread]")
            .first.get_attribute("data-ca-thread")
        )

    tab("sinks")
    baseline = leader()
    assert baseline
    tab("trace")
    page.get_by_role("button", name="Next", exact=True).click()
    page.get_by_text("Threads 26–50 of 130 retained", exact=False).wait_for()
    tab("sinks")
    assert leader() == baseline
    tab("trace")
    page.locator("[data-ca-model]").select_option("other")
    page.get_by_text("Threads 1–15 of 15 retained", exact=False).wait_for()
    tab("sinks")
    assert leader() == baseline
    tab("recap")
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    tab("trace")
    assert page.locator("[data-ca-model]").input_value() == "other"
    assert page.locator("[data-ca-body] [data-ca-thread]").count() == 15
    assert "Threads 1–15 of 15 retained" in page.locator(".ca-page").inner_text()


@pytest.mark.parametrize("width", [390, 320])
def test_phone_first_summary_is_above_fixed_navigation(activity_page, width):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 700})
    page.locator("[data-ca-tab=trace]").click()
    page.locator("[data-ca-model]").select_option("other")
    page.get_by_text("Threads 1–15 of 15 retained", exact=False).wait_for()
    first = page.locator("[data-ca-body] [data-ca-thread]").first.bounding_box()
    footer = page.locator("[data-deck=tabs]").bounding_box()
    assert first and footer
    assert first["y"] + first["height"] <= footer["y"]


@pytest.mark.parametrize("width", [1440, 390, 320])
@pytest.mark.parametrize("failure", ["503", "network"])
def test_automatic_source_replacement_drops_old_snapshot_and_paging(activity_page, width, failure):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 700 if width < 600 else 844})
    page.locator("[data-ca-tab=trace]").click()
    page.locator("[data-ca-model]").select_option("gpt-6.1-sol")
    page.get_by_text("Threads 1–25 of 115 retained", exact=False).wait_for()
    page.get_by_role("button", name="Next", exact=True).click()
    page.get_by_text("Threads 26–50 of 115 retained", exact=False).wait_for()
    original = page.locator("[data-ca-host]").input_value()
    old_thread = page.locator("[data-ca-body] [data-ca-thread]").first.get_attribute(
        "data-ca-thread"
    )
    response = page.request.get(
        page.url.split("/app/")[0] + f"/api/compute/activity/{original}?window=1"
    )
    assert response.status == 200
    replacement = identity("machine", "replacement")
    new_thread = identity("thread", "replacement")
    retained = response.json()
    retained["snapshot"]["threads"] = [
        {**retained["snapshot"]["threads"][0], "id": new_thread, "model": "other"}
    ]
    retained["thread_models"] = ["other"]
    retained["page"] = {"offset": 0, "total": 1, "next": None}
    failed = True
    reads = []
    waiting = []

    def read(route):
        reads.append(route.request.url)
        if not failed:
            route.fulfill(json=retained)
        else:
            waiting.append(route)

    page.route(
        "**/api/compute/activity",
        lambda route: route.fulfill(json={"hosts": [{"id": replacement, "enabled": True}]}),
    )
    page.route("**/api/compute/activity/**", read)
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.wait_for_function(
        "id => document.querySelector('[data-ca-host]').value === id", arg=replacement
    )
    assert waiting
    assert page.locator('[data-ca-body][aria-busy="true"]').count() == 1
    assert page.locator("[data-ca-body] [data-ca-thread]").count() == 0
    assert page.locator("[data-ca-fresh]").inner_text() == "No aggregate snapshot available."
    if failure == "503":
        waiting[0].fulfill(status=503, json={"error": "synthetic_read_failure"})
    else:
        waiting[0].abort("failed")
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert page.locator("[data-ca-host]").input_value() == replacement
    assert page.get_by_role("heading", name="Activity is unavailable", exact=True).count() == 1
    assert page.locator("[data-ca-body] [data-ca-thread]").count() == 0
    assert page.locator("[data-ca-fresh]").inner_text() == "No aggregate snapshot available."
    assert reads
    query = parse_qs(urlparse(reads[-1]).query, keep_blank_values=True)
    assert query["offset"] == ["0"]
    assert query["model"] == [""]
    failed = False
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.get_by_text("Threads 1–1 of 1 retained", exact=False).wait_for()
    assert page.locator("[data-ca-model]").input_value() == ""
    assert (
        page.locator("[data-ca-body] [data-ca-thread]").get_attribute("data-ca-thread")
        == new_thread
    )
    assert page.locator(f'[data-ca-thread="{old_thread}"]').count() == 0
    assert page.get_by_role("button", name="Next", exact=True).is_disabled()
