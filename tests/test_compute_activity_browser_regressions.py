"""Real browser regressions on an owned gateway and synthetic retained data."""

import copy
import importlib
import shutil

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


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_refresh_open_trace_after_report_replacement_keeps_one_snapshot(
    activity_page,
    enrolled,  # noqa: F811
    width,
):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 844})
    hub, machine, path, snapshot = enrolled
    page.locator("[data-ca-tab=trace]").click()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    first = page.locator("[data-ca-body] [data-ca-thread]").first
    thread_id = first.get_attribute("data-ca-thread")
    first.click()
    page.locator("[data-ca-detail-title]").wait_for()
    assert "0.28 h agent time" in page.locator("[data-ca-body]").inner_text()
    previous_provenance = page.locator("[data-ca-fresh]").inner_text()

    newer = copy.deepcopy(snapshot)
    newer["collected_at"] += 60
    updated = next(row for row in newer["threads"] if row["id"] == thread_id)
    updated["seconds"] = 3600
    updated["phases"] = {"unknown": 3600, "tool": 0, "wait": 0, "compaction": 0}
    updated["buckets"] = []
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            newer,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 200
    )
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert page.locator("[data-ca-fresh]").inner_text() != previous_provenance
    assert page.locator("[data-ca-detail-title]").count() == 0
    assert "0.28 h agent time" not in page.locator("[data-ca-body]").inner_text()
    page.locator(f'[data-ca-thread="{thread_id}"]').click()
    page.locator("[data-ca-detail-title]").wait_for()
    assert "1.00 h agent time" in page.locator("[data-ca-body]").inner_text()


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_replacement_before_trace_open_uses_detail_provenance(
    activity_page,
    enrolled,  # noqa: F811
    width,
):
    page = activity_page
    page.set_viewport_size({"width": width, "height": 844})
    hub, machine, _, snapshot = enrolled
    page.locator("[data-ca-tab=trace]").click()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    original_provenance = page.locator("[data-ca-fresh]").inner_text()
    thread_id = page.locator("[data-ca-body] [data-ca-thread]").first.get_attribute(
        "data-ca-thread"
    )

    newer = copy.deepcopy(snapshot)
    for key in ("collected_at", "window_start", "window_end"):
        newer[key] += 60
    newer["coverage"]["partial"] = True
    for thread in newer["threads"]:
        for bucket in thread["buckets"]:
            bucket["at"] += 60
    updated = next(row for row in newer["threads"] if row["id"] == thread_id)
    updated["seconds"] = 7200
    updated["phases"] = {"unknown": 7200, "tool": 0, "wait": 0, "compaction": 0}
    updated["buckets"] = []
    assert (
        request(
            hub, "POST", "/api/runner/activity", newer, cookie=False, token=machine["machine_token"]
        )[0]
        == 200
    )

    page.locator(f'[data-ca-thread="{thread_id}"]').click()
    page.locator("[data-ca-detail-title]").wait_for()
    assert "2.00 h agent time" in page.locator("[data-ca-body]").inner_text()
    expected = page.evaluate(
        "values => values.map(n => new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Amsterdam', dateStyle: 'medium', timeStyle: 'short' }).format(n * 1000))",
        [newer[key] for key in ("collected_at", "window_start", "window_end")],
    )
    detail_provenance = page.locator("[data-ca-fresh]").inner_text()
    assert all(value in detail_provenance for value in expected)
    assert "Coverage for this trace snapshot is unavailable" in detail_provenance
    page.get_by_role("button", name="Back to threads", exact=True).click()
    assert page.locator("[data-ca-fresh]").inner_text() == original_provenance
    assert page.locator(f'[data-ca-thread="{thread_id}"]').evaluate(
        "el => document.activeElement === el"
    )
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.locator('[data-ca-body][aria-busy="false"]').wait_for()
    assert "PARTIAL" in page.locator("[data-ca-fresh]").inner_text()
    page.locator(f'[data-ca-thread="{thread_id}"]').click()
    page.locator("[data-ca-detail-title]").wait_for()
    assert all(value in page.locator("[data-ca-fresh]").inner_text() for value in expected)
    assert "PARTIAL" in page.locator("[data-ca-fresh]").inner_text()
