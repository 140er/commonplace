"""Fresh local browser journeys for named participants and human connection names."""

import threading

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.chat.coworkers.store import open_store
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolContext
from commonplace.gateway import Gateway
from commonplace.workers import WorkerSupervisor


@pytest.mark.parametrize("width", [1280, 390])
def test_named_participants_and_connection_names(tmp_path, monkeypatch, width):
    playwright = pytest.importorskip("playwright.sync_api")
    for worker in ("CHAT", "HOLD"):
        monkeypatch.setenv(f"COMMONPLACE_{worker}_WORKER", "off")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    for key in ("COMMONPLACE_ALLOWED_HOSTS", "COMMONPLACE_APP_URL", "COMMONPLACE_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    owner = db.create_user("owner@example.test")
    member = db.create_user("invited@example.test", name="Invited Person")
    wid = db.create_workspace("Picker fixture", owner)
    db.add_member(wid, member, "member", actor=owner)
    supervisor = WorkerSupervisor(str(tmp_path), launcher=object())
    gateway = Gateway(db, supervisor)
    monkeypatch.setattr(
        gateway, "call_memory_tool", lambda *args, **kwargs: {"wings": {}, "count": 0}
    )
    cookie = sessions.start_session(db, owner, wid)
    # An existing synthetic project is fixture setup; choosing it uses the UI.
    BoardService(ToolContext(sessions.session_principal(db, cookie), gateway)).board_add(
        "work", name="Fixture project", wing="project_fixture"
    )
    server = gateway.build_server("127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(viewport={"width": width, "height": 844})
                context.add_cookies([{"name": sessions.COOKIE, "value": cookie, "url": origin}])
                page = context.new_page()
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/app/?ui=deck#/settings")
                form = page.locator('[data-form="agent"]')
                name = form.get_by_label("Name", exact=True)
                name.fill("Research Helper")
                page.get_by_text("Identifier: research-helper", exact=True).wait_for()
                name.fill("Bad/Name")
                assert "letters" in form.locator("[data-agent-name-error]").inner_text()
                assert name.evaluate("input => input.validationMessage")
                name.fill("Research Helper")
                form.screenshot(path=str(tmp_path / f"agent-name-{width}.png"))
                form.get_by_role("button", name="Create token", exact=True).click()
                page.locator("[data-agent-reveal] .agent-secret").wait_for()
                row = db.connection("research-helper", wid)
                assert row["display_name"] == "Research Helper"
                name.fill("Research Helper")
                assert "already exists" in form.locator("[data-agent-name-error]").inner_text()
                name.fill("Different Helper")
                assert form.locator("[data-agent-name-error]").inner_text() == ""
                # Leave the credential reveal before capturing the conversation form.
                page.goto(origin + "/app/?ui=deck#/bots/-/coworkers/conversations")
                page.get_by_role("button", name="Create bot", exact=True).click()
                page.locator('[data-cw-form="bot"]').get_by_label("Name", exact=True).fill(
                    "Fixture bot"
                )
                with page.expect_response(
                    lambda response: (
                        response.request.method == "POST"
                        and response.url.endswith("/api/coworkers/bots")
                    )
                ) as created:
                    page.locator('[data-cw-form="bot"] button.primary').click()
                assert created.value.status == 201, created.value.text()
                page.locator(".cw-bot").filter(has_text="Fixture bot").wait_for()
                page.get_by_role("button", name="New", exact=True).click()
                assert not errors, errors
                form = page.locator('[data-cw-form="conversation"]')
                assert form.count() == 1, page.locator(".cw-body").inner_text()
                form.get_by_role("combobox", name="Type", exact=True).select_option("group")
                form.get_by_label("Title (optional)").fill("Named group")
                form.get_by_label("Search members").fill("no such person")
                assert not form.get_by_label("Invited Person", exact=True).is_visible()
                page.get_by_text("No members match your search.", exact=True).wait_for()
                form.get_by_label("Search members").fill("Invited")
                form.get_by_label("Invited Person", exact=True).check()
                form.get_by_label("Search members").fill("")
                assert form.get_by_label("Invited Person", exact=True).is_checked()
                form.get_by_label("Fixture bot", exact=True).check()
                project = form.get_by_role("combobox", name="Project (optional)", exact=True)
                options = project.locator("option").all_text_contents()
                assert options[0] == "No project" and len(options) > 1
                project.select_option(index=1)
                assert "Invited Person" in form.locator("[data-cw-audience]").inner_text()
                assert "Fixture bot" in form.locator("[data-cw-audience]").inner_text()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                form.screenshot(path=str(tmp_path / f"member-picker-{width}.png"))
                with page.expect_request(
                    lambda request: (
                        request.method == "POST"
                        and request.url.endswith("/api/coworkers/conversations")
                    )
                ) as submitted:
                    form.get_by_role("button", name="Create conversation", exact=True).click()
                assert submitted.value.post_data_json["member_ids"] == [member]
                page.get_by_role("heading", name="Named group", exact=True).wait_for()
                cid = page.url.split("/conversations/", 1)[1]
                assert set(open_store(gateway, wid).get_conversation(owner, cid)["member_ids"]) == {
                    owner,
                    member,
                }
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()
        db.close()
