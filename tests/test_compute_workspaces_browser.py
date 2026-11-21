"""Cloud workspaces in a real browser, Classic and Deck, desktop and phone, owner and member.

Synthetic local data only: no remote connection and nothing is started. Needs the Playwright package and a
Chromium cache. Playwright is deliberately not a declared dependency (as for the other browser suites), so run

    uv run --with playwright==1.64.1 pytest tests/test_compute_workspaces_browser.py -rs

(or any environment that has Playwright). Without Playwright every case here is reported as skipped with a
reason: ``-rs`` prints one line with the count, so a run that skipped them cannot be mistaken for a pass. With
``CP_BROWSER_E2E=1`` (a browser evidence run) a missing Playwright fails instead, so the cases can never be
skipped by accident. Set COMPUTE_WORKSPACES_SHOTS to a directory to also save a viewport-sized screenshot of
every state (desktop 1280x800, phone 390x844).
"""

import importlib.util
import os
import re
import threading
from pathlib import Path
from types import ModuleType
from typing import NoReturn

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.compute.control import open_store
from commonplace.compute.transport import ParamikoDriver
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolContext
from commonplace.gateway import Gateway
from commonplace.workers import Worker, WorkerSupervisor
from tests.test_compute_control import gateway_payload, node_payload

playwright_api: ModuleType | None
HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if os.environ.get("CP_BROWSER_E2E") == "1":
    from playwright import sync_api as playwright_api  # an evidence run never skips
elif HAS_PLAYWRIGHT:
    from playwright import sync_api as playwright_api
else:
    playwright_api = None
# A mark on every case (not a module-level skip), so the skip count is the number of cases not run.
pytestmark = pytest.mark.skipif(
    playwright_api is None,
    reason="browser cases need the playwright package (see this file's docstring for the command)",
)


def _missing_playwright(*args: object, **kwargs: object) -> NoReturn:
    pytest.fail("browser cases need the playwright package")


sync_playwright = playwright_api.sync_playwright if playwright_api else _missing_playwright
expect = playwright_api.expect if playwright_api else _missing_playwright

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


class NoWorkers:
    def start(self, workspace_id: str, workspace_dir: Path) -> Worker:
        pytest.fail("No memory worker")


def _serve(tmp_path, monkeypatch, *, machine=True):
    for name in ("CHAT", "HOLD"):
        monkeypatch.setenv(f"COMMONPLACE_{name}_WORKER", "off")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    for name in ("COMMONPLACE_ALLOWED_HOSTS", "COMMONPLACE_APP_URL", "COMMONPLACE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        ParamikoDriver, "connect_gateway", lambda *args: pytest.fail("No remote transport")
    )
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    gateway = Gateway(db, WorkerSupervisor(str(tmp_path), launcher=NoWorkers()))
    owner = db.create_user("owner@synthetic.test")
    member = db.create_user("member@synthetic.test")
    wid = db.create_workspace("Workspace browser fixture", owner)
    db.add_member(wid, member, "member", actor=owner)
    cookie = sessions.start_session(db, owner, wid)
    member_cookie = sessions.start_session(db, member, wid)
    principal = sessions.session_principal(db, cookie)
    assert principal is not None
    BoardService(ToolContext(principal=principal, gateway=gateway)).board_add(
        "compute", wing="fixture"
    )
    store = open_store(gateway, wid)
    auth = {"session_hash": sessions._hash(cookie)}
    node = None
    if machine:
        gw = store.create_gateway(principal, **auth, **gateway_payload())
        node = store.create_node(principal, **auth, **node_payload(gw["id"], name="Build machine"))
    server = gateway.build_server("127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def make(**fields):
        body = {"name": "Build box", "image_source": "custom", "image": "ghcr.io/acme/tools:1.2"}
        return store.create_workspace(principal, **auth, **{**body, **fields})[0]

    def state(workspace_id, state, reason=None, **kwargs):
        with store._journal() as conn:
            store._set_workspace_state(conn, workspace_id, state, reason, **kwargs)

    def listed():
        return store.list_workspaces(principal, **auth)["workspaces"]

    class Fixture:
        def __init__(self):
            self.origin, self.cookie, self.member_cookie = origin, cookie, member_cookie
            self.store, self.principal, self.auth, self.node = store, principal, auth, node
            self.make, self.state, self.listed = make, state, listed

    fx = Fixture()
    try:
        yield fx
    finally:
        server.shutdown()
        server.server_close()
        db.close()


@pytest.fixture
def served(tmp_path, monkeypatch):
    yield from _serve(tmp_path, monkeypatch)


@pytest.fixture
def served_empty(tmp_path, monkeypatch):
    yield from _serve(tmp_path, monkeypatch, machine=False)


def _open(browser, fx, ui, size, cookie=None, viewport=None, touch=None):
    width, height = viewport or SIZES[size]
    phone = width < 700
    touch = phone if touch is None else touch  # a tablet can be touch without being a phone width
    context = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch
    )
    context.add_cookies([{"name": sessions.COOKIE, "value": cookie or fx.cookie, "url": fx.origin}])
    page = context.new_page()
    page.set_default_timeout(20000)
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
    # The screen repaints once the inventory has loaded; interact only after it has settled.
    expect(page.get_by_role("button", name="Refresh")).to_be_enabled()
    return context, page, errors


def _run(fx, ui, size, body, cookie=None, viewport=None, touch=None):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context, page, errors = _open(browser, fx, ui, size, cookie, viewport, touch)
            body(page, f"{ui}-{size}", context)
            assert errors == []
            context.close()
        finally:
            browser.close()


def _section(page):
    return page.locator(".compute-ws")


def _focused(page):
    return page.evaluate(FOCUS)


def _swipe(context, page, x, start, end, steps=14):
    """A real touch drag (touchstart, touchmove..., touchend) through the browser's input pipeline."""
    cdp = context.new_cdp_session(page)
    cdp.send(
        "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": start}]}
    )
    for step in range(1, steps + 1):
        point = {"x": x, "y": start + (end - start) * step / steps}
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [point]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    _settled(page)


def _settled(page):
    """Wait until neither the document nor the Deck scroller has moved for several frames (no fixed sleep)."""
    page.evaluate(
        "() => new Promise(resolve => {"
        " const top = () => [document.scrollingElement.scrollTop,"
        " document.querySelector('#content')?.scrollTop ?? 0].join();"
        " let last = top(), still = 0;"
        " const tick = () => { const now = top(); still = now === last ? still + 1 : 0; last = now;"
        " still >= 6 ? resolve() : requestAnimationFrame(tick); };"
        " requestAnimationFrame(tick); })"
    )


def _scrolled(page):
    """How far the document and the Deck scroller (#content) have scrolled."""
    return page.evaluate(
        "() => [Math.round(document.scrollingElement.scrollTop),"
        " Math.round(document.querySelector('#content').scrollTop)]"
    )


def _geometry(page):
    return page.evaluate(
        "() => { const rect = el => { if (!el) return null; const r = el.getBoundingClientRect();"
        " return {top: r.top, bottom: r.bottom, height: r.height}; };"
        " return {bar: rect(document.querySelector('.compute-ws-actions')),"
        " panel: rect(document.querySelector('.compute-ws-editor')),"
        " tabs: rect(document.querySelector('#phone-tabs .m-tabs')), height: innerHeight}; }"
    )


def _under_the_bar(page, selector):
    """True when the first visible match is overlapped by the sticky action bar."""
    return page.evaluate(
        "(selector) => { const el = document.querySelector(selector);"
        " const bar = document.querySelector('.compute-ws-actions');"
        " if (!el || !bar) return false; const a = el.getBoundingClientRect(), b = bar.getBoundingClientRect();"
        " return a.bottom > b.top + 1 && a.top < b.bottom - 1; }",
        selector,
    )


def _assert_touch_targets(page, inputs=True):
    # Every control in the section is at least 44px tall on the phone; checked per element by locator.
    controls = page.locator(
        ".compute-ws button:visible, .compute-ws input:visible, .compute-ws select:visible,"
        " .compute-ws summary:visible, .compute > header button:visible"
    )
    heights = controls.evaluate_all("els => els.map(el => el.getBoundingClientRect().height)")
    assert heights and min(heights) >= TOUCH, (
        f"touch targets under {TOUCH}px: {sorted(heights)[:3]}"
    )
    sizes = controls.evaluate_all(
        "els => els.filter(el => el.tagName === 'INPUT' || el.tagName === 'SELECT')"
        ".map(el => parseFloat(getComputedStyle(el).fontSize))"
    )
    if inputs:
        assert sizes and min(sizes) >= 16, (
            "inputs are 16px on the phone so the browser does not zoom"
        )


