"""Real shell controls and their positions at desktop and phone widths."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from commonplace import schedule_time as times
from tests import test_deck_board_browser as shell
from tests.test_deck_board_browser import browser  # noqa: F401


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("look", ["deck", "classic"])
def test_snooze_goal_and_wake_in_the_card_panel(browser, width, look, monkeypatch):  # noqa: F811
    evidence = Path(os.environ.get("CP584_EVIDENCE", "/tmp/cp584-evidence"))
    evidence.mkdir(parents=True, exist_ok=True)
    before = os.environ.get("CP584_BEFORE_STATIC")
    if before:
        monkeypatch.setattr(shell, "STATIC", Path(before))
    page = browser.new_page(
        viewport={"width": 1280 if look == "classic" else width, "height": 844},
        timezone_id="Europe/Amsterdam",
        reduced_motion="reduce",
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cards = [dict(c) for c in shell.CARDS]
    card = next(c for c in cards if c["number"] == 2)
    writes = []
    try:
        page.clock.install(time=times.at("2030-03-30T08:00Z"))
        shell.open_board(page, cards=cards, look=look)
        page.set_viewport_size({"width": width, "height": 844})

        def route_api(route):
            path = urlsplit(route.request.url).path
            if path == "/api/goals":
                route.fulfill(json={"goals": [{"id": 4, "title": "Ship $& <verbatim>"}]})
            elif path in ("/api/card-snooze/2", "/api/card-snooze/2/cancel", "/api/goals/card"):
                data = route.request.post_data_json
                writes.append((path, data))
                if path.endswith("/cancel"):
                    card.pop("snoozed", None)
                elif path == "/api/goals/card":
                    if data["goal"]:
                        card["goals"] = {"chain": [{"id": 4, "title": "Ship $& <verbatim>"}]}
                    else:
                        card.pop("goals", None)
                else:
                    try:
                        until = times.stamp(times.at(data["until"], data["timezone"]))
                    except ValueError as exc:
                        route.fulfill(status=400, json={"error": str(exc)})
                        return
                    card["snoozed"] = {"until": until, "timezone": data["timezone"]}
                route.fulfill(json=card)
            else:
                route.fallback()

        page.route("**/api/**", route_api)
        page.evaluate("location.hash='#/card/2'")
        page.wait_for_selector("#card-panel .workbench-details:not([aria-busy])")
        panel = page.locator("#card-panel")
        phase = ("before" if before else "after") + ("-classic" if look == "classic" else "")

        def capture(state):
            panel.screenshot(path=str(evidence / f"{phase}-{width}-{state}.png"))
            result = panel.evaluate(
                """el => ({text:el.innerText, positions:[...el.querySelectorAll('[data-card-controls], [data-card-snoozed], [data-card-snooze] summary, [data-card-goal], [data-card-wake]')].map(node=>({text:node.innerText || node.value, x:node.getBoundingClientRect().x, y:node.getBoundingClientRect().y, width:node.getBoundingClientRect().width}))})"""
            )
            (evidence / f"{phase}-{width}-{state}.json").write_text(json.dumps(result, indent=2))

        capture("panel")
        assert panel.get_by_text("Snooze", exact=True).count() == 1
        assert panel.get_by_label("Goal", exact=True).count() == 1
        panel.get_by_text("Snooze", exact=True).click()
        capture("choices")
        panel.get_by_label("Pick a date/time").fill("2030-03-31T02:30")
        panel.get_by_role("button", name="Snooze until then").click()
        page.wait_for_function(
            "document.querySelector('[data-card-controls-error]').textContent.includes('clocks skip it')"
        )
        assert "that time does not exist in Europe/Amsterdam" in panel.inner_text()
        assert panel.get_by_label("Pick a date/time").input_value() == "2030-03-31T02:30"
        panel.get_by_role("button", name="Tomorrow 09:00", exact=True).click()
        page.wait_for_selector("[data-card-wake]")
        assert "Snoozed until 2030-03-31 09:00 Europe/Amsterdam" in panel.inner_text()
        page.wait_for_function(
            "document.querySelector('.card[data-card=\"2\"]').textContent.includes('Snoozed until')"
        )
        assert page.locator('.card[data-card="2"] [data-card-snoozed]').count() == 1
        capture("snoozed")
        panel.get_by_role("button", name="Wake now").click()
        page.wait_for_selector("[data-card-snooze]")
        page.wait_for_function(
            "!document.querySelector('.card[data-card=\"2\"] [data-card-snoozed]')"
        )
        panel.get_by_label("Goal", exact=True).select_option("4")
        panel.get_by_role("button", name="Save goal").click()
        page.wait_for_function(
            "document.querySelector('[data-card-controls]')?.dataset.goalId === '4' && !document.querySelector('[data-card-goal]').disabled"
        )
        assert "Ship $& <verbatim>" in panel.inner_text()
        panel.get_by_label("Goal", exact=True).select_option("")
        panel.get_by_role("button", name="Save goal").click()
        page.wait_for_function(
            "document.querySelector('[data-card-controls]')?.dataset.goalId === '' && !document.querySelector('[data-card-goal]').disabled"
        )
        assert writes[-1] == ("/api/goals/card", {"task": 2, "goal": None})
        assert any(path.endswith("/cancel") for path, _ in writes)
        assert errors == []
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        page.close()
