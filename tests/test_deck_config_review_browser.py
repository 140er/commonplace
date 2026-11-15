"""Optional real-browser proof of the Deck Coworker config review spacing and result copy.

Fixture-served: the real projects.js module mounts into a page that loads the shipped Deck styles, so
no hub and no timing is involved."""

import json
import mimetypes
from urllib.parse import urlsplit

import pytest

from tests.test_deck_board_browser import STATIC, browser  # noqa: F401

STYLES = (
    "app screens map mix ui decide decision-blocks rules console chat workbench chatdock bots "
    "topology topology-detail deck deck-bots deck-capy deck-settings-capy deck-coworkers "
    "deck-board deck-art deck-shell"
).split()
PAGE = (
    '<!doctype html><html lang="en" data-ui="deck"><head><meta charset="utf-8">'
    + "".join(f'<link rel="stylesheet" href="/app/{name}.css">' for name in STYLES)
    + '</head><body><div id="root" class="shell"><section class="workbench-project">'
    '<div class="settings"><div class="settings-inner"><section class="set-sec">'
    "<div data-project-review></div></section></div></div></section></div>"
    """<script type="module">
import {mountProjectReview} from '/app/projects.js';
const e = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let version = {version: 2, ref: 'v2', status: 'proposed', proposed_by: 'user:author', sha256: 'b'.repeat(64)};
const api = async (path, options = {}) => {
  if (path === '/api/me') return {actor: 'user:owner', people: {'user:author': 'Config author'}};
  if (options.method === 'POST') { version = {...version, status: 'rejected', decided_by: 'user:owner'}; return version; }
  if (path.endsWith('/configs')) return {accepted: {version: 1}, versions: [version]};
  if (path.includes('/diff?')) return {diff: '-Old prompt\\n+New prompt'};
  if (path.endsWith('/cards')) return {cards: []};
  throw new Error(path);
};
await mountProjectReview(document.querySelector('[data-project-review]'), {project: {board: 'ledger', can_configure: true}, api, e});
window.ready = true;
</script></body></html>"""
)
GAP = """() => {
  const row = document.querySelector('[data-config-version]');
  const details = row.querySelector('details');
  const above = details.open ? row.querySelector('[data-config-diff]') : row.querySelector('summary');
  const accept = row.querySelector('[data-config-accept]');
  return accept.getBoundingClientRect().top - above.getBoundingClientRect().bottom;
}"""


def open_review(page):
    def serve(route):
        path = urlsplit(route.request.url).path
        if path == "/probe":
            route.fulfill(body=PAGE, content_type="text/html")
            return
        file = STATIC / path.removeprefix("/app/")
        if file.is_file():
            route.fulfill(
                body=file.read_bytes(),
                content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
            )
        else:
            route.fulfill(status=404, body=json.dumps({}))

    page.route("**/*", serve)
    page.goto("http://example.test/probe")
    page.wait_for_function("window.ready === true")
    page.evaluate("document.fonts.ready")


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_accept_sits_eight_pixels_below_changes_open_or_collapsed(browser, size):  # noqa: F811
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    open_review(page)
    assert page.evaluate(GAP) == 8, "collapsed Changes leaves no 8px gap above Accept"
    page.click("[data-config-version] summary")
    assert page.evaluate(GAP) == 8, "open Changes leaves no 8px gap above Accept"


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_rejecting_a_version_leaves_no_repeated_result_line(browser, size):  # noqa: F811
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    open_review(page)
    page.click("[data-config-reject]")
    page.click("[data-config-confirm-reject]")
    page.wait_for_function("!document.querySelector('[data-config-reject]')")
    row = page.text_content("[data-config-version]")
    assert "Rejected by you" in row
    assert page.locator("[data-project-review-status]").inner_text().strip() == ""
    assert not page.locator("[data-project-review-status]").is_visible()
    assert "Rejected v2" not in page.inner_text("[data-project-review]")