def _assert_small_text_is_14px(page):
    sizes = page.locator(
        ".compute-ws small:visible, .compute-ws .compute-quiet:visible,"
        " .compute-ws .compute-field > label:visible, .compute-ws .compute-preset-size:visible"
    ).evaluate_all("els => els.map(el => parseFloat(getComputedStyle(el).fontSize))")
    assert sizes and min(sizes) >= 14, (
        f"hints and errors are 14px on the phone: {sorted(sizes)[:3]}"
    )


def _assert_bar_sits_on_the_tab_bar(page):
    """One bottom offset per look. While its panel runs on below the screen the bar is stuck: its bottom edge is the
    tab bar's top edge (in Deck, the end of the scroller above it). When the panel ends on screen the bar simply ends
    the panel (it is the panel's last row). In no case does it reach under the tab bar."""
    _settled(page)  # the heading's focus scroll must have ended before the edges are measured
    geometry = _geometry(page)
    edge = geometry["tabs"]["top"] if geometry["tabs"] else geometry["height"]
    bar, panel = geometry["bar"], geometry["panel"]
    assert bar["bottom"] <= edge + 1.5, geometry
    assert abs(bar["bottom"] - edge) <= 1.5 or panel["bottom"] - bar["bottom"] <= 14, geometry
    assert bar["height"] <= 70, "Review and Cancel share one 44px row"


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_owner_creates_edits_and_reads_status(served, ui, size):
    fx = served

    def body(page, label, context):
        section = _section(page)
        expect(section).to_contain_text("Nothing here yet. Choose New cloud workspace")
        assert (
            section.get_by_text(
                "a separate place on one of your machines where Coworkers can run"
            ).count()
            == 1
        )
        _shot(page, f"{label}-01-empty")
        page.get_by_role("button", name="New cloud workspace").click()
        expect(page.locator("#compute-ws-heading")).to_be_focused()
        expect(section).not_to_contain_text("Nothing here yet")
        expect(page.locator("[data-ws-preview]")).to_contain_text(
            "2 CPU cores, 4 GiB memory, 20 GiB storage. Machine: not chosen yet"
        )
        expect(page.get_by_role("textbox", name="Name", exact=True)).to_have_attribute(
            "required", ""
        )
        # Each size says what to choose; the presets fill the fields.
        expect(section).to_contain_text("What do I need?")
        assert section.get_by_text("What do I need?").count() == 3
        if size == "phone":
            _assert_touch_targets(page)
            _assert_small_text_is_14px(page)
            _assert_bar_sits_on_the_tab_bar(page)
        _shot(page, f"{label}-02-create-form")
        page.get_by_role("button", name="Large", exact=False).first.click()
        expect(page.get_by_role("spinbutton", name="CPU cores")).to_have_value("4")
        expect(page.get_by_role("spinbutton", name="Memory (GiB)")).to_have_value("8")
        expect(page.get_by_role("spinbutton", name="Storage (GiB)")).to_have_value("50")
        expect(page.locator('[data-preset="large"]')).to_be_focused()
        page.get_by_role("button", name="Standard", exact=False).first.click()
        expect(page.get_by_role("spinbutton", name="CPU cores")).to_have_value("2")
        # A blank name: one summary with a link, the error on the name control only, focus on that control.
        page.get_by_role("button", name="Review and create").click()
        summary = page.locator("[data-ws-summary]")
        expect(summary).to_contain_text("Fix this field, then review again:")
        assert section.locator('[role="alert"]:not(:empty)').count() == 0
        name = page.get_by_role("textbox", name="Name", exact=True)
        expect(name).to_have_attribute("aria-invalid", "true")
        expect(summary).to_be_focused()  # the summary takes focus; its links jump to the fields
        assert section.locator('[aria-invalid="true"]').count() == 1
        expect(page.locator("#ws-error-name")).to_contain_text("Give it a name of 1 to 60")
        link = summary.get_by_role("link", name="Name:")
        # The whole list item is the target: at least 24px anywhere (WCAG 2.5.8), 44px for a finger.
        assert link.bounding_box()["height"] >= (TOUCH if size == "phone" else 24)
        # Programmatic focus draws a ring (the summary has just been focused).
        assert summary.evaluate("el => getComputedStyle(el).outlineStyle") != "none"
        link.click()
        expect(name).to_be_focused()
        _shot(page, f"{label}-03-blank-name")
        # Own image: example, definition, and the error lands on the address control, not the select.
        page.get_by_role("combobox", name="Image", exact=True).select_option("custom")
        address = page.get_by_role("textbox", name="Image address", exact=True)
        assert address.get_attribute("placeholder") is None  # an empty box does not read as filled
        expect(section).to_contain_text("The address of the image, for example ubuntu:24.04.")
        # The jargon sits behind one disclosure with one definition.
        help_box = section.locator(".compute-ws-help")
        expect(help_box).not_to_have_attribute("open", "")
        help_box.locator("summary").click()
        expect(help_box).to_contain_text("A registry is a website that stores images.")
        assert section.get_by_text("docker pull").count() == 1
        help_box.locator("summary").click()
        name.fill("Acceptance box")
        page.get_by_role("button", name="Review and create").click()
        expect(address).to_have_attribute("aria-invalid", "true")
        expect(summary).to_be_focused()
        expect(page.get_by_role("combobox", name="Image", exact=True)).not_to_have_attribute(
            "aria-invalid", "true"
        )
        expect(page.locator("#ws-error-image")).to_contain_text("enter your own address")
        _shot(page, f"{label}-04-blank-image")
        # Invalid values: highlighted, never previewed as the plan, and the preview keeps GiB.
        # (A problem shows when the field is committed, not while it is typed.)
        address.fill("ubuntu; rm -rf /")
        address.blur()
        expect(page.locator("#ws-error-image")).to_contain_text(
            "doesn't look like an image address"
        )
        page.get_by_role("spinbutton", name="CPU cores").fill("64")
        page.get_by_role("spinbutton", name="CPU cores").blur()
        expect(page.locator("#ws-error-cpu_cores")).to_contain_text("from 0.5 to 32")
        page.get_by_role("spinbutton", name="Memory (GiB)").fill("0.1")
        page.get_by_role("spinbutton", name="Memory (GiB)").blur()
        expect(page.locator("#ws-error-memory_mib")).to_contain_text("from 0.5 to 128 GiB")
        expect(page.locator("[data-ws-preview]")).to_contain_text(
            "Fix the highlighted fields to see what this will use."
        )
        expect(page.locator("[data-ws-preview]")).not_to_contain_text("MiB")
        page.get_by_role("button", name="Review and create").click()
        assert page.locator("[data-ws-form]").count() == 1, "invalid values do not reach review"
        expect(summary).to_contain_text("Fix these 3 fields, then review again:")
        expect(summary).to_be_focused()
        _shot(page, f"{label}-05-validation-errors")
        # Fix everything: the errors clear on their own controls, the summary goes away and the preview follows.
        address.fill("ghcr.io/acme/tools:1.2")
        page.get_by_role("spinbutton", name="CPU cores").fill("2")
        page.get_by_role("spinbutton", name="Memory (GiB)").fill("4")
        expect(section.locator('[aria-invalid="true"]')).to_have_count(0)
        expect(summary).to_be_hidden()
        page.get_by_role("combobox", name="Machine", exact=True).select_option(fx.node["id"])
        expect(page.locator("[data-ws-preview]")).to_contain_text(
            "2 CPU cores, 4 GiB memory, 20 GiB storage. Machine: Build machine"
        )
        expect(page.get_by_role("combobox", name="Machine", exact=True)).not_to_contain_text(
            "not checked yet"
        )
        if size == "desktop":
            # The bottom of the form: the machine row, the live preview and the actions.
            page.locator(".compute-ws-actions").scroll_into_view_if_needed()
        _shot(page, f"{label}-06-machine-chosen")
        page.get_by_role("button", name="Review and create").click()
        expect(page.get_by_role("heading", name="Create “Acceptance box”?")).to_be_focused()
        assert fx.listed() == []
        review = page.locator(".compute-ws-review")
        expect(review).to_contain_text(
            "2 CPU cores, 4 GiB memory, 20 GiB storage. Machine: Build machine"
        )
        assert review.get_attribute("role") == "list"
        expect(page.locator("[data-ws-honesty]")).to_contain_text(
            "Starting is not available yet, so nothing is sent to a machine."
        )
        if size == "phone":
            # The honesty sentence is fully above the action bar, never under it.
            assert not _under_the_bar(page, "[data-ws-honesty]")
            _assert_bar_sits_on_the_tab_bar(page)
        _shot(page, f"{label}-07-review")
        page.get_by_role("button", name="Confirm and create").click()
        row = page.locator("[data-ws-id]").first
        row.wait_for()
        # The confirmation is a status line (announced, never focused); focus goes to the saved row's heading.
        notice = page.locator("[data-ws-notice]")
        expect(notice).to_have_text(
            "Saved “Acceptance box”, not started. Starting is not available yet."
        )
        expect(row.get_by_role("heading", name="Acceptance box")).to_be_focused()
        expect(row).to_contain_text("Saved, not started")
        expect(row.get_by_role("heading", name="Acceptance box")).to_be_visible()
        expect(row).to_contain_text("2 CPU cores, 4 GiB memory, 20 GiB storage")
        expect(row).to_contain_text("Machine: Build machine")
        expect(row).to_contain_text("Image: ghcr.io/acme/tools:1.2")
        created = fx.listed()
        assert created[0]["image"] == "ghcr.io/acme/tools:1.2" and created[0]["cpu_cores"] == 2
        assert created[0]["memory_mib"] == 4096 and created[0]["started"] is False
        _shot(page, f"{label}-08-created")
        # Edit the limits: the review names exactly what changes, and only that is saved.
        row.get_by_role("button", name="Change settings for Acceptance box").click()
        expect(page.locator("[data-ws-id]")).to_have_count(0)  # the form replaces the row
        expect(section).to_contain_text("Saved, not started. Starting is not available yet")
        _shot(page, f"{label}-09-edit-never-started")
        page.get_by_role("spinbutton", name="Memory (GiB)").fill("8")
        page.get_by_role("button", name="Review the change").click()
        expect(section.locator(".compute-ws-review")).to_contain_text("Memory: 4 GiB → 8 GiB")
        _shot(page, f"{label}-09b-edit-review")
        page.get_by_role("button", name="Confirm and save").click()
        expect(notice).to_contain_text("Saved “Acceptance box”, not started.")
        expect(row.get_by_role("heading", name="Acceptance box")).to_be_focused()
        assert fx.listed()[0]["memory_mib"] == 8192
        assert (fx.listed()[0]["revision"], fx.listed()[0]["version"]) == (2, 2)
        # Details, with a plain way out: Escape closes and focus returns to the row's button.
        page.get_by_role("button", name="Details for Acceptance box").click()
        detail = page.locator(".compute-ws-detail")
        expect(page.locator("#compute-ws-heading")).to_be_focused()
        expect(detail).to_contain_text("Edited once")
        expect(detail).to_contain_text("Comes from")
        expect(detail).to_contain_text("GitHub Container Registry (ghcr.io)")
        expect(detail).to_contain_text("discard this workspace because it was never started")
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
    first = fx.make(name="Failed first box", node_id=node, image="ghcr.io/acme/f:1")
    fx.state(first["id"], "failed", "image_pull_failed")  # failed before anything was applied

    def body(page, label, context):
        section = _section(page)
        page.locator(".compute-ws [data-ws-id]").first.wait_for()
        names = (
            "No machine yet box",
            "Private box",
            "Pending box",
            "Ready box",
            "Restart box",
            "Failed box",
            "Failed first box",
        )
        rows = {
            name: page.locator(
                "[data-ws-id]", has=page.get_by_role("heading", name=name, exact=True)
            )
            for name in names
        }
        expect(rows["No machine yet box"]).to_contain_text("Saved, not started")
        expect(rows["No machine yet box"]).to_contain_text("Machine: not chosen yet")
        expect(rows["Private box"]).to_contain_text("Private image")
        expect(rows["Pending box"]).to_contain_text("Saved, not started")
        expect(rows["Pending box"]).to_contain_text("Image: Python 3.12 (slim)")
        expect(rows["Ready box"]).to_contain_text("Settings applied")
        expect(rows["Failed box"]).to_contain_text("Failed")
        # A failed row shows its reason in the list, not only in the details.
        expect(rows["Failed box"]).to_contain_text("It ran out of memory at 2 GiB")
        expect(rows["Failed first box"]).to_contain_text("could not download the image")
        # One vocabulary: "Changes waiting" for settings saved after a start; no Ready, Restart needed or running.
        expect(rows["Restart box"]).to_contain_text("Changes waiting")
        expect(section).not_to_contain_text("Restart needed")
        expect(section).not_to_contain_text("running")
        expect(section).not_to_contain_text("matches the saved settings")
        assert section.get_by_role("button", name="Bind a registry login").count() == 0
        # Row actions name their workspace; a started workspace, or one whose start failed, cannot be discarded.
        expect(
            rows["Ready box"].get_by_role("button", name="Details for Ready box")
        ).to_be_visible()
        expect(rows["Ready box"].get_by_role("button", name="Discard Ready box")).to_have_count(0)
        expect(
            rows["Failed first box"].get_by_role("button", name="Discard Failed first box")
        ).to_have_count(0)
        expect(
            rows["Pending box"].get_by_role("button", name="Discard Pending box")
        ).to_be_visible()
        # Discard is danger-styled and set apart from the other actions.
        assert "danger" in rows["Pending box"].get_by_role(
            "button", name="Discard Pending box"
        ).get_attribute("class")
        _shot(page, f"{label}-11-all-statuses")
        # The private image offers its fix: a Change image button that opens the form on the image.
        rows["Private box"].get_by_role("button", name="Details for Private box").click()
        detail = page.locator(".compute-ws-detail")
        expect(detail).to_contain_text(
            "Private images need a sign-in, and Commonplace can't store sign-ins yet"
        )
        expect(detail).to_contain_text("Choose a public image.")
        _shot(page, f"{label}-18-private-detail")
        detail.get_by_role("button", name="Change image").click()
        expect(page.get_by_role("textbox", name="Image address", exact=True)).to_be_focused()
        page.keyboard.press("Escape")
        rows["Restart box"].get_by_role("button", name="Details for Restart box").click()
        detail = page.locator(".compute-ws-detail")
        expect(detail).to_contain_text(
            "The saved settings are newer than the ones a machine applied"
        )
        expect(detail).to_contain_text("Changes are waiting for the next start.")
        expect(detail).not_to_contain_text("matches the saved")
        expect(detail).to_contain_text("can't be discarded yet")
        _shot(page, f"{label}-12-restart-needed-detail")
        page.keyboard.press("Escape")
        rows["Failed box"].get_by_role("button", name="Details for Failed box").click()
        expect(page.locator(".compute-ws-detail")).to_contain_text("ran out of memory at 2 GiB")
        _shot(page, f"{label}-19-failed-detail")
        page.keyboard.press("Escape")
        rows["Failed first box"].get_by_role("button", name="Details for Failed first box").click()
        expect(page.locator(".compute-ws-detail")).to_contain_text("could not download the image")
        expect(page.locator(".compute-ws-detail")).to_contain_text(
            "The last recorded start failed."
        )
        expect(
            page.locator(".compute-ws-detail").get_by_role("button", name="Discard")
        ).to_have_count(0)
        page.keyboard.press("Escape")
        # The edit form of a started workspace says what applies when, and locks the machine.
        rows["Ready box"].get_by_role("button", name="Change settings for Ready box").click()
        expect(section).to_contain_text(
            "A start was recorded for this workspace. Changes to the image, limits or network are saved and apply at its next start"
        )
        expect(page.get_by_role("combobox", name="Machine", exact=True)).to_be_disabled()
        expect(section).to_contain_text("A started workspace can't move to another machine yet.")
        _shot(page, f"{label}-13-edit-started")
        page.keyboard.press("Escape")
        # Discard: a confirmation inside the record's own row with fixed labels, Keep focused by default and as strong as Discard.
        rows["Pending box"].get_by_role("button", name="Discard Pending box").click()
        expect(page.locator("#compute-ws-heading")).to_have_text("Discard “Pending box”?")
        assert rows["Pending box"].locator("#compute-ws-heading").count() == 1, "inside its own row"
        expect(section).to_contain_text("nothing on a machine is affected")
        keep = page.get_by_role("button", name="Keep it")
        expect(keep).to_be_focused()
        confirm = page.get_by_role("button", name="Discard workspace", exact=True)
        assert "danger" in confirm.get_attribute("class")
        # The fixed labels fit their row on a phone: one line each, inside the row, side by side or stacked.
        row_box = rows["Pending box"].bounding_box()
        for button in (confirm, keep):
            box = button.bounding_box()
            assert (
                box["x"] >= row_box["x"] - 1
                and box["x"] + box["width"] <= row_box["x"] + row_box["width"] + 1
            )
            assert box["height"] <= 50, ("a label wrapped", box)
        assert confirm.bounding_box()["height"] >= 32 and keep.bounding_box()["height"] >= 32
        assert len(fx.listed()) == 7
        _shot(page, f"{label}-14-discard-confirm")
        keep.click()
        expect(page.get_by_role("button", name="Discard Pending box")).to_be_focused()
        page.get_by_role("button", name="Discard Pending box").click()
        # The record starts behind the page's back: the body is replaced by the refusal, Discard goes and Keep it has focus.
        fx.state(
            next(item["id"] for item in fx.listed() if item["name"] == "Pending box"),
            "ready",
            applied_revision=1,
        )
        page.get_by_role("button", name="Discard workspace", exact=True).click()
        refused = rows["Pending box"]
        expect(refused).to_contain_text(
            "It started after you opened this, so nothing was discarded."
        )
        expect(refused).not_to_contain_text("nothing on a machine is affected")
        # The status above the refusal changed in the same render: it no longer says "Saved, not started".
        expect(refused.locator(".compute-ws-status")).to_have_text("Started since you opened this")
        expect(refused).not_to_contain_text("Saved, not started")
        expect(page.get_by_role("button", name="Discard workspace")).to_have_count(0)
        expect(page.get_by_role("button", name="Keep it")).to_be_focused()
        # Focus on "Keep it" reads the reason: it is the button's description.
        expect(page.get_by_role("button", name="Keep it")).to_have_accessible_description(
            "It started after you opened this, so nothing was discarded. Keep it, or reload the list to see its current state."
        )
        _shot(page, f"{label}-21-discard-refused")
        page.keyboard.press("Escape")
        rows["No machine yet box"].get_by_role("button", name="Discard No machine yet box").click()
        page.get_by_role("button", name="Discard workspace", exact=True).click()
        notice = page.locator("[data-ws-notice]")
        expect(notice).to_have_text(
            "Discarded “No machine yet box”. Nothing on a machine was affected."
        )
        expect(page.get_by_role("button", name="New cloud workspace")).to_be_focused()
        assert [item["name"] for item in fx.listed()].count("No machine yet box") == 0
        _shot(page, f"{label}-15-discarded")
        if size == "phone":
            _no_sideways_scroll(page)

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_a_server_conflict_and_a_stale_rename_are_explained_and_recoverable(served, ui, size):
    fx = served
    fx.make(name="Taken name")
    mine = fx.make(name="Mine", image="ghcr.io/acme/mine:1")

    def body(page, label, context):
        section = _section(page)
        # The same name is taken by someone else meanwhile: the server answer lands beside the field.
        page.get_by_role("button", name="New cloud workspace").click()
        page.get_by_role("textbox", name="Name", exact=True).fill("Taken name")
        page.get_by_role("button", name="Review and create").click()
        page.get_by_role("button", name="Confirm and create").click()
        expect(page.locator("#ws-error-name")).to_have_text(
            "That name is taken. Choose another, for example Build box 2."
        )
        expect(page.locator("[data-ws-summary]")).to_be_focused()
        expect(page.locator("[data-ws-summary]")).to_contain_text("Fix this field")
        _shot(page, f"{label}-16-server-conflict")
        page.keyboard.press("Escape")  # a changed form asks first
        page.get_by_role("button", name="Leave and lose changes").click()
        # A rename made elsewhere moves the version even though nothing needs a restart: this form is stale.
        page.get_by_role("button", name="Change settings for Mine").click()
        page.get_by_role("textbox", name="Name", exact=True).fill("Mine, renamed here")
        fx.store.update_workspace(
            fx.principal, mine["id"], **fx.auth, expected_revision=1, name="Mine, renamed elsewhere"
        )
        page.get_by_role("button", name="Refresh").click()
        expect(page.get_by_role("button", name="Refresh")).to_be_focused()
        summary = page.locator("[data-ws-summary]")
        expect(summary).to_contain_text("Someone else changed this workspace. Nothing was saved.")
        expect(page.locator("[data-ws-notice]")).to_contain_text("changed while you had it open")
        expect(summary).to_contain_text("What you typed is still in the form below.")
        expect(page.get_by_role("textbox", name="Name", exact=True)).to_have_value(
            "Mine, renamed here"
        )
        reload_button = page.get_by_role("button", name="Reload to continue")
        expect(reload_button).to_be_visible()
        summary.evaluate("el => el.scrollIntoView({block: 'center'})")
        _shot(page, f"{label}-17-stale-edit")
        page.locator(".compute-ws-actions").scroll_into_view_if_needed()
        _settled(page)
        _shot(page, f"{label}-17c-stale-actions")
        # The submit is really disabled (not just styled) while stale, with the way on beside it; nothing is overwritten.
        review = page.get_by_role("button", name="Review the change")
        expect(review).to_be_disabled()
        review.click(force=True)
        assert (
            page.get_by_role("heading", name="Confirm the change to “Mine, renamed here”").count()
            == 0
        )
        saved = next(item for item in fx.listed() if item["id"] == mine["id"])
        assert saved["name"] == "Mine, renamed elsewhere", "nothing was overwritten"
        # Reload to continue: a fresh form on the current values; what was typed waits behind "Apply my changes again".
        reload_button.click()
        name = page.get_by_role("textbox", name="Name", exact=True)
        expect(name).to_have_value("Mine, renamed elsewhere")
        expect(section.get_by_role("button", name="Reload to continue")).to_have_count(0)
        reapply = page.get_by_role("button", name="Apply my changes again")
        expect(reapply).to_be_visible()
        expect(section.locator("[data-ws-reapply]")).to_contain_text("Name: Mine, renamed here")
        _shot(page, f"{label}-17b-stale-recovered")
        reapply.click()
        expect(name).to_have_value("Mine, renamed here")
        expect(reapply).to_have_count(0)
        name.fill("Mine, final")
        page.get_by_role("button", name="Review the change").click()
        page.get_by_role("button", name="Confirm and save").click()
        expect(page.locator("[data-ws-notice]")).to_contain_text("Saved “Mine, final”")
        assert next(i for i in fx.listed() if i["id"] == mine["id"])["version"] == 3

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_member_sees_the_list_read_only_and_no_machine_names(served, ui, size):
    fx = served
    fx.make(name="Shared box", node_id=fx.node["id"])
    fx.make(name="Unassigned box", image="ghcr.io/acme/other:1")

    def body(page, label, context):
        section = _section(page)
        expect(section.get_by_role("heading", name="Shared box")).to_be_visible()
        expect(section).to_contain_text("Machine: assigned (name hidden)")
        expect(section).to_contain_text("Image: ghcr.io/acme/tools:1.2")
        expect(section).not_to_contain_text("Build machine")
        expect(page.locator(".compute")).to_contain_text(
            "You can read this workspace's settings. Only its owner can change them."
        )
        expect(page.locator(".compute")).not_to_contain_text("requires the current workspace owner")
        for action in ("create", "edit", "bind", "discard"):
            assert page.locator(f'[data-ws-action="{action}"]').count() == 0
        page.get_by_role("button", name="Details for Shared box").click()
        detail = page.locator(".compute-ws-detail")
        expect(detail).to_contain_text("You can read this workspace's settings.")
        expect(detail).to_contain_text("assigned (name hidden)")
        expect(detail).not_to_contain_text("Build machine")
        _shot(page, f"{label}-20-member")
        page.get_by_role("button", name="Close").click()
        expect(page.get_by_role("button", name="Details for Shared box")).to_be_focused()
        # Refresh keeps the member's section on screen all along and does not lose the place.
        page.get_by_role("button", name="Details for Shared box").click()
        page.evaluate(
            "() => { window.__sawSection = []; new MutationObserver(() => window.__sawSection.push("
            "!!document.querySelector('[data-ws-root]'))).observe(document.querySelector('#content') ||"
            " document.body, {childList: true, subtree: true}); }"
        )
        page.get_by_role("button", name="Refresh").click()
        expect(page.get_by_role("button", name="Refresh")).to_be_focused()
        expect(section.get_by_role("heading", name="Shared box")).to_be_visible()
        assert all(page.evaluate("window.__sawSection")), (
            "the section never vanished during the reload"
        )
        if size == "phone":
            _no_sideways_scroll(page)

    _run(fx, ui, size, body, cookie=fx.member_cookie)


