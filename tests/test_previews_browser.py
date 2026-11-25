"""A hostile preview in a real browser: its own files load, the viewer's session does not, and it
cannot move the page the person is looking at.

Needs the Playwright package and a Chromium cache; Playwright is deliberately not a declared
dependency (as for the other browser suites), so run

    uv run --with playwright==1.64.1 pytest tests/test_previews_browser.py -rs

Without Playwright the case is reported as skipped with a reason; with ``CP_BROWSER_E2E=1`` a
missing Playwright fails instead. Synthetic local data only.
"""

import importlib.util
import json
import os
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolContext
from commonplace.gateway import Gateway
from commonplace.previews import service, store
from commonplace.workers import WorkerSupervisor

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
if os.environ.get("CP_BROWSER_E2E") == "1" or HAS_PLAYWRIGHT:
    from playwright import sync_api as playwright_api
else:
    playwright_api = None
pytestmark = pytest.mark.skipif(
    playwright_api is None,
    reason="browser cases need the playwright package (see this file's docstring for the command)",
)

PROBE = """
const out = {};
try { out.cookie = document.cookie === "" ? "empty" : "read"; } catch (e) { out.cookie = "blocked"; }
try { localStorage.setItem("probe", "1"); out.storage = "open"; } catch (e) { out.storage = "blocked"; }
out.origin = String(self.origin);
fetch("/api/previews", { credentials: "include" })
  .then((r) => { out.fetch = "status " + r.status; })
  .catch(() => { out.fetch = "blocked"; })
  .finally(() => { document.body.dataset.result = JSON.stringify(out); });
"""
FILES = [
    {
        "path": "index.html",
        "text": "<!doctype html><title>Probe</title><link rel=stylesheet href=app.css>"
        "<h1>Probe</h1><a id=docs href=docs/>Docs</a>"
        "<form id=leak method=post action=/api/previews><button>Send</button></form>"
        "<script src=app.js></script>",
    },
    {"path": "docs/index.html", "text": "<!doctype html><title>Docs</title><h1>Docs page</h1>"},
    {"path": "app.css", "text": "h1 { color: rgb(1, 2, 3); }"},
    {"path": "app.js", "text": PROBE},
]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A running gateway with one signed-in owner and one card; previews are published by tests."""
    for name in ("CHAT", "HOLD"):
        monkeypatch.setenv(f"COMMONPLACE_{name}_WORKER", "off")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    for name in ("COMMONPLACE_ALLOWED_HOSTS", "COMMONPLACE_APP_URL", "COMMONPLACE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    gateway = Gateway(db, WorkerSupervisor(str(tmp_path), launcher=object()))
    owner = db.create_user("owner@synthetic.test")
    wid = db.create_workspace("Preview browser fixture", owner)
    cookie = sessions.start_session(db, owner, wid)
    person = sessions.session_principal(db, cookie)
    board = BoardService(ToolContext(principal=person, gateway=gateway))
    board.board_add("work", wing="fixture")
    number = board.task_add(board="work", title="Probe page")["number"]
    server = gateway.build_server("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(
            origin=f"http://127.0.0.1:{server.server_address[1]}",
            cookie=cookie,
            gateway=gateway,
            person=person,
            number=number,
        )
    finally:
        server.shutdown()
        server.server_close()
        db.close()


@pytest.fixture
def served(world):
    shown, _ = service.publish(world.gateway, world.person, world.number, "probe", FILES)
    return world.origin, world.cookie, shown["id"], world.number


def test_a_preview_loads_its_own_files_but_never_the_viewers_session(served):
    origin, cookie, identity, _number = served
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": sessions.COOKIE, "value": cookie, "url": origin}])
            page = context.new_page()
            page.set_default_timeout(20000)
            seen = []
            page.on("console", lambda m: seen.append(f"console {m.type}: {m.text}"))
            page.on("pageerror", lambda e: seen.append(f"error: {e}"))
            page.on("requestfailed", lambda r: seen.append(f"failed: {r.url} {r.failure}"))
            page.on("response", lambda r: seen.append(f"response: {r.status} {r.url}"))
            page.goto(f"{origin}/p/{identity}/")
            # The tab shows the viewer; the preview runs in its frame.
            frame = page.frame_locator("iframe")
            # Waiting by selector, not by an evaluated string: the page's policy forbids eval.
            try:
                frame.locator("body[data-result]").wait_for(state="attached", timeout=5000)
            except playwright_api.TimeoutError:
                pytest.fail("no result: " + " | ".join(seen))
            result = json.loads(frame.locator("body").get_attribute("data-result"))
            # Its own stylesheet and script loaded under the sandbox policy ...
            assert frame.locator("h1").evaluate("e => getComputedStyle(e).color") == "rgb(1, 2, 3)"
            # ... but the page has no origin of its own, so the session stays out of reach.
            assert result == {
                "cookie": "blocked",
                "storage": "blocked",
                "origin": "null",
                "fetch": "blocked",
            }
            context.close()
        finally:
            browser.close()


def test_links_lead_on_forms_post_nothing_and_the_key_ends_after_thirty_minutes(
    served, monkeypatch
):
    origin, cookie, identity, number = served
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": sessions.COOKIE, "value": cookie, "url": origin}])
            page = context.new_page()
            page.set_default_timeout(20000)
            posts = []
            page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
            page.goto(f"{origin}/p/{identity}/")
            keyed = page.url
            assert "/~" in keyed and keyed.endswith("/")
            frame = page.frame_locator("iframe")
            # A link between the preview's own pages works inside the frame; the tab stays put.
            frame.locator("#docs").click()
            frame.locator("h1", has_text="Docs page").wait_for()
            assert page.url == keyed
            assert page.frames[1].url == keyed + "~files/docs/"
            # A form in the preview posts nothing anywhere (the frame may not submit forms).
            page.goto(keyed)
            # The browser reports the refusal on the console: wait for that, not for a timer.
            with page.expect_event(
                "console", predicate=lambda m: "Blocked form submission" in m.text, timeout=10000
            ):
                frame.locator("#leak button").click(no_wait_after=True)
            assert posts == []
            # The address the browser shows is a key: a browser with no session opens it ...
            anonymous = browser.new_context()
            stranger = anonymous.new_page()
            assert stranger.goto(keyed).status == 200
            stranger.frame_locator("iframe").locator("h1", has_text="Probe").wait_for()
            shot(stranger, "served-viewer")
            # ... until it ends after thirty minutes, and then it gets the sign-in page.
            moment = store.clock()
            monkeypatch.setattr(store, "clock", lambda: moment + timedelta(minutes=31))
            assert stranger.goto(keyed).status == 401
            stranger.get_by_role("link", name="Sign in to Commonplace").wait_for()
            shot(stranger, "served-401-sign-in")
            anonymous.close()
            monkeypatch.setattr(store, "clock", lambda: moment)
            # A served page links on even though it allows no script: the stopped page leads to
            # the card, and the link works in a sandboxed page.
            gateway_stop(origin, cookie, identity)
            response = page.goto(f"{origin}/p/{identity}/")
            assert response.status == 410
            shot(page, "served-410-stopped")
            page.get_by_role("link", name=f"Open card #{number} in Commonplace").click()
            page.wait_for_url(f"{origin}/app/#/card/{number}")
            context.close()
        finally:
            browser.close()


def test_the_viewer_is_never_framed_and_a_file_opened_as_a_page_shows_the_viewer(served):
    origin, cookie, identity, _number = served
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": sessions.COOKIE, "value": cookie, "url": origin}])
            page = context.new_page()
            page.goto(f"{origin}/p/{identity}/")
            keyed = page.url
            # The file address opened in a tab of its own lands on the viewer, not on the file.
            page.goto(keyed + "~files/docs/")
            assert page.url == keyed + "docs/"
            assert page.locator("iframe").get_attribute("sandbox") == "allow-scripts"
            context.close()
        finally:
            browser.close()


APP_HOST = "app.example.test"
SHARED_HOST = "cmmn.example.test"  # same registrable domain as the app: shares its session cookie
OTHER_HOST = "elsewhere.test"  # a different domain
RESOLVER = "--host-resolver-rules=" + ",".join(
    f"MAP {host} 127.0.0.1" for host in (APP_HOST, SHARED_HOST, OTHER_HOST)
)


@pytest.fixture
def elsewhere():
    """A second site on this machine, reachable under two other host names. It keeps the headers
    of every request it gets."""
    received = []

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(dict(self.headers))
            body = b"<!doctype html><title>Elsewhere</title><h1>Elsewhere</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

        def log_message(self, *args):
            pass

    site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    try:
        yield site.server_address[1], received
    finally:
        site.shutdown()
        site.server_close()


# Each attempt a hostile page can make to move the tab or reach another site, with what the
# browser says when it refuses.
NAVIGATORS = {
    "location-href": ("location.href = %s;", "frame-src"),
    "location-replace": ("location.replace(%s);", "frame-src"),
    "top-location": ("top.location = %s;", "navigation"),
    "parent-location": ("parent.location.href = %s;", "navigation"),
    "window-open": ("window.open(%s);", "popup"),
    "window-open-top": ("window.open(%s, '_top');", "navigation"),
    "meta-refresh": (
        "document.head.insertAdjacentHTML('beforeend', "
        "'<meta http-equiv=refresh content=\"0;url=' + %s + '\">');",
        "frame-src",
    ),
    "form-submit": (
        "const f = document.createElement('form'); f.action = %s; f.target = '_top'; "
        "document.body.append(f); f.submit();",
        "Blocked form submission",
    ),
    "link-to-top": (
        "const a = document.createElement('a'); a.href = %s; a.target = '_top'; "
        "document.body.append(a); a.click();",
        "navigation",
    ),
}
REFUSED = {
    "frame-src": ("Framing", "frame-src"),
    "navigation": ("Unsafe attempt to initiate navigation", "sandboxed"),
    "popup": ("Blocked opening", "allow-popups"),
    "Blocked form submission": ("Blocked form submission", "sandbox"),
}


def test_a_framed_preview_cannot_move_the_page_or_reach_another_site(world, elsewhere):
    """A hostile preview tries every way it knows to send the tab to another site, once to a
    host that shares the app's cookie domain (where the session cookie would be sent) and once to
    an unrelated host. The tab stays on the viewer, the other site is never asked for anything
    and the browser says why it refused. On the old design (the preview as the tab's own page)
    every navigation here succeeded and carried the shared-domain session cookie along."""
    port, received = elsewhere
    targets = {
        "shared": f"http://{SHARED_HOST}:{port}/oauth/authorize?client_id=x",
        "other": f"http://{OTHER_HOST}:{port}/",
    }
    previews = {}
    for label, target in targets.items():
        files = [{"path": "index.html", "text": "<!doctype html><title>Hostile</title>"}]
        for how, (code, _why) in NAVIGATORS.items():
            files.append(
                {
                    "path": f"{how}.html",
                    "text": "<!doctype html><title>Hostile</title><h1>Hostile</h1><script>"
                    + (code % json.dumps(target))
                    + "</script>",
                }
            )
        shown, _ = service.publish(world.gateway, world.person, world.number, label, files)
        previews[label] = shown["id"]
    app = f"http://{APP_HOST}:{world.origin.rsplit(':', 1)[1]}"
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch(args=[RESOLVER])
        try:
            context = browser.new_context()
            context.add_cookies(
                [
                    {
                        "name": sessions.COOKIE,
                        "value": world.cookie,
                        "domain": ".example.test",
                        "path": "/",
                    }
                ]
            )
            page = context.new_page()
            page.set_default_timeout(20000)
            seen, problems = [], []
            page.on("console", lambda m: seen.append(m.text))
            # The fixture is honest: opened as a page, the shared host does get the session cookie.
            page.goto(targets["shared"])
            assert world.cookie in received.pop().get("Cookie", "")
            page.goto(f"{app}/p/{previews['shared']}/")
            keyed = page.url
            for label, identity in previews.items():
                for how, (_code, refusal) in NAVIGATORS.items():
                    prefix, tail = REFUSED[refusal]
                    page.goto(f"{app}/p/{identity}/")
                    base = page.url
                    seen.clear()
                    try:
                        with page.expect_event(
                            "console",
                            predicate=lambda m, prefix=prefix, tail=tail: (
                                m.text.startswith(prefix) and tail in m.text
                            ),
                            timeout=10000,
                        ):
                            page.goto(f"{base}{how}.html")
                    except playwright_api.TimeoutError:
                        problems.append(f"{label} {how}: the browser said {seen}")
                        continue
                    assert page.url == f"{base}{how}.html", (label, how, page.url)
                    assert len(page.frames) == 2
            assert not problems, "\n".join(problems)
            assert received == [], received
            assert keyed.startswith(app)
            context.close()
        finally:
            browser.close()


def shot(page, name):
    """With PREVIEW_SHOTS set, keep a screenshot of a served page (desktop and phone)."""
    target = os.environ.get("PREVIEW_SHOTS")
    if not target:
        return
    from pathlib import Path

    Path(target).mkdir(parents=True, exist_ok=True)
    for label, size in (("desktop", (1280, 800)), ("phone", (390, 844))):
        page.set_viewport_size({"width": size[0], "height": size[1]})
        page.screenshot(path=str(Path(target) / f"{name}-{label}.png"))


def gateway_stop(origin, cookie, identity):
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(origin)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    try:
        conn.request(
            "POST",
            f"/api/previews/{identity}/stop",
            b"{}",
            {"Cookie": f"{sessions.COOKIE}={cookie}", "Content-Type": "application/json"},
        )
        assert conn.getresponse().status == 200
    finally:
        conn.close()
