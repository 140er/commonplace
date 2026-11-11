"""A board refresh never replaces a form the person has open in the card panel.

Each case drives the real app: open a card, open a form in its panel, let a background
refresh run (another card changes, so the event feed wakes the board), and read the panel.
"""

from tests.workbench_e2e.scenarios import settle
from tests.workbench_e2e.seed import request

CARD = "A-131"
OTHER = "A-132"
TYPED = "Please take this over, the copy is ready"


def _wait(page, predicate, timeout=8000):
    """Poll through Playwright, which only delivers request events while it is called."""
    waited = 0
    while not predicate() and waited < timeout:
        page.wait_for_timeout(50)
        waited += 50
    return predicate()


def _refresh_probe(page):
    """Count needs-me reads: every refresh reads them once."""
    seen = []
    page.on("request", lambda req: seen.append(req.url) if "/api/needs-me" in req.url else None)
    return seen


def _wake(seed, text="refresh me"):
    """Change another card so the open page's event feed triggers a background refresh."""
    status, _body, _raw = request(
        seed, "POST", f"/api/cards/{seed.number(OTHER)}/comments", {"text": text}
    )
    assert status == 200, status


def _refreshed(page, seen, before):
    assert _wait(page, lambda: len(seen) > before), "no background refresh ran"
    settle(page, 600)


def _open_card(ui, seed):
    page = ui.open(f"#/card/{seed.number(CARD)}")
    page.wait_for_selector('#card-panel [data-action="handoff"]')
    # Let the opening poll finish so only the refresh a case triggers runs afterwards.
    settle(page, 2500)
    return page


def _handoff_text(page):
    return page.evaluate(
        "() => { const f = document.querySelector('#card-panel form[data-form=\"handoff\"]');"
        " return f ? f.elements.note.value : null; }"
    )


def test_open_handoff_form_survives_refresh(ui, app):
    page = _open_card(ui, app)
    seen = _refresh_probe(page)
    page.click('#card-panel [data-action="handoff"]')
    page.fill('#card-panel form[data-form="handoff"] [name="note"]', TYPED)
    before = len(seen)
    _wake(app)
    _refreshed(page, seen, before)
    assert _handoff_text(page) == TYPED


def test_handoff_form_opened_while_card_fetch_is_in_flight_survives(ui, app):
    """The refresh already asked for the card; the form opens before the answer arrives."""
    page = _open_card(ui, app)
    seen = _refresh_probe(page)
    held = []
    page.route(f"**/api/cards/{app.number(CARD)}", lambda route: held.append(route))
    before = len(seen)
    _wake(app)
    assert _wait(page, lambda: held), "the refresh never re-read the card"
    page.click('#card-panel [data-action="handoff"]')
    page.fill('#card-panel form[data-form="handoff"] [name="note"]', TYPED)
    for route in held:
        route.continue_()
    page.unroute(f"**/api/cards/{app.number(CARD)}")
    _refreshed(page, seen, before)
    assert _handoff_text(page) == TYPED


def test_panel_updates_after_the_form_closes(ui, app):
    page = _open_card(ui, app)
    seen = _refresh_probe(page)
    page.click('#card-panel [data-action="handoff"]')
    page.fill('#card-panel form[data-form="handoff"] [name="note"]', TYPED)
    before = len(seen)
    status, _body, _raw = request(
        app, "POST", f"/api/cards/{app.number(CARD)}/comments", {"text": "A comment from Leon"}
    )
    assert status == 200, status
    _refreshed(page, seen, before)
    assert _handoff_text(page) == TYPED
    assert "A comment from Leon" not in page.inner_text("#card-panel")
    page.get_by_role("button", name="Cancel").click()
    page.wait_for_selector("#card-panel .activity")
    page.wait_for_function(
        "() => document.querySelector('#card-panel .activity').textContent.includes('A comment from Leon')"
    )
    assert page.locator('#card-panel form[data-form="handoff"]').count() == 0


def test_comment_in_progress_survives_refresh(ui, app):
    page = _open_card(ui, app)
    seen = _refresh_probe(page)
    page.fill("#details-comment", TYPED)
    before = len(seen)
    _wake(app)
    _refreshed(page, seen, before)
    assert page.input_value("#details-comment") == TYPED


STALE = "A-124"
STALE_TOO = "A-118"


def _let_claim_run_out(seed, cid):
    """The agent holding the card stopped: Needs me offers Requeue and Reassign."""
    with seed.service().transaction() as conn:
        # With no coordinator or parent agent to resume it, the card is the person's to recover.
        conn.execute("UPDATE boards SET coordinator=NULL WHERE slug='atlas'")
        conn.execute(
            "UPDATE tasks SET claim_until='2000-01-01T00:00:00Z' WHERE id=?",
            (seed.number(cid),),
        )


def _open_needs_handoff(ui, seed):
    _let_claim_run_out(seed, STALE)
    page = ui.open("#/needs")
    page.click(f'[data-needs-select="{seed.number(STALE)}"]')
    page.click('#card-panel [data-action="need-requeue"]')
    page.wait_for_selector('#card-panel form[data-form="handoff"]')
    page.fill('#card-panel form[data-form="handoff"] [name="note"]', TYPED)
    settle(page, 2500)
    return page


def test_needs_handoff_form_survives_queue_change(ui, app):
    page = _open_needs_handoff(ui, app)
    seen = _refresh_probe(page)
    before = len(seen)
    # A second card stops, so the queue itself changes and the Needs list redraws.
    _let_claim_run_out(app, STALE_TOO)
    _wake(app)
    assert _wait(page, lambda: len(seen) > before), "no background refresh ran"
    assert _wait(
        page, lambda: page.locator(f'[data-needs-select="{app.number(STALE_TOO)}"]').count() == 1
    ), "the queue did not change"
    settle(page, 600)
    assert _handoff_text(page) == TYPED


def test_needs_cancel_closes_the_form(ui, app):
    page = _open_needs_handoff(ui, app)
    page.get_by_role("button", name="Cancel").click()
    settle(page, 600)
    assert page.locator('#card-panel form[data-form="handoff"]').count() == 0
    assert page.locator('#card-panel [data-action="need-requeue"]').count() == 1
