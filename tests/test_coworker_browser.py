"""Real Chromium + gateway + durable coworkers; synthetic providers only.

Optional Playwright dependency is supplied by the coordinator's browser round.
No operator credentials, external endpoints, or memory workers are used.
"""

import json
import threading

import pytest

from commonplace import sessions
from commonplace.chat import settings
from commonplace.chat.coworkers import runtime
from commonplace.chat.coworkers.store import open_store
from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.workers import WorkerSupervisor


def test_browser_durable_chat_remains_usable_during_background_turn(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    for name in ("CHAT", "HOLD"):
        monkeypatch.setenv(f"COMMONPLACE_{name}_WORKER", "off")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    monkeypatch.delenv("COMMONPLACE_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("COMMONPLACE_APP_URL", raising=False)
    monkeypatch.delenv("COMMONPLACE_BASE_URL", raising=False)
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    supervisor = WorkerSupervisor(str(tmp_path), launcher=object())
    gateway = Gateway(db, supervisor)
    owner = db.create_user("synthetic@example.test")
    wid = db.create_workspace("Browser fixture", owner)
    member = db.create_user("invited@example.test")
    db.add_member(wid, member, "member", actor=owner)
    cookie = sessions.start_session(db, owner, wid)
    settings.set_front(
        db,
        sessions.person_principal(db, owner, wid),
        {
            "front": {
                "provider": "openai_compatible",
                "model": "synthetic",
                "base_url": "http://127.0.0.1:9",
            }
        },
    )
    entered, release = threading.Event(), threading.Event()
    prompts = []

    def complete(config, key, system, turns, **kwargs):
        data = json.loads(turns[0]["content"])
        prompts.append(data)
        if data["message"]["text"] == "Block this turn":
            entered.set()
            assert release.wait(20), "fixture must release the blocked synthetic provider"
            return "Late answer must be withheld"
        return "Synthetic coworker answer"

    monkeypatch.setattr(runtime.model, "complete", complete)
    monkeypatch.setattr(
        gateway, "call_memory_tool", lambda *args: pytest.fail("No global memory access")
    )
    gateway.coworker_worker.start()
    server = gateway.build_server("127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": sessions.COOKIE, "value": cookie, "url": origin}])
                page = context.new_page()
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/app/#/coworkers")
                page.get_by_role("button", name="Create bot", exact=True).click()
                page.get_by_label("Name", exact=True).fill("Mira")
                page.get_by_label("Instructions", exact=True).fill("Be concise.")
                page.locator('[data-cw-form="bot"] button.primary').click()
                page.locator('.cw-bot [data-cw-action="start-dm"]').click()
                page.get_by_label("Title (optional)").fill("Private first room")
                page.get_by_role("button", name="Create conversation", exact=True).click()
                page.locator("#cw-compose").fill("Block this turn")
                page.get_by_role("button", name="Send", exact=True).click()
                assert entered.wait(5)
                first_id = page.url.split("/coworkers/", 1)[1]
                page.locator("#cw-compose").fill("A second message is accepted")
                page.get_by_role("button", name="Send", exact=True).click()
                page.locator(".cw-message p").filter(
                    has_text="A second message is accepted"
                ).wait_for()
                assert not release.is_set()
                # A different conversation can receive a response while A is held.
                page.locator('.cw-bot [data-cw-action="start-dm"]').click()
                page.get_by_label("Title (optional)").fill("Private second room")
                page.get_by_role("button", name="Create conversation", exact=True).click()
                page.get_by_role("heading", name="Private second room", exact=True).wait_for()
                page.locator("#cw-memory-text").fill("Exact scoped memory <script>inert</script>")
                page.get_by_role("button", name="Add memory", exact=True).click()
                page.locator('[data-cw-action="hide-memory"]').wait_for()
                page.locator("#cw-compose").fill("Quick question")
                page.get_by_role("button", name="Send", exact=True).click()
                page.locator(".cw-message p").filter(
                    has_text="Synthetic coworker answer"
                ).wait_for()
                assert not release.is_set()
                second_id = page.url.split("/coworkers/", 1)[1]
                assert first_id != second_id
                assert (
                    prompts[-1]["memories"][0]["text"]
                    == "Exact scoped memory <script>inert</script>"
                )
                assert all(row["text"] != "Block this turn" for row in prompts[-1]["history"])
                page.reload()
                page.locator(".cw-message p").filter(
                    has_text="Synthetic coworker answer"
                ).wait_for()
                page.locator('[data-cw-action="hide-memory"]').click()
                page.get_by_text("No saved memory in this conversation.", exact=True).wait_for()
                page.goto(origin + "/app/#/coworkers/" + first_id)
                page.locator(".cw-message p").filter(
                    has_text="A second message is accepted"
                ).wait_for()
                # Cancel both the blocked turn and its queued successor.
                for item in open_store(gateway, wid).list_jobs(owner, first_id):
                    if item["state"] in ("pending", "running"):
                        page.locator(
                            f'[data-cw-action="cancel-job"][data-id="{item["id"]}"]'
                        ).click()
                assert all(
                    j["state"] == "cancelled"
                    for j in open_store(gateway, wid).list_jobs(owner, first_id)
                )
                release.set()
                gateway.coworker_worker.stop()
                assert all(
                    m["text"] != "Late answer must be withheld"
                    for m in open_store(gateway, wid).list_messages(owner, first_id)
                )
                page.get_by_role("button", name="Refresh", exact=True).click()
                assert (
                    "Late answer must be withheld" not in page.locator(".cw-timeline").inner_text()
                )
                page.locator('[data-cw-action="new-conversation"]').click()
                page.locator('[data-cw-form="conversation"] select[name="kind"]').select_option(
                    "group"
                )
                page.get_by_label("Title (optional)").fill("Shared planning room")
                page.locator('input[name="bot_ids"]').check()
                page.get_by_label("Other workspace user IDs").fill(member)
                page.get_by_role("button", name="Create conversation", exact=True).click()
                page.get_by_role("heading", name="Shared planning room", exact=True).wait_for()
                group_id = page.url.split("/coworkers/", 1)[1]
                assert set(
                    open_store(gateway, wid).get_conversation(member, group_id)["member_ids"]
                ) == {owner, member}
                assert "Exact scoped memory" not in page.locator("[data-cw-memory]").inner_text()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator("#cw-compose").is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert errors == []
            finally:
                browser.close()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()
        db.close()
