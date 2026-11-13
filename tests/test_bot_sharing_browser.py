"""Real-session sharing, audience and lifecycle journeys in the opt-in Deck look."""

import os
import shutil
from pathlib import Path

import pytest

from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_shares_api import fake, team_fixture  # noqa: F401
from tests.test_bots_shares_lifecycle import remove
from tests.test_bots_turn import configure


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
        viewport={"width": size[0], "height": size[1]}, device_scale_factor=2
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
    target = os.environ.get("CP_UI_SHOTS")
    if target:
        folder = Path(target)
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / f"bot-sharing-{label}-{size[0]}.png"))


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_owner_and_member_sharing_journey(browser, team, fake, size):  # noqa: F811
    from playwright.sync_api import expect

    db = team.hub.hub.db
    with db._lock, db._tx() as conn:
        conn.execute("UPDATE users SET name=? WHERE id=?", ("Workspace owner", team.avery))
        conn.execute("UPDATE users SET name=? WHERE id=?", ("Workspace member", team.blake))
    bot = team.bot(name="Team helper", grants=None)
    configure(team.hub)
    owner_context, page = open_bot(browser, team, team.avery, bot, size)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    member_context = None
    try:
        page.locator(".deck-bot-settings").click()
        page.get_by_role("button", name="Add a person", exact=True).click()
        page.get_by_label("Find a person by name").fill("Workspace member")
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
        capture(page, "owner-editor", size)
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
        expect(page.locator(".bot-access")).to_contain_text("Workspace owner, Workspace member")
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
            "Workspace owner gave Workspace member access"
        )
        expect(page.locator("[data-access-log]")).to_contain_text(
            "changed Workspace member’s permissions"
        )
        expect(page.locator("[data-access-log]")).to_contain_text(
            "removed Workspace member’s access"
        )
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
        page.get_by_role("button", name="Disable bot", exact=True).click()
        page.get_by_role("button", name="Disable for everyone?", exact=True).click()
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
        page.get_by_role("button", name="Share with a project", exact=True).click()
        page.get_by_label("Project audience").select_option("docs")
        page.get_by_role("button", name="Preview sharing", exact=True).click()
        expect(page.locator("[data-learning-share]")).to_contain_text("everyone who can read Docs")
        expect(page.locator("[data-learning-share]")).to_contain_text(words)
        assert team.hub.get(team.hub.member, "/api/projects/docs/learned")[1]["items"] == []
        capture(page, "learning-preview", size)
        page.get_by_role("button", name="Share these words", exact=True).click()
        expect(page.locator("[data-learning-share]")).to_have_count(0)
        items = team.hub.get(team.hub.member, "/api/projects/docs/learned")[1]["items"]
        assert [item["text"] for item in items] == [words]
    finally:
        context.close()