@pytest.mark.parametrize("size", SCREENS)
def test_classic_shows_cloud_workspaces_as_a_first_level_entry(served, size):
    fx = served
    fx.make(name="Reachable box")
    for cookie in (fx.cookie, fx.member_cookie):
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                width, height = SIZES[size]
                phone = size == "phone"
                context = browser.new_context(
                    viewport={"width": width, "height": height}, has_touch=phone, is_mobile=phone
                )
                context.add_cookies([{"name": sessions.COOKIE, "value": cookie, "url": fx.origin}])
                page = context.new_page()
                page.set_default_timeout(20000)
                page.goto(f"{fx.origin}/app/?ui=classic#/settings")
                bar = "#phone-tabs .m-tabs" if phone else "nav.rail"
                entry = page.locator(f'{bar} a[data-nav="compute"]')
                entry.wait_for()
                page.wait_for_load_state("networkidle")
                expect(entry).to_have_text("Cloud workspaces")
                if cookie == fx.cookie:
                    _shot(page, f"classic-{size}-22a-settings-with-entry")
                # It is in the account menu too (the phone's Menu button, the desktop rail's account menu).
                menu = "details.m-menu summary" if phone else "details.user-menu summary"
                page.locator(menu).first.click()
                expect(page.locator('a[href="#/compute"]:visible').first).to_be_visible()
                page.locator(menu).first.click()
                entry.click()
                page.locator("#compute-workspaces").wait_for()
                expect(_section(page).get_by_role("heading", name="Reachable box")).to_be_visible()
                expect(entry).to_have_attribute("aria-current", "page")
                if cookie == fx.cookie:
                    _shot(page, f"classic-{size}-22-entry-route")  # the route the entry opens
                context.close()
            finally:
                browser.close()


