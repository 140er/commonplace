"""Real-session sharing, audience and lifecycle journeys in the opt-in Deck look."""

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_shares_api import fake, team_fixture  # noqa: F401
from tests.test_bots_shares_lifecycle import remove
from tests.test_bots_turn import configure

LONG_NAME = "Willowbrookcopperfield Riverstonemeadowvale"


@pytest.fixture
def browser():
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    with playwright.sync_playwright() as manager:
        try:
            browser = manager.chromium.launch(executable_path=shutil.which("google-chrome"))
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional browser executable unavailable")
            raise
        yield browser
        browser.close()


def open_bot(browser, team, user, bot, size):
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        device_scale_factor=2,
        timezone_id="Europe/Amsterdam",
    )
    origin = f"http://127.0.0.1:{team.hub.port}"
    name, value = team.auth[user]["Cookie"].split("=", 1)
    context.add_cookies([{"name": name, "value": value, "url": origin}])
    page = context.new_page()
    page.set_default_timeout(10000)
    page.goto(f"{origin}/app/?ui=deck#/bots/{bot}")
    page.locator(".deck-bot-settings").wait_for()
    return context, page


def capture(page, label, size):
    from playwright.sync_api import expect

    expect(page.locator(".bot-access")).not_to_contain_text(
        re.compile(r"Loading (bot settings|memory|learning|audience)")
    )
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    from tests.test_deck_card_needs_browser import assert_contrast

    assert_contrast(page, ".bot-access")
    panel = page.locator(".bot-access")
    assert panel.count() == 1
    assert panel.evaluate("el => getComputedStyle(el.querySelector('h2')).fontSize") == "24px"
    for card in page.locator(".bot-access-card").all():
        assert card.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(17, 17, 17)"
        assert card.evaluate("el => getComputedStyle(el).borderRadius") == "20px"
    if size[0] == 390:
        assert panel.evaluate("el => getComputedStyle(el).paddingLeft") == "16px"
    measurements = panel.evaluate("""el => {
        const sheet = el.closest('.sheet'), title = el.querySelector('h2');
        const tabs = [...el.querySelectorAll('.bot-access-tabs button')];
        const checks = [...el.querySelectorAll('.bot-access-check input')];
        const labels = [...el.querySelectorAll('.bot-access-check')];
        const entries = [...el.querySelectorAll('[data-access-log] li')];
        const css = node => getComputedStyle(node), rect = node => node.getBoundingClientRect();
        return {
            titleTop: rect(title).top, expectedTop: rect(sheet).top + parseFloat(css(el).paddingTop),
            surface: css(sheet).backgroundColor,
            sectionRows: new Set(tabs.map(node => rect(node).top)).size,
            sectionCount: tabs.length,
            tabsOverflow: el.querySelector('.bot-access-tabs')?.scrollWidth > el.querySelector('.bot-access-tabs')?.clientWidth,
            selectedVisible: tabs.filter(node => node.getAttribute('aria-pressed') === 'true').every(node => rect(node).left >= rect(node.parentElement).left && rect(node).right <= rect(node.parentElement).right),
            selected: tabs.filter(node => node.getAttribute('aria-pressed') === 'true').map(node => css(node).backgroundColor),
            checks: checks.map(node => ({ size: rect(node).width, height: rect(node).height, radius: css(node).borderRadius, background: css(node).backgroundColor, border: css(node).borderColor })),
            optionGap: labels.length === 2 ? rect(labels[1]).top - rect(labels[0]).bottom : null,
            entryGaps: entries.slice(1).map((node, i) => rect(node).top - rect(entries[i]).bottom),
            times: [...el.querySelectorAll('time')].map(node => ({ text: node.textContent, title: node.title, at: node.dateTime })),
            primary: [...el.querySelectorAll('[data-bots-action=adopt], [data-bots-action=enable]')].map(node => ({ width: rect(node).width, cardWidth: rect(node.closest('section')).width, background: css(node).backgroundColor, color: css(node).color, radius: css(node).borderRadius, left: rect(node).left, parentLeft: rect(node.parentElement).left }))
        };
    }""")
    assert abs(measurements["titleTop"] - measurements["expectedTop"]) < 1, measurements
    assert measurements["surface"] == "rgb(0, 0, 0)"
    if measurements["sectionCount"]:
        assert measurements["sectionRows"] == 1
        assert measurements["selectedVisible"]
        assert measurements["selected"] == ["rgb(255, 255, 255)"]
        if measurements["sectionCount"] == 4 and size[0] == 390:
            assert measurements["tabsOverflow"]
    if measurements["checks"]:
        assert measurements["optionGap"] == 12
        for check in measurements["checks"]:
            assert check["size"] == check["height"] == 20
            assert check["radius"] == "6px"
            assert check["background"] in {"rgb(30, 30, 30)", "rgb(255, 255, 255)"}
            if check["background"] == "rgb(30, 30, 30)":
                assert check["border"] == "rgb(74, 74, 74)"
    assert all(gap == 20 for gap in measurements["entryGaps"])
    for time in measurements["times"]:
        assert time["title"] == time["at"]
        assert time["text"].startswith(("Today ", "Yesterday ")) or ", " in time["text"]
    for button in measurements["primary"]:
        assert button["width"] < button["cardWidth"] / 2
        assert button["background"] == "rgb(255, 255, 255)"
        assert button["color"] == "rgb(0, 0, 0)"
        assert button["radius"] == "999px"
        assert button["left"] == button["parentLeft"]
    target = os.environ.get("CP_UI_SHOTS")
    if target:
        folder = Path(target)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"bot-sharing-{label}-{size[0]}-measurements.json").write_text(
            json.dumps(measurements, indent=2)
        )
        page.screenshot(path=str(folder / f"bot-sharing-{label}-{size[0]}.png"))


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_owner_and_member_sharing_journey(browser, team, fake, size):  # noqa: F811
    from playwright.sync_api import expect

    db = team.hub.hub.db
    with db._lock, db._tx() as conn:
        conn.execute("UPDATE users SET name=? WHERE id=?", ("Workspace owner", team.avery))
        conn.execute("UPDATE users SET name=? WHERE id=?", (LONG_NAME, team.blake))
    bot = team.bot(name="Team helper", grants=None)
    configure(team.hub)
    owner_context, page = open_bot(browser, team, team.avery, bot, size)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    member_context = None
    try:
        page.locator(".deck-bot-settings").click()
        page.get_by_role("button", name="Add a person", exact=True).click()
        page.get_by_label("Find a person by name").fill(LONG_NAME)
        assert len(LONG_NAME) > 40 and len(LONG_NAME.split()) == 2
        assert (
            page.get_by_label("Find a person by name").evaluate(
                "el => getComputedStyle(el).outlineColor"
            )
            == "rgb(74, 74, 74)"
        )
        capture(page, "picker", size)
        page.locator("[data-people-picker] li:not([hidden]) button").click()
        preview = page.locator(".bot-access-preview")
        expect(preview).to_contain_text("has no access")
        page.get_by_label("Can see shared conversations", exact=True).check()
        expect(preview).to_contain_text("cannot send messages")
        page.get_by_label("Can talk to this bot", exact=True).check()
        expect(preview).to_contain_text("read and reply to shared conversations")
        page.get_by_label("Can see shared conversations", exact=True).uncheck()
        expect(preview).to_contain_text("cannot read other shared conversations")
        capture(page, "preview", size)
        page.get_by_role("button", name="Grant access", exact=True).click()
        expect(page.locator(".bot-access")).to_contain_text("Access saved")
        page.locator(".bot-access").evaluate("el => el.scrollTop = 0")
        expect(page.locator("[data-people-access] [role=status]")).to_have_text("Access saved.")
        expect(page.locator(".bot-access-people li b")).to_have_text(LONG_NAME)
        expect(page.locator(".bot-access-people li p")).to_have_text(
            "Can talk privately and start shared conversations. They can read and reply to shared conversations they start, but cannot read other shared conversations."
        )
        capture(page, "owner-editor", size)
        page.get_by_role("button", name="Access log", exact=True).click()
        expect(page.locator(".bot-access")).not_to_contain_text("Access saved.")
        page.get_by_role("button", name="People & permissions", exact=True).click()
        member_context, member = open_bot(browser, team, team.blake, bot, size)
        member.locator(".deck-bot-settings").click()
        expect(member.locator("[data-member-access]")).to_contain_text("You can talk privately")
        expect(
            member.locator("[data-permission-editor], [data-bots-action=sharing-picker]")
        ).to_have_count(0)
        capture(member, "member-read-only", size)
        assert team.share(bot, team.casey, auth=team.hub.member)[0] == 403
        assert (
            team.hub.request(
                team.hub.member, "PUT", f"/api/bots/{bot}/access", {"version": 0, "entries": []}
            )[0]
            == 403
        )
        member.get_by_role("button", name="Close", exact=True).click()
        fake.script = [[("text", "Shared bot answered the member.")]]
        member.get_by_label("Message to Team helper").fill("Hello from my private conversation")
        member.get_by_role("button", name="Send", exact=True).click()
        expect(member.locator('[data-region="messages"]')).to_contain_text(
            "Shared bot answered the member."
        )
        page.get_by_role("button", name="Change access", exact=True).click()
        page.get_by_label("Can see shared conversations", exact=True).check()
        page.get_by_role("button", name="Save access", exact=True).click()
        expect(page.locator("[data-permission-editor]")).to_have_count(0)
        page.get_by_role("button", name="Close", exact=True).click()
        page.get_by_role("button", name="New shared conversation", exact=True).click()
        expect(page.locator(".bot-access")).to_contain_text(f"Workspace owner, {LONG_NAME}")
        assert team.hub.get(team.hub.owner, f"/api/bots/{bot}/threads")[1]["threads"] == []
        capture(page, "thread-audience", size)
        page.get_by_label("Conversation name").fill("Team planning")
        page.get_by_role("button", name="Start shared conversation", exact=True).click()
        expect(page.locator('select[name="bot-thread"]')).to_have_value(
            team.store.threads(bot, team.avery)[0]["id"]
        )
        expect(page.locator(".bot-thread-audience")).to_contain_text(
            "Shared conversation · visible"
        )
        fake.script = [[("text", "Shared conversation answer.")]]
        page.get_by_label("Message to Team helper").fill("A message for everyone")
        page.get_by_role("button", name="Send", exact=True).click()
        expect(page.locator('[data-region="messages"]')).to_contain_text(
            "Shared conversation answer."
        )
        expect(page.locator(".bm-author")).to_have_text("Workspace owner")
        expect(page.locator('[data-region="messages"]')).not_to_contain_text(
            "Hello from my private conversation"
        )
        # Read-only permission remains usable even when there is no private thread.
        page.locator(".deck-bot-settings").click()
        page.get_by_role("button", name="Change access", exact=True).click()
        page.get_by_label("Can talk to this bot", exact=True).uncheck()
        page.get_by_role("button", name="Save access", exact=True).click()
        expect(page.locator("[data-permission-editor]")).to_have_count(0)
        member.reload()
        expect(member.locator(".bots-compose textarea")).to_have_count(0)
        member.locator(".deck-bot-settings").click()
        expect(member.locator("[data-member-access]")).to_contain_text("You cannot send messages")
        page.get_by_role("button", name="Change access", exact=True).click()
        page.get_by_role("button", name="Revoke access", exact=True).click()
        page.get_by_role("button", name="Remove access now?", exact=True).click()
        expect(page.locator(".bot-access-people")).to_contain_text("No one else has access")
        assert (
            member.evaluate("async id => (await fetch(`/api/bots/${id}/messages`)).status", bot)
            == 404
        )
        page.get_by_role("button", name="Access log", exact=True).click()
        expect(page.locator("[data-access-log]")).to_contain_text(
            f"Workspace owner gave {LONG_NAME} access"
        )
        expect(page.locator("[data-access-log]")).to_contain_text(
            f"changed {LONG_NAME}’s permissions"
        )
        expect(page.locator("[data-access-log]")).to_contain_text(f"removed {LONG_NAME}’s access")
        expect(page.get_by_role("button", name="Disable bot", exact=True)).to_have_count(0)
        capture(page, "log", size)
        assert not errors
    finally:
        owner_context.close()
        if member_context:
            member_context.close()


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_frozen_adoption_and_enable(browser, team, size):
    from playwright.sync_api import expect

    status, result, _ = team.hub.post(
        team.auth[team.casey], "/api/bots", {"name": "Research helper"}
    )
    assert status == 201
    bot = result["bot"]["id"]
    assert team.share(bot, team.blake, auth=team.auth[team.casey])[0] == 201
    remove(team, team.casey)
    context, page = open_bot(browser, team, team.avery, bot, size)
    try:
        expect(page.locator("[data-frozen]")).to_contain_text("needs a new owner")
        capture(page, "frozen-adopt", size)
        page.get_by_role("button", name="Adopt bot", exact=True).click()
        expect(page.locator("[data-frozen]")).to_have_count(0)
        assert team.store.get(bot, team.avery)["owner_id"] == team.avery
        page.get_by_role("button", name="Name, job & memory", exact=True).click()
        expect(page.locator(".bot-access")).not_to_contain_text("Loading memory")
        disable = page.get_by_role("button", name="Disable bot", exact=True)
        expect(disable).to_be_visible()
        assert disable.evaluate("el => getComputedStyle(el).color") == "rgb(240, 112, 138)"
        assert disable.evaluate("el => getComputedStyle(el).backgroundColor") == "rgba(0, 0, 0, 0)"
        page.locator(".bot-access").evaluate("el => el.scrollTop = 0")
        capture(page, "name-job-memory", size)
        disable.click()
        expect(page.get_by_role("group", name="Disable bot")).to_contain_text(
            "Conversations and access will be kept"
        )
        assert not team.store.get(bot, team.avery)["frozen"]
        page.get_by_role("button", name="Cancel", exact=True).click()
        expect(page.get_by_role("group", name="Disable bot")).to_have_count(0)
        disable.click()
        page.get_by_role("button", name="Disable for everyone", exact=True).click()
        expect(page.locator("[data-frozen]")).to_contain_text("disabled")
        capture(page, "disabled-enable", size)
        page.get_by_role("button", name="Enable bot", exact=True).click()
        expect(page.locator("[data-frozen]")).to_have_count(0)
    finally:
        context.close()


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_learning_sharing_requires_a_project_preview_and_a_separate_confirmation(
    browser, team, size
):
    from playwright.sync_api import expect

    from commonplace.bots import learned

    bot = team.bot(name="Learning helper", grants=None)
    assert (
        team.hub.request(
            team.hub.owner,
            "PATCH",
            "/api/learning/settings",
            {"enabled": True, "shared_enabled": True},
        )[0]
        == 200
    )
    assert (
        team.hub.request(
            team.hub.owner, "PATCH", "/api/projects/docs/learning", {"shared_enabled": True}
        )[0]
        == 200
    )
    assert (
        team.hub.request(team.hub.owner, "PATCH", f"/api/bots/{bot}/learning", {"mode": "auto"})[0]
        == 200
    )
    words = "Keep release notes with the project."
    message = team.store.add_user_message(bot, team.avery, words, "seed-learning-001")["id"]
    learned.open_learned(team.hub.hub.gateway, team.workspace).add_item(
        team.avery,
        bot,
        kind="preference",
        topic="release notes",
        text=words,
        source_message_id=message,
    )
    context, page = open_bot(browser, team, team.avery, bot, size)
    try:
        page.locator(".deck-bot-settings").click()
        page.get_by_role("button", name="Learning & sharing", exact=True).click()
        expect(page.get_by_role("button", name="Share with a project", exact=True)).to_be_visible()
        capture(page, "learning", size)
        page.get_by_role("button", name="Share with a project", exact=True).click()
        page.get_by_label("Project audience").select_option("docs")
        page.get_by_role("button", name="Preview sharing", exact=True).click()
        expect(page.locator("[data-learning-share]")).to_contain_text("everyone who can read Docs")
        expect(page.locator("[data-learning-share]")).to_contain_text(words)
        assert team.hub.get(team.hub.member, "/api/projects/docs/learned")[1]["items"] == []
        capture(page, "learning-confirmation", size)
        page.get_by_role("button", name="Share these words", exact=True).click()
        expect(page.locator("[data-learning-share]")).to_have_count(0)
        items = team.hub.get(team.hub.member, "/api/projects/docs/learned")[1]["items"]
        assert [item["text"] for item in items] == [words]
    finally:
        context.close()
