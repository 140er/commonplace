"""Cloud workspaces in a real browser, Classic and Deck, desktop and phone, owner and member.

Synthetic local data only: no remote connection and nothing is started. Run with Playwright
installed (for example ``uv run --with playwright==1.64.1``) and a Chromium cache. Without
Playwright the module is skipped with a stated reason, so ordinary collection is unaffected;
with ``CP_BROWSER_E2E=1`` (a browser evidence run) a missing Playwright fails instead, so the
cases can never be skipped by accident. Set COMPUTE_WORKSPACES_SHOTS to a directory to also
save a viewport-sized screenshot of every state (desktop 1280x800, phone 390x844).
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

if os.environ.get("CP_BROWSER_E2E") == "1":
    from playwright import sync_api as playwright_api  # an evidence run never skips
else:
    playwright_api = pytest.importorskip(
        "playwright.sync_api", reason="browser test needs the playwright package"
    )
sync_playwright = playwright_api.sync_playwright
expect = playwright_api.expect

SIZES = {"desktop": (1280, 800), "phone": (390, 844)}
TOUCH = 44
UIS = ["deck", "classic"]
SCREENS = ["desktop", "phone"]
FOCUS = (
    "(() => { const a = document.activeElement; return a ? {id: a.id, tag: a.tagName,"
    " name: a.getAttribute('name') || '', notice: a.hasAttribute('data-ws-notice'),"
    " summary: a.hasAttribute('data-ws-summary'), action: a.dataset.wsAction || ''} : {}; })()"
)


def _shot(page, name):
    target = os.environ.get("COMPUTE_WORKSPACES_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f"{name}.png"))


def _no_sideways_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (
        "page scrolls sideways"
    )


def _luminance(rgb):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first, second):
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _rgb(text):
    return [int(float(part)) for part in text[text.index("(") + 1 : text.index(")")].split(",")[:3]]


@pytest.fixture
def served(tmp_path, monkeypatch):
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
        return store.create_workspace(principal, **auth, **{**body, **fields})[0]

    def state(workspace_id, state, reason=None, **kwargs):
        with store._journal() as conn:
            store._set_workspace_state(conn, workspace_id, state, reason, **kwargs)

    def listed():
        return store.list_workspaces(principal, **auth)["workspaces"]

    fx.make, fx.state, fx.listed = make, state, listed
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


def _run(fx, ui, size, body, cookie=None):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size, cookie)
            body(page, f"{ui}-{size}")
            assert errors == []
            context.close()
        finally:
            browser.close()


def _section(page):
    return page.locator(".compute-ws")


def _focused(page):
    return page.evaluate(FOCUS)


def _assert_touch_targets(page):
    # Every control in the section is at least 44px tall on the phone; checked per element by locator.
    controls = page.locator(
        ".compute-ws button:visible, .compute-ws input:visible, .compute-ws select:visible,"
        " .compute-ws summary:visible"
    )
    heights = controls.evaluate_all("els => els.map(el => el.getBoundingClientRect().height)")
    assert heights and min(heights) >= TOUCH, (
        f"touch targets under {TOUCH}px: {sorted(heights)[:3]}"
    )
    sizes = controls.evaluate_all(
        "els => els.filter(el => el.tagName === 'INPUT' || el.tagName === 'SELECT')"
        ".map(el => parseFloat(getComputedStyle(el).fontSize))"
    )
    assert sizes and min(sizes) >= 16, "inputs are 16px on the phone so the browser does not zoom"


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_owner_creates_edits_and_reads_status(served, ui, size):
    fx = served

    def body(page, label):
        section = _section(page)
        expect(section).to_contain_text("No cloud workspaces yet. Choose New cloud workspace")
        assert section.get_by_text("isolated environment").count() == 1
        _shot(page, f"{label}-01-empty")
        page.get_by_role("button", name="New cloud workspace").click()
        expect(page.locator("#compute-ws-heading")).to_be_focused()
        expect(section).not_to_contain_text("No cloud workspaces yet")
        expect(page.locator("[data-ws-preview]")).to_contain_text(
            "2 CPU cores, 4 GiB memory, 20 GiB storage. No machine chosen yet"
        )
        expect(page.get_by_role("textbox", name="Name", exact=True)).to_have_attribute(
            "required", ""
        )
        expect(page.get_by_role("textbox", name="Name", exact=True)).to_have_attribute(
            "placeholder", "Build box"
        )
        if size == "phone":
            _assert_touch_targets(page)
            # The actions stay reachable above the fixed tab bar without scrolling to the end.
            button = page.get_by_role("button", name="Review and create")
            box = button.bounding_box()
            bar = page.evaluate(
                "(document.querySelector('#phone-tabs .m-tabs')?.getBoundingClientRect().top)"
                " ?? window.innerHeight"
            )
            assert box["y"] >= 0 and box["y"] + box["height"] <= bar + 1, (box, bar)
        _shot(page, f"{label}-02-create-form")
        # A blank name: one summary alert, the error on the name control only, focus on that control.
        page.get_by_role("button", name="Review and create").click()
        expect(page.locator("[data-ws-summary]")).to_have_text(
            "Fix the highlighted fields, then review again."
        )
        assert section.locator('[role="alert"]').count() == 1
        name = page.get_by_role("textbox", name="Name", exact=True)
        expect(name).to_have_attribute("aria-invalid", "true")
        expect(name).to_be_focused()
        assert section.locator('[aria-invalid="true"]').count() == 1
        expect(page.locator("#ws-error-name")).to_contain_text("Give it a name of 1 to 60")
        _shot(page, f"{label}-03-blank-name")
        # Own image: example, definition, and the error lands on the address control, not the select.
        page.get_by_role("combobox", name="Image", exact=True).select_option("custom")
        address = page.get_by_role("textbox", name="Image address", exact=True)
        expect(address).to_have_attribute("placeholder", "ghcr.io/acme/tools:1.2")
        expect(section).to_contain_text(
            "An image is the ready-made software a workspace starts from."
        )
        name.fill("Acceptance box")
        page.get_by_role("button", name="Review and create").click()
        expect(address).to_have_attribute("aria-invalid", "true")
        expect(address).to_be_focused()
        expect(page.get_by_role("combobox", name="Image", exact=True)).not_to_have_attribute(
            "aria-invalid", "true"
        )
        expect(page.locator("#ws-error-image")).to_contain_text("enter your own address")
        _shot(page, f"{label}-04-blank-image")
        # Invalid values: highlighted, never previewed as the plan, and the preview keeps GiB.
        name.fill("Acceptance box")
        address.fill("ubuntu; rm -rf /")
        expect(page.locator("#ws-error-image")).to_contain_text(
            "doesn't look like an image address"
        )
        page.get_by_role("spinbutton", name="CPU cores").fill("64")
        expect(page.locator("#ws-error-cpu_cores")).to_contain_text("from 0.5 to 32")
        page.get_by_role("spinbutton", name="Memory (GiB)").fill("0.1")
        expect(page.locator("#ws-error-memory_mib")).to_contain_text("from 0.5 to 128 GiB")
        expect(page.locator("[data-ws-preview]")).to_contain_text(
            "Fix the highlighted fields to see what this will use."
        )
        expect(page.locator("[data-ws-preview]")).not_to_contain_text("MiB")
        page.get_by_role("button", name="Review and create").click()
        assert page.locator("[data-ws-form]").count() == 1, "invalid values do not reach review"
        _shot(page, f"{label}-05-validation-errors")
        # Fix everything: the errors clear on their own controls and the preview follows.
        address.fill("ghcr.io/acme/tools:1.2")
        page.get_by_role("spinbutton", name="CPU cores").fill("2")
        page.get_by_role("spinbutton", name="Memory (GiB)").fill("4")
        expect(section.locator('[aria-invalid="true"]')).to_have_count(0)
        page.get_by_role("combobox", name="Machine", exact=True).select_option(fx.node["id"])
        expect(page.locator("[data-ws-preview]")).to_contain_text(
            "2 CPU cores, 4 GiB memory, 20 GiB storage on Build machine"
        )
        _shot(page, f"{label}-06-machine-chosen")
        page.get_by_role("button", name="Review and create").click()
        expect(page.get_by_role("heading", name="Create Acceptance box?")).to_be_focused()
        assert fx.listed() == []
        expect(page.locator(".compute-ws-review")).to_contain_text(
            "2 CPU cores, 4 GiB memory, 20 GiB storage on Build machine"
        )
        expect(section).to_contain_text("This only saves the settings in Commonplace")
        _shot(page, f"{label}-07-review")
        page.get_by_role("button", name="Confirm and create").click()
        row = page.locator("[data-ws-id]").first
        row.wait_for()
        # The confirmation is a status line that takes focus, so it is announced, and says it once.
        notice = page.locator("[data-ws-notice]")
        expect(notice).to_have_text(
            "Saved Acceptance box in Commonplace. Nothing has been sent to Build machine yet."
        )
        expect(notice).to_be_focused()
        expect(row).to_contain_text("Not started yet")
        expect(row.get_by_role("heading", name="Acceptance box")).to_be_visible()
        expect(row).to_contain_text("2 CPU cores, 4 GiB memory, 20 GiB storage on Build machine")
        created = fx.listed()
        assert created[0]["image"] == "ghcr.io/acme/tools:1.2" and created[0]["cpu_cores"] == 2
        assert created[0]["memory_mib"] == 4096 and created[0]["started"] is False
        _shot(page, f"{label}-08-created")
        # Edit the limits: the review names exactly what changes, and only that is saved.
        row.get_by_role("button", name="Change settings for Acceptance box").click()
        expect(page.locator("[data-ws-id]")).to_have_count(0)  # the form replaces the row
        expect(section).to_contain_text("Not started yet, so changes apply when it first starts.")
        _shot(page, f"{label}-09-edit-never-started")
        page.get_by_role("spinbutton", name="Memory (GiB)").fill("8")
        page.get_by_role("button", name="Review the change").click()
        expect(section.locator(".compute-ws-review")).to_contain_text("Memory: 4 GiB → 8 GiB")
        page.get_by_role("button", name="Confirm and save").click()
        expect(notice).to_contain_text("Saved Acceptance box in Commonplace.")
        expect(notice).to_be_focused()
        assert fx.listed()[0]["memory_mib"] == 8192
        assert fx.listed()[0]["revision"] == 2
        # Details, with a plain way out: Escape closes and focus returns to the row's button.
        page.get_by_role("button", name="Details for Acceptance box").click()
        detail = page.locator(".compute-ws-detail")
        expect(detail).to_contain_text("Changed 1 time since it was created")
        expect(detail).to_contain_text("can't be deleted yet")
        _shot(page, f"{label}-10-detail")
        page.keyboard.press("Escape")
        expect(detail).to_have_count(0)
        expect(page.get_by_role("button", name="Details for Acceptance box")).to_be_focused()
        if size == "phone":
            _no_sideways_scroll(page)
        assert page.locator(".compute-ws [style]").count() == 0

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_every_status_and_discarding_read_plainly(served, ui, size):
    fx = served
    node = fx.node["id"]
    fx.make(name="No machine yet box")
    fx.make(name="Private box", node_id=node, image="ghcr.io/acme/p:1")
    with fx.store._journal() as conn:  # a record stored before registry logins were hidden
        conn.execute("UPDATE cloud_workspaces SET private_registry=1 WHERE name='Private box'")
    fx.make(
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

    def body(page, label):
        section = _section(page)
        page.locator(".compute-ws [data-ws-id]").first.wait_for()
        rows = {
            name: page.locator("[data-ws-id]", has=page.get_by_role("heading", name=name))
            for name in (
                "No machine yet box",
                "Private box",
                "Pending box",
                "Ready box",
                "Restart box",
                "Failed box",
            )
        }
        expect(rows["No machine yet box"]).to_contain_text("No machine chosen")
        expect(rows["Private box"]).to_contain_text("Needs a registry login")
        expect(rows["Pending box"]).to_contain_text("Not started yet")
        expect(rows["Ready box"]).to_contain_text("Ready")
        expect(rows["Failed box"]).to_contain_text("Failed")
        # One plain status per row: a restart is its own status and is never also "Ready".
        expect(rows["Restart box"]).to_contain_text("Restart needed")
        expect(rows["Restart box"]).not_to_contain_text("Ready")
        expect(section).not_to_contain_text("matches the saved settings")
        assert section.get_by_role("button", name="Bind a registry login").count() == 0
        # Row actions name their workspace; started workspaces cannot be discarded.
        expect(
            rows["Ready box"].get_by_role("button", name="Details for Ready box")
        ).to_be_visible()
        expect(rows["Ready box"].get_by_role("button", name="Discard Ready box")).to_have_count(0)
        expect(
            rows["Pending box"].get_by_role("button", name="Discard Pending box")
        ).to_be_visible()
        _shot(page, f"{label}-11-all-statuses")
        rows["Restart box"].get_by_role("button", name="Details for Restart box").click()
        detail = page.locator(".compute-ws-detail")
        expect(detail).to_contain_text("Its saved settings changed after it last started")
        expect(detail).not_to_contain_text("matches the saved")
        expect(detail).to_contain_text("can't be deleted yet")
        _shot(page, f"{label}-12-restart-needed-detail")
        page.keyboard.press("Escape")
        rows["Failed box"].get_by_role("button", name="Details for Failed box").click()
        expect(page.locator(".compute-ws-detail")).to_contain_text("ran out of memory at 2 GiB")
        page.keyboard.press("Escape")
        # The edit form of a started workspace says a restart is needed and locks the machine.
        rows["Ready box"].get_by_role("button", name="Change settings for Ready box").click()
        expect(section).to_contain_text("applies when it is restarted")
        expect(page.get_by_role("combobox", name="Machine", exact=True)).to_be_disabled()
        expect(section).to_contain_text("A started workspace can't move to another machine yet.")
        _shot(page, f"{label}-13-edit-started")
        page.keyboard.press("Escape")
        # Discard: a confirmation that says what happens, then a plain confirmation and focus on it.
        rows["Pending box"].get_by_role("button", name="Discard Pending box").click()
        expect(page.locator("#compute-ws-heading")).to_have_text("Discard Pending box?")
        expect(section).to_contain_text("nothing on a machine is affected")
        assert len(fx.listed()) == 6
        _shot(page, f"{label}-14-discard-confirm")
        page.get_by_role("button", name="Keep it").click()
        expect(page.get_by_role("button", name="Discard Pending box")).to_be_focused()
        page.get_by_role("button", name="Discard Pending box").click()
        page.get_by_role("button", name="Discard Pending box", exact=True).click()
        notice = page.locator("[data-ws-notice]")
        expect(notice).to_have_text("Discarded Pending box. Nothing on a machine was affected.")
        expect(notice).to_be_focused()
        assert [item["name"] for item in fx.listed()].count("Pending box") == 0
        _shot(page, f"{label}-15-discarded")
        if size == "phone":
            _no_sideways_scroll(page)

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_server_conflict_and_a_stale_edit_are_explained(served, ui, size):
    fx = served
    fx.make(name="Taken name")
    mine = fx.make(name="Mine", image="ghcr.io/acme/mine:1")

    def body(page, label):
        section = _section(page)
        # The same name is taken by someone else meanwhile: the server answer lands beside the field.
        page.get_by_role("button", name="New cloud workspace").click()
        page.get_by_role("textbox", name="Name", exact=True).fill("Taken name")
        page.get_by_role("button", name="Review and create").click()
        page.get_by_role("button", name="Confirm and create").click()
        expect(page.locator("#ws-error-name")).to_have_text(
            "Another cloud workspace already uses this name."
        )
        expect(page.get_by_role("textbox", name="Name", exact=True)).to_be_focused()
        expect(page.locator("[data-ws-summary]")).to_contain_text("Fix the highlighted field")
        _shot(page, f"{label}-16-server-conflict")
        page.keyboard.press("Escape")
        # A form opened before the list changed never overwrites the newer saved state.
        page.get_by_role("button", name="Change settings for Mine").click()
        page.get_by_role("spinbutton", name="Memory (GiB)").fill("8")
        fx.store.update_workspace(
            fx.principal, mine["id"], **fx.auth, expected_revision=1, cpu_cores=8
        )
        page.get_by_role("button", name="Reload inventory").click()
        expect(page.get_by_role("button", name="Reload inventory")).to_be_focused()
        page.get_by_role("button", name="Review the change").click()
        page.get_by_role("button", name="Confirm and save").click()
        expect(page.locator("[data-ws-summary]")).to_contain_text(
            "This workspace changed while you were editing"
        )
        expect(section.locator("[data-ws-summary]")).to_be_focused()
        _shot(page, f"{label}-17-stale-edit")
        saved = next(item for item in fx.listed() if item["name"] == "Mine")
        assert (saved["cpu_cores"], saved["memory_mib"]) == (8, 4096), "nothing was overwritten"

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_member_sees_the_list_read_only_and_no_machine_names(served, ui, size):
    fx = served
    fx.make(name="Shared box", node_id=fx.node["id"])
    fx.make(name="Unassigned box", image="ghcr.io/acme/other:1")

    def body(page, label):
        section = _section(page)
        expect(section.get_by_role("heading", name="Shared box")).to_be_visible()
        expect(section).to_contain_text("on an assigned machine")
        expect(section).not_to_contain_text("Build machine")
        expect(page.locator(".compute")).to_contain_text("requires the current workspace owner")
        for action in ("create", "edit", "bind", "discard"):
            assert page.locator(f'[data-ws-action="{action}"]').count() == 0
        page.get_by_role("button", name="Details for Shared box").click()
        detail = page.locator(".compute-ws-detail")
        expect(detail).to_contain_text("Only the workspace owner can change cloud workspaces.")
        expect(detail).to_contain_text("A machine is assigned")
        expect(detail).not_to_contain_text("Build machine")
        _shot(page, f"{label}-20-member")
        page.get_by_role("button", name="Close").click()
        expect(page.get_by_role("button", name="Details for Shared box")).to_be_focused()
        if size == "phone":
            _no_sideways_scroll(page)

    _run(fx, ui, size, body, cookie=fx.member_cookie)


@pytest.mark.parametrize("size", SCREENS)
def test_compute_is_reachable_from_the_classic_menu_for_everyone(served, size):
    fx = served
    fx.make(name="Reachable box")
    for cookie in (fx.cookie, fx.member_cookie):
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                width, height = SIZES[size]
                context = browser.new_context(viewport={"width": width, "height": height})
                context.add_cookies([{"name": sessions.COOKIE, "value": cookie, "url": fx.origin}])
                page = context.new_page()
                page.set_default_timeout(10000)
                page.goto(f"{fx.origin}/app/?ui=classic#/settings")
                menu = "details.m-menu summary" if size == "phone" else "details.user-menu summary"
                page.locator(menu).first.wait_for()
                page.wait_for_load_state("networkidle")  # the shell repaints its menu once loaded
                page.locator(menu).first.click()
                page.locator('a[href="#/compute"]:visible').first.click()
                page.locator("#compute-workspaces").wait_for()
                expect(_section(page).get_by_role("heading", name="Reachable box")).to_be_visible()
                context.close()
            finally:
                browser.close()


def test_deck_control_borders_are_at_least_3_to_1(served):
    fx = served

    def body(page, label):
        page.get_by_role("button", name="New cloud workspace").click()
        for selector in ('input[name="ws_name"]', 'select[name="ws_network"]'):
            style = page.locator(selector).evaluate(
                "el => { const s = getComputedStyle(el); return {border: s.borderTopColor,"
                " background: s.backgroundColor}; }"
            )
            ratio = _contrast(_rgb(style["border"]), _rgb(style["background"]))
            assert ratio >= 3, f"{selector} border {style} is {ratio:.2f}:1"

    _run(fx, "deck", "desktop", body)