def test_deck_control_borders_buttons_and_placeholders_are_readable(served):
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="New cloud workspace").click()
        page.locator('input[name="ws_name"]').fill("")
        for selector in ('input[name="ws_name"]', 'select[name="ws_network"]'):
            style = page.locator(selector).evaluate(
                "el => { const s = getComputedStyle(el); return {border: s.borderTopColor,"
                " background: s.backgroundColor}; }"
            )
            ratio = _contrast(_rgb(style["border"]), _rgb(style["background"]))
            assert ratio >= 3, f"{selector} border {style} is {ratio:.2f}:1"
        # A secondary button has a visible edge against the surface (it had none).
        edge = page.get_by_role("button", name="Cancel").evaluate(
            "el => { const s = getComputedStyle(el); return {border: s.borderTopColor,"
            " width: s.borderTopWidth, background: getComputedStyle(el.closest('.compute-editor')).backgroundColor}; }"
        )
        assert (
            edge["width"] != "0px"
            and _contrast(_rgb(edge["border"]), _rgb(edge["background"])) >= 3
        ), edge
        # Placeholders are readable (4.5:1), not the browser's dim default.
        page.get_by_role("combobox", name="Image", exact=True).select_option("custom")
        placeholder = page.locator('input[name="ws_image"]').evaluate(
            "el => ({color: getComputedStyle(el, '::placeholder').color,"
            " background: getComputedStyle(el).backgroundColor})"
        )
        assert _contrast(_rgb(placeholder["color"]), _rgb(placeholder["background"])) >= 4.5, (
            placeholder
        )
        # The control line is its own token: the global --line-strong is what the rest of Deck uses, unchanged.
        tokens = page.evaluate(
            "() => ({root: getComputedStyle(document.documentElement).getPropertyValue('--line-strong').trim(),"
            " control: getComputedStyle(document.querySelector('.compute')).getPropertyValue('--control-line').trim()})"
        )
        assert tokens["control"].lower() == "#6b6b73"
        assert tokens["root"].lower() != "#6b6b73", tokens
        # The token lives in Deck's own stylesheet; compute.css only uses it (with the global line as the Classic fallback).
        static = Path(__file__).resolve().parents[1] / "commonplace" / "web" / "static"
        assert "--control-line: #6B6B73" in (static / "deck-capy.css").read_text()
        assert "#6B6B73" not in (static / "compute.css").read_text()

    _run(fx, "deck", "desktop", body)


