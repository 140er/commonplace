"""Cloud workspaces in a real browser, Classic and Deck, desktop and phone, owner and member.

Synthetic local data only: no remote connection and nothing is started. Set
COMPUTE_WORKSPACES_SHOTS to a directory to also save a screenshot of every state.
"""

import os
import threading
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.compute.control import open_store
from commonplace.compute.transport import ParamikoDriver
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolContext
from commonplace.gateway import Gateway
from commonplace.workers import WorkerSupervisor
from tests.test_compute_control import gateway_payload, node_payload

SIZES = {"desktop": (1440, 1000), "phone": (390, 844)}


def _shot(page, name):
    target = os.environ.get("COMPUTE_WORKSPACES_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f"{name}.png"), full_page=True)


def _no_sideways_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (
        "page scrolls sideways"
    )


@pytest.fixture
def served(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    for name in ("CHAT", "HOLD"):
        monkeypatch.setenv(f"COMMONPLACE_{name}_WORKER", "off")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    for name in ("COMMONPLACE_ALLOWED_HOSTS", "COMMONPLACE_APP_URL", "COMMONPLACE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        ParamikoDriver, "connect_gateway", lambda *args: pytest.fail("No remote transport")
    )
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    gateway = Gateway(db, WorkerSupervisor(str(tmp_path), launcher=object()))
    owner = db.create_user("owner@synthetic.test")
    member = db.create_user("member@synthetic.test")
    wid = db.create_workspace("Workspace browser fixture", owner)
    db.add_member(wid, member, "member", actor=owner)
    cookie = sessions.start_session(db, owner, wid)
    member_cookie = sessions.start_session(db, member, wid)
    principal = sessions.session_principal(db, cookie)
    BoardService(ToolContext(principal=principal, gateway=gateway)).board_add(
        "compute", wing="fixture"
    )
    store = open_store(gateway, wid)
    auth = {"session_hash": sessions._hash(cookie)}
    gw = store.create_gateway(principal, **auth, **gateway_payload())
    node = store.create_node(principal, **auth, **node_payload(gw["id"], name="Build machine"))
    server = gateway.build_server("127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class Fixture:
        pass

    fx = Fixture()
    fx.origin, fx.cookie, fx.member_cookie = origin, cookie, member_cookie
    fx.store, fx.principal, fx.auth, fx.node = store, principal, auth, node

    def make(**fields):
        body = {"name": "Build box", "image_source": "custom", "image": "ghcr.io/acme/tools:1.2"}
        return store.create_workspace(principal, **auth, **{**body, **fields})

    def state(workspace_id, state, reason=None, **kwargs):
        with store._journal() as conn:
            store._set_workspace_state(conn, workspace_id, state, reason, **kwargs)

    fx.make, fx.state = make, state
    try:
        yield fx
    finally:
        server.shutdown()
        server.server_close()
        db.close()


def _open(browser, fx, ui, size, cookie=None):
    width, height = SIZES[size]
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_cookies([{"name": sessions.COOKIE, "value": cookie or fx.cookie, "url": fx.origin}])
    page = context.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "response",
        lambda r: (
            errors.append(f"{r.status} {r.url}")
            if "/api/compute/workspaces" in r.url and r.status >= 500
            else None
        ),
    )
    route = "#/compute/management" if ui == "deck" else "#/compute"
    page.goto(f"{fx.origin}/app/?ui={ui}{route}")
    page.locator("#compute-workspaces").wait_for()
    return context, page, errors


@pytest.mark.parametrize("ui", ["deck", "classic"])
@pytest.mark.parametrize("size", ["desktop", "phone"])
def test_owner_creates_edits_and_reads_status(served, ui, size):
    from playwright.sync_api import sync_playwright

    fx = served
    label = f"{ui}-{size}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size)
            section = page.locator(".compute-ws")
            assert "No cloud workspaces yet" in section.inner_text()
            _shot(page, f"{label}-01-empty")
            page.locator('[data-ws-action="create"]').click()
            # Focus moves to the form heading; the preview shows the defaults.
            assert page.evaluate("document.activeElement.id") == "compute-ws-heading"
            assert "2 CPU, 4 GiB memory, 20 GiB storage. No machine chosen yet" in (
                page.locator("[data-ws-preview]").inner_text()
            )
            _shot(page, f"{label}-02-create-form")
            # Inline validation with the reason, while typing.
            page.locator('select[name="ws_image_choice"]').select_option("custom")
            page.locator('input[name="ws_image"]').fill("ubuntu; rm -rf /")
            assert (
                "Use only lowercase letters" in page.locator('[data-ws-error="image"]').inner_text()
            )
            page.locator('input[name="ws_cpu_cores"]').fill("64")
            assert "between 0.5 and 32" in page.locator('[data-ws-error="cpu_cores"]').inner_text()
            page.locator('input[name="ws_memory_gib"]').fill("0.1")
            assert (
                "between 0.5 and 128 GiB"
                in page.locator('[data-ws-error="memory_mib"]').inner_text()
            )
            page.locator('input[name="ws_name"]').fill("Acceptance box")
            page.locator('input[name="ws_name"]').press("Enter")
            assert page.locator("[data-ws-form]").count() == 1, "invalid values do not reach review"
            _shot(page, f"{label}-03-validation-errors")
            # Fix everything; the live preview follows; review shows resources before confirm.
            page.locator('input[name="ws_image"]').fill("ghcr.io/acme/tools:1.2")
            page.locator('input[name="ws_cpu_cores"]').fill("2")
            page.locator('input[name="ws_memory_gib"]').fill("4")
            page.locator('select[name="ws_node_id"]').select_option(fx.node["id"])
            assert "2 CPU, 4 GiB memory, 20 GiB storage on Build machine" in (
                page.locator("[data-ws-preview]").inner_text()
            )
            page.locator('input[name="ws_storage_gib"]').press("Enter")
            page.get_by_role("heading", name="Create Acceptance box?").wait_for()
            assert fx.store.list_workspaces(fx.principal, **fx.auth)["workspaces"] == []
            assert "2 CPU, 4 GiB memory, 20 GiB storage on Build machine" in (
                page.locator(".compute-ws-review").inner_text()
            )
            _shot(page, f"{label}-04-review")
            page.get_by_role("button", name="Confirm and create").click()
            page.locator("[data-ws-id]").first.wait_for()
            row = page.locator("[data-ws-id]").first
            text = row.inner_text()
            assert "Acceptance box" in text and "Not started yet" in text
            assert "2 CPU, 4 GiB memory, 20 GiB storage on Build machine" in text
            created = fx.store.list_workspaces(fx.principal, **fx.auth)["workspaces"]
            assert created[0]["image"] == "ghcr.io/acme/tools:1.2" and created[0]["cpu_cores"] == 2
            assert created[0]["memory_mib"] == 4096 and created[0]["started"] is False
            _shot(page, f"{label}-05-created")
            # Edit the limits.
            row.get_by_role("button", name="Change settings").click()
            page.locator('input[name="ws_memory_gib"]').fill("8")
            page.locator('input[name="ws_memory_gib"]').press("Enter")
            page.get_by_role("heading", name="Confirm the change to Acceptance box").wait_for()
            page.get_by_role("button", name="Confirm and save").click()
            page.get_by_text("8 GiB memory", exact=False).first.wait_for()
            assert (
                fx.store.list_workspaces(fx.principal, **fx.auth)["workspaces"][0]["memory_mib"]
                == 8192
            )
            # Details.
            page.locator('[data-ws-action="detail"]').first.click()
            detail = page.locator(".compute-ws-detail")
            assert "Settings version" in detail.inner_text()
            _shot(page, f"{label}-06-detail")
            page.get_by_role("button", name="Close").first.click()
            if size == "phone":
                _no_sideways_scroll(page)
                small = page.evaluate(
                    "[...document.querySelectorAll('.compute-ws button, .compute-ws input, .compute-ws select')]"
                    ".filter(el => el.offsetParent && el.getBoundingClientRect().height < 40)"
                    ".map(el => el.outerHTML.slice(0, 120))"
                )
                assert small == [], f"touch targets are at least 40px tall on the phone: {small}"
            # No inline styles in the section.
            assert page.locator(".compute-ws [style]").count() == 0
            assert errors == []
            context.close()
        finally:
            browser.close()


@pytest.mark.parametrize("ui", ["deck", "classic"])
@pytest.mark.parametrize("size", ["desktop", "phone"])
def test_every_status_and_the_registry_step_read_plainly(served, ui, size):
    from playwright.sync_api import sync_playwright

    fx = served
    node = fx.node["id"]
    fx.make(name="No machine yet box")
    private = fx.make(name="Private box", node_id=node, private_registry=True)
    pending = fx.make(
        name="Pending box",
        node_id=node,
        image_source="curated",
        image="docker.io/library/python:3.12-slim",
    )
    ready = fx.make(name="Ready box", node_id=node, cpu_cores=4, memory_mib=8192)
    fx.state(ready["id"], "ready", applied_revision=1)
    restart = fx.make(name="Restart box", node_id=node)
    fx.state(restart["id"], "ready", applied_revision=1)
    fx.store.update_workspace(
        fx.principal, restart["id"], **fx.auth, expected_revision=1, storage_gib=50
    )
    failed = fx.make(name="Failed box", node_id=node, memory_mib=2048)
    fx.state(failed["id"], "failed", "out_of_memory", applied_revision=1)
    label = f"{ui}-{size}"
    assert pending["status"] == "pending"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size)
            page.locator(".compute-ws [data-ws-id]").first.wait_for()
            body = page.locator(".compute-ws").inner_text()
            for phrase in (
                "No machine yet",
                "No credential bound yet",
                "Not started yet",
                "Ready",
                "Restart needed to apply the new settings.",
                "ran out of memory at 2 GiB",
                "Pinned" if False else "Not pinned: the tag can change",
                "Curated image",
            ):
                assert phrase in body, phrase
            _shot(page, f"{label}-10-all-statuses")
            private_row = page.locator(f'[data-ws-id="{private["id"]}"]')
            private_row.get_by_role("button", name="Bind a registry login").click()
            assert page.locator('input[type="password"]').count() == 0
            page.locator('input[name="ws_credential_id"]').fill("bad id!")
            page.locator('input[name="ws_credential_id"]').press("Enter")
            assert (
                "reference name of a stored credential"
                in page.locator('[data-ws-error="credential_id"]').inner_text()
            )
            _shot(page, f"{label}-11-bind-error")
            page.locator('input[name="ws_credential_id"]').fill("registry-login")
            page.locator('input[name="ws_credential_id"]').press("Enter")
            page.get_by_text("Registry login bound to Private box.").wait_for()
            assert (
                page.locator(f'[data-ws-id="{private["id"]}"]')
                .inner_text()
                .count("No credential bound yet")
                == 0
            )
            # The edit form of a started workspace says a restart is needed and locks the machine.
            page.locator(f'[data-ws-id="{ready["id"]}"]').get_by_role(
                "button", name="Change settings"
            ).click()
            assert "needs a restart to apply" in page.locator(".compute-ws-editor").inner_text()
            assert page.locator('select[name="ws_node_id"]').is_disabled()
            _shot(page, f"{label}-12-edit-started")
            if size == "phone":
                _no_sideways_scroll(page)
            assert errors == []
            context.close()
        finally:
            browser.close()


@pytest.mark.parametrize("ui", ["deck", "classic"])
@pytest.mark.parametrize("size", ["desktop", "phone"])
def test_member_sees_the_list_read_only(served, ui, size):
    from playwright.sync_api import sync_playwright

    fx = served
    fx.make(name="Shared box", node_id=fx.node["id"], private_registry=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size, cookie=fx.member_cookie)
            section = page.locator(".compute-ws")
            assert "Shared box" in section.inner_text()
            assert "requires the current workspace owner" in page.locator(".compute").inner_text()
            for action in ("create", "edit", "bind"):
                assert page.locator(f'[data-ws-action="{action}"]').count() == 0
            page.locator('[data-ws-action="detail"]').first.click()
            assert "Only the workspace owner can change cloud workspaces." in (
                page.locator(".compute-ws-detail").inner_text()
            )
            _shot(page, f"{ui}-{size}-20-member")
            if size == "phone":
                _no_sideways_scroll(page)
            assert errors == []
            context.close()
        finally:
            browser.close()
