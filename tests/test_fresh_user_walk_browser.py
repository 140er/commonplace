"""A brand-new owner walks the top flows of the usability audit (card #372) through the browser only.

The instance is empty: no user, workspace, token or card exists until the person signs up in the
page. Only mail delivery is captured, so the confirm link can be opened. Every action by the person
is a click, a typed field or a file pick in the web app. No API call, MCP call or bot call is made
by the test while it walks.

Some preconditions cannot be made by a person in the UI: a config proposal needs a Coworker, a
card waiting for release needs an untrusted agent, a reservation needs an agent that claims a card,
a live coordinator needs the Coworker's lease. ``seed_agents`` creates exactly those, in process
through the server's own services, once the person has signed up, made the project and worked a
card (the workspace must exist first) and before the steps that need them. The seed is the only
code that plays an agent; every step of the walk, before and after it, is UI-only.

Set PLAYWRIGHT_BROWSERS_PATH to an installed browser cache when HOME is isolated.
"""

import json
import re
import threading
from types import SimpleNamespace

import pytest

from commonplace import mailer, sessions, urls
from commonplace.boards.service import BoardService
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolContext
from commonplace.gateway import Gateway
from commonplace.orch import store as orch_store
from commonplace.policy import Policy
from commonplace.runner import registry
from commonplace.workers import WorkerSupervisor
from tests.runner_harness import http
from tests.test_orch_surfaces import MACHINE, config_text, sha

EMAIL = "walker@example.invalid"
PROJECT = "Atlas launch"
SLUG = "atlas-launch"
STEP_TIMEOUT_MS = 15000


class Mailbox:
    configured = True

    def __init__(self):
        self.messages = []
        self.changed = threading.Condition()

    def send(self, to, subject, text, html=None):
        with self.changed:
            self.messages.append((to, text))
            self.changed.notify_all()

    def link(self):
        with self.changed:
            assert self.changed.wait_for(lambda: self.messages, timeout=30), "no mail arrived"
        return re.search(r"https?://\S+", self.messages[-1][1])[0]