def test_deck_danger_button_has_a_visible_edge_and_one_line_label(served):
    fx = served
    fx.make(name="Danger box", node_id=fx.node["id"])

    def body(page, label, context):
        page.get_by_role("button", name="Details for Danger box").click()
        discard = page.locator(".compute-ws-detail").get_by_role("button", name="Discard")
        style = discard.evaluate(
            "el => { const s = getComputedStyle(el); return {border: s.borderTopColor, width: s.borderTopWidth,"
            " background: getComputedStyle(el.closest('.compute-editor')).backgroundColor, height: el.getBoundingClientRect().height}; }"
        )
        assert style["width"] != "0px"
        assert _contrast(_rgb(style["border"]), _rgb(style["background"])) >= 3, style
        assert style["height"] <= 40, ("the label wrapped", style)
        _shot(page, f"{label}-31-danger-button")

    _run(fx, "deck", "desktop", body)


@pytest.mark.parametrize("ui", UIS)
def test_a_touch_tablet_gets_the_phone_sizes(served, ui):
    """At 768px (not a phone width) a touch device still gets 44px targets, 16px inputs and 14px text: the rules follow
    the pointer, not only the width."""
    fx = served
    fx.make(name="Tablet box", node_id=fx.node["id"])

    def body(page, label, context):
        assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
        _assert_touch_targets(page, inputs=False)
        _shot(page, f"{label}-768-list")
        page.get_by_role("button", name="New cloud workspace").click()
        _assert_touch_targets(page)
        _assert_small_text_is_14px(page)
        page.get_by_role("textbox", name="Name", exact=True).fill("")
        page.get_by_role("button", name="Review and create").click()
        link = page.locator("[data-ws-summary]").get_by_role("link", name="Name:")
        assert link.bounding_box()["height"] >= TOUCH
        _shot(page, f"{label}-768-form-errors")

    _run(fx, ui, "desktop", body, viewport=(768, 1024), touch=True)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_an_invalid_field_looks_invalid(served, ui, size):
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="New cloud workspace").click()
        page.get_by_role("spinbutton", name="CPU cores").fill("64")
        page.get_by_role("combobox", name="Image", exact=True).select_option("custom")
        page.get_by_role("textbox", name="Image address", exact=True).fill("bad image")
        page.get_by_role("button", name="Review and create").click()
        # Red edge on inputs, selects included (the generic control rule used to win), red message with an icon.
        for selector in (
            'input[name="ws_cpu_cores"]',
            'input[name="ws_image"]',
            'input[name="ws_name"]',
        ):
            colors = page.locator(selector).evaluate(
                "el => ({edge: getComputedStyle(el).borderTopColor, error: getComputedStyle("
                "document.getElementById(el.getAttribute('aria-describedby').split(' ').pop())).color})"
            )
            assert colors["edge"] == colors["error"], (selector, colors)
        normal = page.locator('select[name="ws_network"]').evaluate(
            "el => getComputedStyle(el).borderTopColor"
        )
        assert normal != page.locator('input[name="ws_cpu_cores"]').evaluate(
            "el => getComputedStyle(el).borderTopColor"
        )
        icon = page.locator("#ws-error-cpu_cores").evaluate(
            "el => getComputedStyle(el, '::before').content"
        )
        assert icon not in ("none", "normal", ""), icon
        # Error text and the edge keep their contrast against the surface.
        message = page.locator("#ws-error-cpu_cores").evaluate(
            "el => ({color: getComputedStyle(el).color, background: getComputedStyle(el.closest('.compute-editor')).backgroundColor,"
            " size: parseFloat(getComputedStyle(el).fontSize)})"
        )
        assert _contrast(_rgb(message["color"]), _rgb(message["background"])) >= 4.5, message
        if size == "phone":
            assert message["size"] >= 14
        # A select can be invalid too: choose nothing valid by injecting an unknown network, then check its edge.
        page.evaluate(
            "() => { const s = document.querySelector('select[name=\"ws_network\"]'); s.setAttribute('aria-invalid', 'true'); }"
        )
        assert (
            page.locator('select[name="ws_network"]').evaluate(
                "el => getComputedStyle(el).borderTopColor"
            )
            == colors["edge"]
        )
        _shot(page, f"{label}-23-invalid-state")
        # A form-level refusal is the summary's, never pinned on the Name field.
        page.get_by_role("textbox", name="Name", exact=True).fill("Box")
        page.get_by_role("textbox", name="Image address", exact=True).fill("ghcr.io/acme/tools:1.2")
        page.get_by_role("spinbutton", name="CPU cores").fill("2")
        page.get_by_role("button", name="Review and create").click()
        page.route(
            "**/api/compute/workspaces",
            lambda route: (
                route.fulfill(
                    status=409,
                    content_type="application/json",
                    body='{"error":"conflict","field":"request_id","reason":"request_reused"}',
                )
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        page.get_by_role("button", name="Confirm and create").click()
        expect(page.locator("[data-ws-summary]")).to_contain_text(
            "already sent with different values"
        )
        expect(page.locator("[data-ws-summary]")).to_be_focused()
        assert page.locator('.compute-ws [aria-invalid="true"]').count() == 0

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("viewport", [(390, 844), (650, 900)])
def test_the_phone_form_scrolls_by_touch_and_the_action_bar_never_covers_what_matters(
    served, ui, viewport
):
    """A real touch swipe scrolls the form; the sticky bar sits on the tab bar (one offset per look), and the focused
    field, its error and the review sentence are never under it. 650px is inside Deck's phone range (600-699)."""
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="New cloud workspace").click()
        page.get_by_role("textbox", name="Name", exact=True).wait_for()
        before = _scrolled(page)
        _swipe(context, page, viewport[0] // 2, viewport[1] - 220, 120)
        after = _scrolled(page)
        assert after != before and max(after) > 200, (
            "a touch swipe scrolls the page",
            before,
            after,
        )
        _assert_bar_sits_on_the_tab_bar(page)
        _shot(page, f"{label}-{viewport[0]}-29-form-after-swipe")
        # The action bar is one row: Review and Cancel side by side, 44px tall.
        buttons = page.locator(".compute-ws-actions .btn")
        boxes = [buttons.nth(i).bounding_box() for i in range(buttons.count())]
        assert (
            len(boxes) == 2
            and abs(boxes[0]["y"] - boxes[1]["y"]) <= 1
            and min(b["height"] for b in boxes) >= TOUCH
        )
        # Tabbing to the last field keeps it (and its hint) above the bar.
        page.get_by_role("combobox", name="Network", exact=True).focus()
        expect(page.get_by_role("combobox", name="Network", exact=True)).to_be_focused()
        _settled(page)
        assert not _under_the_bar(page, 'select[name="ws_network"]')
        page.get_by_role("combobox", name="Machine", exact=True).focus()
        expect(page.get_by_role("combobox", name="Machine", exact=True)).to_be_focused()
        _settled(page)
        assert not _under_the_bar(page, 'select[name="ws_node_id"]')
        # The first error is brought fully into view, above the bar, with focus on it.
        page.get_by_role("spinbutton", name="Storage (GiB)").fill("0")
        page.get_by_role("textbox", name="Name", exact=True).fill("")
        page.get_by_role("button", name="Review and create").click()
        name = page.get_by_role("textbox", name="Name", exact=True)
        expect(page.locator("[data-ws-summary]")).to_be_focused()
        page.locator("[data-ws-summary]").get_by_role("link", name="Name:").click()
        expect(name).to_be_focused()
        assert not _under_the_bar(page, 'input[name="ws_name"]')
        assert not _under_the_bar(page, "#ws-error-name")
        _shot(page, f"{label}-{viewport[0]}-30-first-error-in-view")
        page.get_by_role("textbox", name="Name", exact=True).fill("Phone box")
        page.get_by_role("spinbutton", name="Storage (GiB)").fill("20")
        # An error on a field lower down is also in view, not under the bar.
        page.get_by_role("spinbutton", name="Storage (GiB)").fill("9999")
        page.get_by_role("button", name="Review and create").click()
        expect(page.locator("[data-ws-summary]")).to_be_focused()
        page.locator("[data-ws-summary]").get_by_role("link", name="Storage:").click()
        expect(page.get_by_role("spinbutton", name="Storage (GiB)")).to_be_focused()
        assert not _under_the_bar(page, 'input[name="ws_storage_gib"]')
        assert not _under_the_bar(page, "#ws-error-storage_gib")
        page.get_by_role("spinbutton", name="Storage (GiB)").fill("20")
        page.get_by_role("button", name="Review and create").click()
        expect(page.get_by_role("heading", name="Create “Phone box”?")).to_be_focused()
        assert not _under_the_bar(page, "[data-ws-honesty]")
        _assert_bar_sits_on_the_tab_bar(page)
        # Touch targets hold at this width, including the Refresh button on Deck.
        _assert_touch_targets(page, inputs=False)
        _no_sideways_scroll(page)
        _shot(page, f"{label}-{viewport[0]}-24-phone-review")

    _run(fx, ui, "phone", body, viewport=viewport)


@pytest.mark.parametrize("ui", UIS)
def test_escape_steps_back_and_saving_keeps_focus_on_the_button(served, ui):
    fx = served
    fx.make(name="Escape box", node_id=fx.node["id"])

    def body(page, label, context):
        section = _section(page)
        page.get_by_role("button", name="Change settings for Escape box").click()
        page.get_by_role("spinbutton", name="Memory (GiB)").fill("8")
        page.get_by_role("button", name="Review the change").click()
        expect(page.locator("#compute-ws-heading")).to_have_text(
            "Confirm the change to “Escape box”"
        )
        # Escape from the review goes back to the form, with the typed value.
        page.keyboard.press("Escape")
        expect(page.get_by_role("spinbutton", name="Memory (GiB)")).to_have_value("8")
        # Escape from a changed form asks before throwing the change away; the safe choice has focus.
        page.keyboard.press("Escape")
        expect(section).to_contain_text("Leave without saving?")
        expect(page.get_by_role("button", name="Keep editing")).to_be_focused()
        _shot(page, f"{label}-25-leave-confirm")
        page.keyboard.press("Escape")
        expect(section).not_to_contain_text("Leave without saving?")
        # Saving keeps focus on the pressed button, marks the section busy and says so in the status line.
        page.get_by_role("button", name="Review the change").click()
        # The save is held by the test (not by a sleep) until the busy state has been checked, then released.
        held = []
        page.route(
            "**/api/compute/workspaces/*",
            lambda route: (
                held.append(route) if route.request.method == "POST" else route.continue_()
            ),
        )
        confirm = page.get_by_role("button", name="Confirm and save")
        confirm.click()
        busy = page.locator(".compute-ws-editor")
        expect(busy).to_have_attribute("aria-busy", "true")
        expect(page.get_by_role("button", name="Saving…")).to_be_focused()
        expect(page.get_by_role("button", name="Saving…")).to_have_attribute(
            "aria-disabled", "true"
        )
        # An aria-disabled button looks disabled (it used to look live).
        assert (
            float(
                page.get_by_role("button", name="Saving…").evaluate(
                    "el => getComputedStyle(el).opacity"
                )
            )
            < 1
        )
        assert page.locator("[data-ws-notice]").inner_text() == "Saving…"
        notice = page.locator("[data-ws-notice]")
        assert len(held) == 1
        held[0].continue_()
        expect(notice).to_contain_text("Saved “Escape box”")
        expect(
            page.locator(".compute-ws").get_by_role("heading", name="Escape box", exact=True)
        ).to_be_focused()

    _run(fx, ui, "desktop", body)


@pytest.mark.parametrize("ui", UIS)
def test_names_that_hide_themselves_are_refused_when_the_field_is_committed(served, ui):
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="New cloud workspace").click()
        name = page.get_by_role("textbox", name="Name", exact=True)
        for hidden in (
            "a\u3164b",
            "ab\uffa0",
            "a\u00a0b",
            "a\u3000b",
            "a\u034fb",
            "a\ufe0fb",
            "\u3164",
            "a\u180fb",
        ):
            name.fill(hidden)
            name.blur()
            expect(page.locator("#ws-error-name")).to_contain_text(
                "Use letters, numbers and normal spaces."
            )
        name.fill("Café 2")
        expect(page.locator("#ws-error-name")).to_have_text("")

    _run(fx, ui, "desktop", body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_typing_is_silent_and_choosing_an_image_does_not_move_the_page(served, ui, size):
    """Errors are written when a field is committed or the form is submitted, never per keystroke (a polite live
    region would announce each one), and changing the image choice shows the address box without scrolling."""
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="New cloud workspace").click()
        page.get_by_role("combobox", name="Image", exact=True).select_option("custom")
        address = page.get_by_role("textbox", name="Image address", exact=True)
        page.evaluate(
            "() => { window.__slotWrites = 0; document.querySelectorAll('[data-ws-error]').forEach(slot =>"
            " new MutationObserver(list => { window.__slotWrites += list.length; }).observe(slot,"
            " {childList: true, characterData: true, subtree: true})); }"
        )
        address.click()
        address.press_sequentially("ghcr.io", delay=30)
        expect(address).to_have_value("ghcr.io")
        assert page.evaluate("() => window.__slotWrites") == 0, "no error is written while typing"
        expect(address).not_to_have_attribute("aria-invalid", "true")
        address.press("Tab")  # committing the field is when its problem is shown
        expect(page.locator("#ws-error-image")).to_contain_text("Add the image name")
        expect(address).to_have_attribute("aria-invalid", "true")
        # Switching between a ready-made image and "My own image" toggles the box in place: no scroll, no focus move.
        choice = page.get_by_role("combobox", name="Image", exact=True)
        choice.select_option("docker.io/library/ubuntu:24.04")
        expect(address).to_be_hidden()
        choice.focus()
        top = page.evaluate(
            "() => { const s = document.querySelector('#content') || document.scrollingElement;"
            " return [s.scrollTop, window.scrollY]; }"
        )
        choice.select_option("custom")
        expect(address).to_be_visible()
        assert (
            page.evaluate(
                "() => { const s = document.querySelector('#content') || document.scrollingElement;"
                " return [s.scrollTop, window.scrollY]; }"
            )
            == top
        ), "choosing an image does not scroll the page"
        expect(choice).to_be_focused()

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_no_machines_enrolled_says_what_to_do(served_empty, ui, size):
    fx = served_empty

    def body(page, label, context):
        page.get_by_role("button", name="New cloud workspace").click()
        machine = page.get_by_role("combobox", name="Machine", exact=True)
        # Nothing to choose: the field says so and is disabled, and no sentence points at the SSH settings.
        expect(machine).to_contain_text("No machines set up yet")
        expect(machine).to_be_disabled()
        expect(_section(page)).to_contain_text("No machine is set up yet, so none can be chosen.")
        # A disabled field looks disabled (dimmed, not an editable box).
        assert float(machine.evaluate("el => getComputedStyle(el).opacity")) < 0.7
        expect(_section(page)).not_to_contain_text("Targets")
        expect(_section(page)).not_to_contain_text("Advanced")
        _shot(page, f"{label}-26-no-machines")
        page.get_by_role("textbox", name="Name", exact=True).fill("Machine-less")
        page.get_by_role("button", name="Review and create").click()
        page.get_by_role("button", name="Confirm and create").click()
        expect(page.locator("[data-ws-id]").first).to_contain_text("Saved, not started")
        expect(page.locator("[data-ws-id]").first).to_contain_text("Machine: not chosen yet")
        _shot(page, f"{label}-26b-no-machines-row")
        page.get_by_role("button", name="Details for Machine-less").click()
        detail = page.locator(".compute-ws-detail")
        expect(detail).to_contain_text(
            "No machine is set up yet. Starting will need one, which is not available yet."
        )
        expect(detail).not_to_contain_text("Choose a machine")
        _shot(page, f"{label}-27-no-machine-detail")

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("size", SCREENS)
def test_the_limit_reached_state_says_why_there_is_no_create_button(served, ui, size):
    fx = served
    with fx.store._journal() as conn:
        for number in range(100):
            conn.execute(
                "INSERT INTO cloud_workspaces (id,name,name_key,image_source,image_reference,"
                "cpu_millicores,memory_mib,storage_gib,network,created_by,created_at,updated_at) "
                "VALUES (?,?,?,'custom','ghcr.io/acme/tools:1',2000,4096,20,'outbound','u',?,?)",
                (f"{number:032x}", f"Box {number}", f"box {number}", float(number), float(number)),
            )

    def body(page, label, context):
        expect(_section(page)).to_contain_text(
            "You have reached the limit of 100 cloud workspaces."
        )
        expect(page.get_by_role("button", name="New cloud workspace")).to_have_count(0)
        _shot(page, f"{label}-28-limit-reached")

    _run(fx, ui, size, body)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("viewport", [(390, 844), (320, 640)])
def test_the_stale_and_leave_rows_wrap_instead_of_clipping_on_a_narrow_phone(served, ui, viewport):
    """Long labels in the stale-edit and leave-confirm rows wrap onto 44px rows: nothing is cut off or overlaps."""
    fx = served
    mine = fx.make(name="Mine", image="ghcr.io/acme/mine:1")
    width = viewport[0]

    def intact(page, group):
        buttons = page.locator(f"{group} .btn")
        count = buttons.count()
        assert count >= 2
        boxes = []
        for index in range(count):
            button = buttons.nth(index)
            assert button.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (
                button.inner_text() + " is clipped"
            )
            assert button.evaluate("el => getComputedStyle(el).whiteSpace") == "normal"
            box = button.bounding_box()
            assert box["height"] >= TOUCH - 0.5, button.inner_text()
            assert box["x"] >= -0.5 and box["x"] + box["width"] <= width + 0.5, (
                "inside the viewport"
            )
            boxes.append(box)
        for first in range(count):
            for second in range(first + 1, count):
                a, b = boxes[first], boxes[second]
                apart = (
                    a["x"] + a["width"] <= b["x"] + 0.5
                    or b["x"] + b["width"] <= a["x"] + 0.5
                    or a["y"] + a["height"] <= b["y"] + 0.5
                    or b["y"] + b["height"] <= a["y"] + 0.5
                )
                assert apart, "buttons do not overlap"
        _no_sideways_scroll(page)

    def body(page, label, context):
        page.get_by_role("button", name="Change settings for Mine").click()
        page.get_by_role("textbox", name="Name", exact=True).fill("Mine, renamed here")
        # The leave confirmation first.
        page.keyboard.press("Escape")
        expect(_section(page)).to_contain_text("Leave without saving?")
        page.locator(".compute-ws-leave").scroll_into_view_if_needed()
        _settled(page)
        intact(page, ".compute-ws-leave")
        _shot(page, f"{label}-25-leave-confirm-{width}")
        page.get_by_role("button", name="Keep editing").click()
        # Then the stale form: the record changes elsewhere.
        fx.store.update_workspace(
            fx.principal, mine["id"], **fx.auth, expected_revision=1, name="Mine, renamed elsewhere"
        )
        page.get_by_role("button", name="Refresh").click()
        expect(page.get_by_role("button", name="Reload to continue")).to_be_visible()
        # The whole screen repaints once more when the refresh ends: measure the stale row after that.
        expect(page.get_by_role("button", name="Refresh")).to_be_enabled()
        page.locator(".compute-ws-stale").scroll_into_view_if_needed()
        _settled(page)
        intact(page, ".compute-ws-stale")
        _shot(page, f"{label}-17-stale-edit-{width}")

    _run(fx, ui, "phone", body, viewport=viewport)


@pytest.mark.parametrize("size", SCREENS)
def test_deck_calls_the_page_cloud_workspaces_and_keeps_the_route_card_on_the_map(served, size):
    fx = served
    fx.make(name="Reachable box")
    width, height = SIZES[size]
    for cookie, owner in ((fx.cookie, True), (fx.member_cookie, False)):
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                phone = size == "phone"
                context = browser.new_context(
                    viewport={"width": width, "height": height}, has_touch=phone, is_mobile=phone
                )
                context.add_cookies([{"name": sessions.COOKIE, "value": cookie, "url": fx.origin}])
                page = context.new_page()
                page.set_default_timeout(20000)
                page.goto(f"{fx.origin}/app/?ui=deck#/compute")
                bar = "nav.m-tabs" if phone else "nav.rail"
                entry = page.locator(f'{bar} a[data-nav="compute"]')
                # The phone tab has room for the short word; its accessible name is the full one.
                expect(entry).to_have_attribute(
                    "aria-label", "Cloud workspaces"
                ) if phone else expect(entry).to_contain_text("Cloud workspaces")
                if phone:
                    expect(entry).to_contain_text("Workspaces")
                    expect(entry).to_be_visible()  # laid out before its text is measured
                    lines = entry.evaluate(
                        "el => { const text = [...el.childNodes].find(n => n.nodeType === 3);"
                        " const r = document.createRange(); r.selectNodeContents(text);"
                        " return new Set([...r.getClientRects()].map(b => Math.round(b.top))).size; }"
                    )
                    assert lines == 1, "the tab word is on one line, not split"
                expect(page.locator("h1").first).to_have_text("Cloud workspaces")
                tabs = page.get_by_role("navigation", name="Cloud workspaces views")
                if owner:
                    expect(tabs.get_by_role("link", name="Map")).to_have_attribute(
                        "aria-current", "page"
                    )
                    expect(page.locator(".dc-explain")).to_contain_text(
                        "The map shows your machines and how they connect."
                    )
                    # The route card sits with the map, not with the management page.
                    expect(page.locator("[data-compute-routes]")).to_have_count(1)
                    expect(page.locator(".dc-route-management")).to_be_visible()
                    _shot(page, f"deck-{size}-01b-map-with-route-card")
                    tabs.get_by_role("link", name="Management").click()
                    page.locator("#compute-workspaces").wait_for()
                    expect(page.locator("[data-compute-routes]")).to_have_count(0)
                    expect(page.locator(".dc-route-management")).to_have_count(0)
                    expect(page.locator("h1").first).to_have_text("Cloud workspaces")
                    _shot(page, f"deck-{size}-01c-management-without-route-card")
                else:
                    # A member has nothing on the map and nothing to manage: the one read-only page, no tab bar.
                    page.locator("#compute-workspaces").wait_for()
                    expect(
                        page.get_by_role("navigation", name="Cloud workspaces views")
                    ).to_have_count(0)
                    expect(page.get_by_role("link", name="Management")).to_have_count(0)
                    expect(
                        _section(page).get_by_role("heading", name="Reachable box")
                    ).to_be_visible()
                    _shot(page, f"deck-{size}-20b-member-no-management-tab")
                context.close()
            finally:
                browser.close()


@pytest.mark.parametrize("ui", UIS)
def test_typing_writes_no_live_region_whose_text_did_not_change(served, ui):
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="New cloud workspace").click()
        memory = page.get_by_role("spinbutton", name="Memory (GiB)")
        memory.fill("0.1")
        memory.press("Tab")  # committed: its error is shown
        expect(page.locator("#ws-error-memory_mib")).not_to_be_empty()
        page.evaluate(
            "() => { window.__writes = 0; document.querySelectorAll('[data-ws-error], [data-ws-preview] p')"
            ".forEach(node => new MutationObserver(list => { window.__writes += list.length; })"
            ".observe(node, {childList: true, characterData: true, subtree: true})); }"
        )
        name = page.get_by_role("textbox", name="Name", exact=True)
        name.click()
        name.press_sequentially("Quiet box", delay=20)
        expect(name).to_have_value("Quiet box")
        assert page.evaluate("() => window.__writes") == 0, (
            "typing in another field rewrote a region"
        )

    _run(fx, ui, "desktop", body)


