"""The Pipeline view in a real browser over the example fixture: layout at desktop and phone size,
phone stacking, the empty state, reduced motion. Skips without Playwright or Chrome.
Set PIPELINE_SHOTS to a directory to also write screenshots."""

import json
import mimetypes
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"
FIXTURE = json.loads(Path(__file__).with_name("pipeline_fixture.json").read_text())
STYLES = ["app", "screens", "map", "mix", "ui", "deck", "deck-capy", "deck-art", "deck-shell"]


def mount(page, answer, route="#/pipeline"):
    html = "<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
    html += "".join(f'<link rel="stylesheet" href="/app/{name}.css">' for name in STYLES)
    html += """<div id="root"><div class="shell two" data-view="page"><main class="center">
        <div id="phone-bar"></div><header class="head"></header><div id="content"></div>
        <div id="phone-tabs"></div></main></div></div><script type="module">
        import { createDeck, applyDeck } from '/app/deck.js';
        applyDeck();
        const answer = ANSWER;
        const api = async path => {
          if (path.startsWith('/api/pipeline/prs')) return answer;
          return {};
        };
        const ui = createDeck({root:document.querySelector('#root'),api,
          context:() => ({me:{workspace:'demo',actor:'demo',can_admin:true,role:'owner'},boards:[],needs:{},go(){}}),title(){}});
        await ui.show(ROUTE);
        await document.fonts.ready;
        window.ready = true;
        </script>""".replace("ANSWER", json.dumps(answer)).replace("ROUTE", json.dumps(route))

    def serve(request):
        path = urlsplit(request.request.url).path
        if path == "/":
            request.fulfill(body=html, content_type="text/html")
        else:
            file = STATIC / path.removeprefix("/app/")
            if file.is_file():
                request.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                )
            else:
                request.fulfill(status=404, body="")

    page.route("**/*", serve)
    page.goto("http://example.test/?ui=deck" + route)
    page.wait_for_function("window.ready === true")
    page.wait_for_function("document.querySelector('link[data-pipeline-style]')?.sheet")
    page.wait_for_selector(".pl-pr, .pl-empty", timeout=5000)


def browser_page(pw, width, height=900, **options):
    browser = pw.chromium.launch(executable_path=shutil.which("google-chrome"))
    return browser, browser.new_page(
        viewport={"width": width, "height": height}, device_scale_factor=2, **options
    )


def shot(page, name):
    folder = os.environ.get("PIPELINE_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=True)


def boxes(page, selector):
    return page.evaluate(
        """selector => [...document.querySelectorAll(selector)].map(el => {
          const r = el.getBoundingClientRect(); return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, width:r.width};
        })""",
        selector,
    )


@pytest.fixture(scope="module")
def pw():
    sync = pytest.importorskip("playwright.sync_api")
    if not shutil.which("google-chrome"):
        pytest.skip("no Chrome")
    with sync.sync_playwright() as playwright:
        yield playwright


def test_desktop_draws_connected_blocks_in_a_wrapping_canvas(pw):
    browser, page = browser_page(pw, 1280)
    try:
        mount(page, FIXTURE)
        scroll = page.evaluate("document.documentElement.scrollWidth")
        assert scroll == 1280
        first = page.locator(".pl-pr").first
        cells = boxes(page, ".pl-pr:first-of-type .pl-cell")
        assert len(cells) == 11
        tops = sorted({round(c["top"]) for c in cells})
        assert 2 <= len(tops) <= 4, tops  # a few rows, left to right then down
        row = [c for c in cells if round(c["top"]) == tops[0]]
        assert [c["left"] for c in row] == sorted(c["left"] for c in row)
        wires = boxes(page, ".pl-pr:first-of-type .pl-wire")
        blocks = boxes(page, ".pl-pr:first-of-type .pl-block")
        for block, wire in zip(blocks, wires):
            if abs(block["top"] - wire["top"]) < 80 and wire["left"] > block["left"]:
                assert (
                    wire["left"] >= block["right"] - 0.5
                )  # the connector starts where its block ends
        assert first.locator("summary").bounding_box()["height"] >= 44
        for pill in boxes(page, ".pl-wait"):
            assert pill["right"] - pill["left"] <= 76, pill
        shot(page, "desktop-1280")
    finally:
        browser.close()


def test_phone_stacks_every_block_and_connector_vertically(pw):
    browser, page = browser_page(pw, 390, 844)
    try:
        mount(page, FIXTURE)
        assert page.evaluate("document.documentElement.scrollWidth") == 390
        blocks = boxes(page, ".pl-pr:first-of-type .pl-block")
        assert len(blocks) == 11
        assert len({round(b["left"]) for b in blocks}) == 1, "blocks share one column"
        assert all(b["right"] <= 390 - 8 for b in blocks)
        assert [b["top"] for b in blocks] == sorted(b["top"] for b in blocks)
        assert all(a["bottom"] <= b["top"] for a, b in zip(blocks, blocks[1:]))
        for wait in boxes(page, ".pl-pr:first-of-type .pl-wait"):
            assert wait["left"] >= 0 and wait["right"] <= 390, wait
        shot(page, "phone-390")
    finally:
        browser.close()


def test_empty_state_explains_where_data_comes_from(pw):
    browser, page = browser_page(pw, 390, 844)
    try:
        mount(page, {"demo": False, "prs": []})
        text = page.locator(".pl-empty").inner_text()
        assert "Pipeline events come from the deploy flow" in text
        assert page.locator(".pl-empty .cp-art svg").count() == 1
        assert page.evaluate("document.documentElement.scrollWidth") == 390
        shot(page, "empty-phone-390")
    finally:
        browser.close()


def test_reduced_motion_stops_the_pulse(pw):
    browser, page = browser_page(pw, 1280, reduced_motion="reduce")
    try:
        mount(page, FIXTURE)
        names = page.evaluate(
            """() => [...document.querySelectorAll('.pl-mark[data-state="running"] circle:last-child')]
              .map(el => getComputedStyle(el).animationName)"""
        )
        assert names and set(names) == {"none"}, names
    finally:
        browser.close()
    browser, page = browser_page(pw, 1280, reduced_motion="no-preference")
    try:
        mount(page, FIXTURE)
        names = page.evaluate(
            """() => [...document.querySelectorAll('.pl-mark[data-state="running"] circle:last-child')]
              .map(el => getComputedStyle(el).animationName)"""
        )
        assert set(names) == {"pl-pulse"}, names
    finally:
        browser.close()


def test_the_example_banner_and_pr_header_stats_fit_the_phone(pw):
    browser, page = browser_page(pw, 390, 844)
    try:
        mount(page, FIXTURE)
        for stat in boxes(page, ".pl-stat"):
            assert stat["left"] >= 0 and stat["right"] <= 390, stat
    finally:
        browser.close()