class NoWorker:
    def start(self, *_):
        raise AssertionError("This walk must not start a memory or model worker")


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    mailbox = Mailbox()
    monkeypatch.setattr(mailer, "from_env", lambda: mailbox)
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    supervisor = WorkerSupervisor(str(tmp_path), launcher=NoWorker())
    gateway = Gateway(db, supervisor)
    server = gateway.build_server("127.0.0.1", 0)
    port = server.server_address[1]
    monkeypatch.setattr(urls, "APP_URL", f"http://127.0.0.1:{port}")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield SimpleNamespace(
            db=db, gateway=gateway, mail=mailbox, port=port, origin=f"http://127.0.0.1:{port}"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        gateway.handoff.close()
        supervisor.stop_all()
        db.close()


def seed_agents(site):
    """Play the other side: a Coworker with a proposal and a lease, an untrusted agent's card, a holder."""
    db, gateway = site.db, site.gateway
    workspace = db.workspaces()[0]["id"]
    user = db._conn.execute(
        "SELECT user_id FROM memberships WHERE workspace_id = ?", (workspace,)
    ).fetchone()["user_id"]
    person = sessions.person_principal(db, user, workspace)

    def connect(name, *, trust, **policy):
        base = dict(
            profile="full",
            scopes=["memory:read", "memory:write"],
            read_wings=["shared"],
            write_wings=["shared"],
            trust=trust,
        )
        base.update(policy)
        base = {key: value for key, value in base.items() if value is not None}
        _, token = db.create_token(
            name=name,
            client="generic",
            user_ref=user,
            workspace_ref=workspace,
            policy=Policy.build(**base),
            actor=user,
        )
        principal = gateway.authenticate(f"Bearer {token}")
        service = BoardService(ToolContext(principal=principal, gateway=gateway))
        return SimpleNamespace(token=token, principal=principal, service=service)

    coworker = connect("coordinator-bot", trust="trusted")
    registry.ensure(db)
    now = "2030-01-01T00:00:00Z"
    with db._lock, db._tx() as conn:
        conn.execute(
            "INSERT INTO runner_machines(id, workspace_id, label, machine_connection_id, "
            "worker_connection_id, projects, topics, wings, requesters, binding_state, "
            "enrolled_by, updated_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'owner_attested',?,?,?,?)",
            (
                MACHINE,
                workspace,
                "runner-one",
                "conn_machine_example",
                "conn_worker_example",
                '["demo"]',
                '["worker"]',
                '["shared"]',
                json.dumps([coworker.principal.connection_id]),
                user,
                user,
                now,
                now,
            ),
        )
    store = orch_store.open_store(gateway, workspace)
    text = config_text(board=SLUG)
    store.write(person, SLUG, text, connections=[], sha256=sha(text))
    store.propose(
        coworker.principal, SLUG, text.replace("max_agents = 3", "max_agents = 5"), connections=[]
    )
    status, claimed = http(
        site.port,
        "POST",
        f"/api/orch/boards/{SLUG}/roles/main/resume",
        token=coworker.token,
        body={},
    )
    assert status == 200, claimed
    stranger = connect(
        "release-bot", trust="untrusted", write_wings=None, write_wing="inbox", profile="standard"
    )
    waiting = stranger.service.task_add(board=SLUG, title="Agent's follow-up")["number"]
    return SimpleNamespace(waiting=waiting)


def test_a_new_owner_walks_the_top_flows_in_the_browser(site):
    pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    from playwright.sync_api import Error, expect, sync_playwright

    with sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch()
        except Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional browser executable unavailable")
            raise
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.set_default_timeout(STEP_TIMEOUT_MS)
            calls = []
            page.on("request", lambda r: calls.append(r.url) if "/mcp" in r.url else None)
            Walk(page, site, expect).run()
            assert calls == [], "the walk reached an MCP endpoint"
        finally:
            browser.close()


class Walk:
    def __init__(self, page, site, expect):
        self.page, self.site, self.expect = page, site, expect

    def run(self):
        self.sign_up()
        self.first_project()
        self.new_project()
        self.cards()
        reserved = self.add_card("Review the budget", "", "Me (no run starts)", "normal")
        self.seeded = seed_agents(self.site)
        self.accept_config_from_today()
        self.release_from_card()
        self.drop_reservation(reserved)
        self.trust_in_admin()
        self.coworker_stop_resume()
        self.memory_import()

    def sign_up(self):
        page, expect = self.page, self.expect
        page.goto(self.site.origin + "/app/")
        page.fill('input[name="email"]', EMAIL)
        page.click("button.primary")
        page.goto(self.site.mail.link())
        page.get_by_role("button", name="Sign in").click()
        expect(page.locator(".shell")).to_be_visible()
        expect(page.locator(".rail")).to_contain_text("Owner, " + EMAIL)

    def first_project(self):
        page, expect = self.page, self.expect
        expect(page).to_have_url(re.compile(r"#/board/commonplace$"))
        expect(page.locator(".deck-project-row")).to_have_text("Commonplace")

    def new_project(self):
        page, expect = self.page, self.expect
        page.goto(self.site.origin + "/app/#/boards")
        page.get_by_role("button", name="New project").or_(
            page.get_by_role("link", name="New project")
        ).first.click()
        page.get_by_label("Project name").fill(PROJECT)
        page.get_by_role("button", name="Create project").click()
        expect(page).to_have_url(re.compile(rf"#/board/{SLUG}$"))
        expect(page.locator(".deck-project-row", has_text=PROJECT)).to_be_visible()

    def add_card(self, title, details, who, priority):
        page, expect = self.page, self.expect
        self.close_card()
        page.get_by_role("button", name="Add card").first.click()
        page.fill('input[name="title"]', title)
        page.fill('textarea[name="body"]', details)
        page.select_option('select[name="assignee"]', label=who)
        page.select_option('select[name="priority"]', priority)
        page.locator("[data-board-add] button[type=submit]").click()
        panel = page.locator("#card-panel")
        expect(panel).to_contain_text(title)
        number = int(page.url.rsplit("/", 1)[1])
        expect(page.locator(f'.card[data-card="{number}"]')).to_be_visible()
        return number

    def close_card(self):
        """The open card panel makes the board behind it inert; close it as a person would."""
        if self.page.locator("#card-panel").is_visible():
            self.page.locator('#card-panel [data-action="close-panel"]').first.click()
        self.expect(self.page.locator("#card-panel")).to_be_hidden()

    def open_edit_card(self):
        summary = self.page.locator("#card-panel summary", has_text="Edit card")
        self.expect(summary).to_be_visible()
        if summary.locator("xpath=..").get_attribute("open") is None:
            summary.click()

    def cards(self):
        page, expect = self.page, self.expect
        number = self.add_card("Draft the launch note", "First words", "Me (no run starts)", "high")
        panel = page.locator("#card-panel")
        # Edit: the title and details change on the card.
        self.open_edit_card()
        panel.get_by_role("button", name="Edit", exact=True).click()
        page.fill(
            'form[data-form="edit-card"] input[name="title"]', "Draft the launch announcement"
        )
        page.get_by_role("button", name="Save", exact=True).click()
        expect(panel).to_contain_text("Draft the launch announcement")
        expect(page.locator(f'.card[data-card="{number}"]')).to_contain_text(
            "Draft the launch announcement"
        )
        # Prioritise: urgent sticks after the card reloads.
        self.open_edit_card()
        panel.locator("#card-priority").select_option("urgent")
        panel.locator('[data-action="panel-priority"]').click()
        page.reload()
        self.open_edit_card()
        expect(page.locator("#card-panel #card-priority")).to_have_value("urgent")
        # Move: Move to... lists every status; Doing marks the card as started.
        page.locator('#card-panel [data-action="card-menu"]').first.click()
        page.locator('.card-menu [data-menu-move="doing"]').click()
        expect(page.locator("#card-panel")).to_contain_text("Doing")

    def accept_config_from_today(self):
        page, expect = self.page, self.expect
        page.goto(self.site.origin + "/app/#/")
        item = page.locator("[data-bt-config]")
        expect(item).to_have_count(1)
        expect(item).to_contain_text("Agent limit: 3 to 5")
        expect(item).to_contain_text("coordinator-bot")
        item.get_by_role("button", name="Accept").click()
        expect(item).to_have_count(0)

    def release_from_card(self):
        page, expect = self.page, self.expect
        page.goto(f"{self.site.origin}/app/#/card/{self.seeded.waiting}")
        form = page.locator('form[data-form="release-origin"]')
        expect(form).to_be_visible()
        expect(form.locator("[data-release-creator]")).to_contain_text("release-bot")
        form.locator('input[name="reason"]').fill("Checked by the owner")
        form.get_by_role("button", name="Release").click()
        expect(form).to_have_count(0)
        expect(page.locator("#card-panel")).to_contain_text("Ready")

    def drop_reservation(self, number):
        page, expect = self.page, self.expect
        page.goto(f"{self.site.origin}/app/#/card/{number}")
        panel = page.locator("#card-panel")
        expect(panel).to_contain_text("Reserved for walker")
        panel.get_by_role("button", name="Drop reservation").click()
        form = page.locator('form[data-form="remove-assignment"]')
        form.locator('textarea[name="note"]').fill("Handing it back")
        form.get_by_role("button", name="Drop reservation").click()
        expect(form).to_have_count(0)
        expect(panel).not_to_contain_text("Reserved for walker")

    def trust_in_admin(self):
        page, expect = self.page, self.expect
        page.goto(self.site.origin + "/app/#/admin")
        row = page.locator("tr", has_text="release-bot")
        expect(row).to_contain_text("untrusted")
        row.get_by_role("button", name="Make standard for release-bot").click()
        expect(row).to_contain_text("Make release-bot standard?")
        row.get_by_role("button", name="Confirm for release-bot").click()
        expect(row.locator("span", has_text=re.compile(r"^standard$"))).to_be_visible()
        expect(row).to_contain_text("Trust level saved.")
        # The limit the owner accepted from Today is the one Admin shows as effective.
        limits = page.locator("tr", has_text=PROJECT).filter(has_text="Coworker config")
        expect(limits).to_contain_text(re.compile(r"Unlimited\s*5\s*5\s*Coworker config"))

    def coworker_stop_resume(self):
        page, expect = self.page, self.expect
        page.goto(self.site.origin + "/app/#/settings")
        switch = page.get_by_label("Bots manage agent starts")
        switch.check()
        expect(switch).to_be_checked()
        page.get_by_role("button", name=f"{PROJECT} review settings").click()
        lease = page.locator("[data-orch-lease]")
        expect(lease).to_contain_text("Live")
        page.get_by_role("button", name="Stop Coworker…").click()
        page.get_by_role("button", name="Stop Coworker", exact=True).click()
        expect(lease).to_contain_text("Ended")
        page.get_by_role("button", name="Resume Coworker…").click()
        page.get_by_role("button", name="Resume Coworker", exact=True).click()
        expect(page.locator("[data-orch-status]")).to_contain_text("Coworker resumed.")

    def memory_import(self):
        page, expect = self.page, self.expect
        page.goto(self.site.origin + "/app/#/memory")
        page.locator('[data-memory-action="import"]').click()
        dialog = page.locator("dialog[open]")
        expect(dialog).to_contain_text("Import memory files")
        # The dialog redraws once its destination rules arrive, which resets a choice made
        # earlier. The hidden reason note is drawn with the rules: wait for it, then choose.
        expect(dialog.locator("[data-import-reason]")).to_be_attached()
        dialog.locator("#import-format").select_option(label="Memory text (.md or .txt)")
        # The destination follows the file type, so the default choices reach Preview.
        expect(dialog.locator("#import-scope")).to_have_value("private")
        expect(dialog).to_contain_text("can only be saved into your private imported history")
        dialog.locator('input[type="file"]').set_input_files(
            {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"My exact words.\n"}
        )
        dialog.get_by_role("button", name="Preview file").click()
        expect(dialog.locator("#memory-import-title")).to_have_text("Review import")
        expect(dialog).to_contain_text("notes.txt")
        expect(dialog).to_contain_text("1 file · 1 message")
        expect(dialog).to_contain_text("No AI model reads it")
        expect(dialog).to_contain_text("No memory has been saved yet")