@pytest.mark.parametrize("ui", UIS)
def test_a_ready_made_image_error_is_shown_on_the_visible_list(served, ui):
    fx = served

    def body(page, label, context):
        page.get_by_role("button", name="New cloud workspace").click()
        page.get_by_role("textbox", name="Name", exact=True).fill("Curated box")
        page.get_by_role("button", name="Review and create").click()
        page.route(
            "**/api/compute/workspaces",
            lambda route: (
                route.fulfill(
                    status=400,
                    content_type="application/json",
                    body='{"error":"invalid_request","field":"image","reason":"not_curated"}',
                )
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        page.get_by_role("button", name="Confirm and create").click()
        choice = page.get_by_role("combobox", name="Image", exact=True)
        expect(choice).to_have_attribute("aria-invalid", "true")
        expect(page.locator("#ws-error-image_choice")).to_contain_text(
            "Pick one of the listed images"
        )
        assert "ws-error-image_choice" in choice.get_attribute("aria-describedby")

    _run(fx, ui, "desktop", body)


VIEWPORTS = [
    pytest.param((1280, 800), False, id="desktop"),
    pytest.param((390, 844), True, id="phone-390"),
    pytest.param((320, 640), True, id="phone-320"),
    pytest.param((768, 1024), True, id="tablet-768-touch"),
]


def _tag(viewport):
    return f"{viewport[0]}"


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("viewport,touch", VIEWPORTS)
def test_card_621_states_at_every_viewport(served, ui, viewport, touch):
    """The review minors of the card, seen in the page: list semantics, header Close, review warning, refused row, stale copy."""
    fx = served
    fx.make(name="Plain box")
    mine = fx.make(name="Mine")
    soon = fx.make(name="Soon started")

    def body(page, label, context):
        tag = f"{label.split('-')[0]}-{_tag(viewport)}"
        section = _section(page)
        # The list is a list, and a record's detail has its Close in the header, beside the name.
        expect(section.locator("ul.compute-ws-list")).to_have_attribute("role", "list")
        _shot(page, f"{tag}-01-list")
        page.get_by_role("button", name="Details for Plain box").click()
        detail = page.locator(".compute-ws-detail")
        header_close = detail.locator("header").get_by_role(
            "button", name="Close details of Plain box"
        )
        expect(header_close).to_be_visible()
        expect(detail.get_by_role("button", name="Close details of Plain box")).to_have_count(1)
        box = header_close.bounding_box()
        assert box["x"] + box["width"] <= viewport[0] + 1, "Close is inside the viewport"
        _shot(page, f"{tag}-02-detail-close-in-header")
        header_close.click()
        expect(page.get_by_role("button", name="Details for Plain box")).to_be_focused()
        # Choosing a machine that is not verified says what that means before anything is saved.
        page.get_by_role("button", name="Change settings for Plain box").click()
        page.get_by_role("combobox", name="Machine", exact=True).select_option(
            label="Build machine"
        )
        page.get_by_role("button", name="Review the change").click()
        review = page.locator(".compute-ws-review")
        expect(review).to_contain_text(
            "Build machine is not verified yet, so nothing can start on it. Your settings are still saved."
        )
        _shot(page, f"{tag}-03-review-machine-not-verified")
        page.get_by_role("button", name="Back").click()
        page.get_by_role("button", name="Cancel").click()
        page.get_by_role("button", name="Leave and lose changes").click()
        # A refused discard names what happened in the row and in the description of the focused button.
        page.get_by_role("button", name="Discard Soon started").click()
        fx.state(soon["id"], "failed", "start_failed")
        page.get_by_role("button", name="Discard workspace", exact=True).click()
        refused = page.locator(f'[data-ws-id="{soon["id"]}"]')
        expect(refused.locator(".compute-ws-status")).to_have_text("Started since you opened this")
        expect(page.get_by_role("button", name="Keep it")).to_be_focused()
        _shot(page, f"{tag}-04-discard-refused")
        page.get_by_role("button", name="Keep it").click()
        # A stale edit: nothing was saved, the form still holds what was typed, and the reload says how to get it back.
        page.get_by_role("button", name="Change settings for Mine").click()
        page.get_by_role("textbox", name="Name", exact=True).fill("Mine, renamed here")
        fx.store.update_workspace(
            fx.principal, mine["id"], **fx.auth, expected_revision=1, name="Mine, renamed elsewhere"
        )
        page.get_by_role("button", name="Refresh").click()
        summary = page.locator("[data-ws-summary]")
        expect(summary).to_contain_text("Nothing was saved.")
        expect(summary).to_contain_text("What you typed is still in the form below.")
        _shot(page, f"{tag}-05-stale-banner")
        page.get_by_role("button", name="Reload to continue").click()
        expect(page.locator("[data-ws-notice]")).to_have_text(
            "Reloaded. Your earlier changes were not saved. Apply my changes again puts them back."
        )
        expect(page.get_by_role("button", name="Apply my changes again")).to_be_visible()
        _shot(page, f"{tag}-06-after-reload")
        _no_sideways_scroll(page)

    _run(fx, ui, "phone" if viewport[0] < 700 else "desktop", body, viewport=viewport, touch=touch)


@pytest.mark.parametrize("ui", UIS)
@pytest.mark.parametrize("viewport,touch", VIEWPORTS)
def test_card_621_the_disabled_machine_field_at_every_viewport(served_empty, ui, viewport, touch):
    fx = served_empty

    def body(page, label, context):
        page.get_by_role("button", name="New cloud workspace").click()
        machine = page.get_by_role("combobox", name="Machine", exact=True)
        expect(machine).to_be_disabled()
        assert float(machine.evaluate("el => getComputedStyle(el).opacity")) == pytest.approx(0.55)
        machine.scroll_into_view_if_needed()
        _settled(page)
        _shot(page, f"{label.split('-')[0]}-{_tag(viewport)}-07-disabled-machine-select")

    _run(fx, ui, "phone" if viewport[0] < 700 else "desktop", body, viewport=viewport, touch=touch)


@pytest.mark.parametrize("ui", UIS)
def test_a_failed_list_load_is_one_alert_and_the_status_line_stays_silent(served, ui):
    fx = served
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            context.add_cookies([{"name": sessions.COOKIE, "value": fx.cookie, "url": fx.origin}])
            context.route(
                re.compile(r".*/api/compute/workspaces$"),
                lambda route: route.fulfill(status=500, json={"error": "x"}),
            )
            page = context.new_page()
            page.set_default_timeout(20000)
            route = "#/compute/management" if ui == "deck" else "#/compute"
            page.goto(f"{fx.origin}/app/?ui={ui}{route}")
            problem = page.locator("[data-ws-problem]")
            sentence = "Cloud workspaces could not be loaded. Reload to try again."
            expect(problem).to_have_text(sentence)
            section = _section(page)
            expect(section.locator('[role="alert"]:not(:empty)')).to_have_count(1)
            expect(page.locator("[data-ws-notice]")).to_have_text("")
            reload = section.get_by_role("button", name="Reload", exact=True)
            reload.click()
            expect(problem).to_have_text(sentence)
            # The status line never says it as well: one voice for one failure.
            expect(page.locator("[data-ws-notice]")).to_have_text("")
            expect(section.locator('[role="alert"]:not(:empty)')).to_have_count(1)
            _shot(page, f"{ui}-desktop-08-load-failed")
            context.close()
        finally:
            browser.close()


def test_the_deck_phone_tab_labels_keep_their_distance_at_320(served):
    fx = served
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(
                viewport={"width": 320, "height": 640}, has_touch=True, is_mobile=True
            )
            context.add_cookies([{"name": sessions.COOKIE, "value": fx.cookie, "url": fx.origin}])
            page = context.new_page()
            page.set_default_timeout(20000)
            page.goto(f"{fx.origin}/app/?ui=deck#/compute")
            page.locator("nav.m-tabs").wait_for()
            labels = page.evaluate(
                "() => [...document.querySelectorAll('nav.m-tabs > :is(a, button)')].map(el => {"
                " const node = [...el.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());"
                " if (!node) return null;"
                " const range = document.createRange(); range.selectNodeContents(node);"
                " const box = range.getBoundingClientRect();"
                " return {text: node.textContent.trim(), left: box.left, right: box.right,"
                " size: getComputedStyle(el).fontSize}; }).filter(Boolean)"
            )
            _shot(page, "deck-320-09-tab-labels")
            assert len(labels) >= 5, labels
            assert {item["size"] for item in labels} == {"11px"}, labels
            for first, second in zip(labels, labels[1:]):
                assert second["left"] - first["right"] >= 1, (first, second)
            assert labels[0]["left"] >= 0 and labels[-1]["right"] <= 320, labels
            workspaces = next(item for item in labels if item["text"] == "Workspaces")
            settings = next(item for item in labels if item["text"] == "Settings")
            assert settings["left"] - workspaces["right"] >= 1, (workspaces, settings)
            context.close()
        finally:
            browser.close()
