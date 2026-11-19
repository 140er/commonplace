"""Persistent previews, slice 2: ``/p/<id>/`` serves a ready preview's files to people who can
open its card, with the isolation headers, plain state pages, the stop rule and path discipline.

Real local HTTP over the console fixture; time is the preview module's own clock, which the
tests move. Nothing sleeps.
"""

import base64
import http.client
import json
import shutil
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.previews import links, service, serving, store
from commonplace.workers import WorkerSupervisor
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service as boards

INDEX = "<!doctype html><title>Demo</title><link rel=stylesheet href=assets/app.css><h1>Hello</h1>"
PAGE = [
    {"path": "index.html", "text": INDEX},
    {"path": "assets/app.css", "text": "h1{color:rgb(1, 2, 3)}"},
    {"path": "assets/app.js", "text": "document.body.dataset.ran = '1';"},
    {"path": "docs/index.html", "text": "<p>Docs</p>"},
    {"path": "blob.bin", "base64": base64.b64encode(bytes(range(16))).decode()},
]


def fetch(h, auth, path, method="GET", body=None, headers=None, port=None):
    """``(status, headers, bytes)`` without assuming JSON."""
    conn = http.client.HTTPConnection("127.0.0.1", port or h.hub.port, timeout=30)
    sent = dict(headers or {})
    if isinstance(auth, dict):
        sent.update(auth)
    elif auth:
        sent["Authorization"] = "Bearer " + auth
    try:
        conn.request(method, path, body, sent)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def visit(h, auth, path, port=None):
    """Open ``path`` the way a browser tab does: follow the server's redirects, signed in on
    every hop. Returns the final ``(status, headers, bytes)``."""
    for _hop in range(4):
        status, headers, body = fetch(h, auth, path, port=port)
        if status not in (303, 308):
            return status, headers, body
        assert "Set-Cookie" not in headers
        path = headers["Location"]
    raise AssertionError("too many redirects")


def card(h, title="Build the page"):
    status, body, _ = h.request(h.owner, "POST", "/api/boards/work/cards", {"title": title})
    assert status == 201, body
    return body["number"]


def published(h, files=None, name="home"):
    number = card(h)
    shown, _ = service.publish(h.gateway, h.person(h.hub.admin), number, name, files or PAGE)
    return number, shown["id"]


def folder(h, identity, revision=1):
    return store.revision_dir(h.gateway, h.workspace, identity, revision)


# ── serving ──


def test_a_ready_preview_serves_its_files_with_the_isolation_headers(console_hub):
    h = console_hub
    _number, identity = published(h)
    status, headers, body = visit(h, h.owner, f"/p/{identity}/")
    assert status == 200 and body.decode() == INDEX
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    policy = headers["Content-Security-Policy"]
    assert policy == serving.FILE_POLICY
    assert policy.startswith("sandbox allow-scripts allow-forms allow-popups;")
    assert "allow-same-origin" not in policy and "allow-top-navigation" not in policy
    assert "connect-src 'none'" in policy and "frame-ancestors 'self'" in policy
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "private, no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    # A resource or opener policy would break the sandboxed page's own files (see serving.py).
    assert "Cross-Origin-Resource-Policy" not in headers
    assert "Set-Cookie" not in headers and "Cross-Origin-Opener-Policy" not in headers
    status, headers, body = visit(h, h.owner, f"/p/{identity}/assets/app.css")
    assert status == 200 and headers["Content-Type"] == "text/css; charset=utf-8"
    status, headers, body = visit(h, h.owner, f"/p/{identity}/assets/app.js")
    assert headers["Content-Type"] == "text/javascript; charset=utf-8"
    status, headers, body = visit(h, h.owner, f"/p/{identity}/docs/")
    assert status == 200 and body == b"<p>Docs</p>"
    status, headers, body = visit(h, h.owner, f"/p/{identity}/blob.bin")
    assert status == 200 and body == bytes(range(16))
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["Content-Disposition"] == "attachment"
    status, headers, _ = fetch(h, h.owner, f"/p/{identity}")
    assert status == 308 and headers["Location"] == f"/p/{identity}/"
    # A query string is ignored: it never selects another file.
    assert visit(h, h.owner, f"/p/{identity}/?path=../../x")[2].decode() == INDEX


def test_a_member_opens_it_and_everyone_else_gets_one_answer(console_hub):
    h = console_hub
    _number, identity = published(h)
    assert visit(h, h.member, f"/p/{identity}/")[0] == 200
    unknown = fetch(h, h.owner, "/p/pv_0000000000000000/")
    foreign = fetch(h, h.other, f"/p/{identity}/")
    malformed = fetch(h, h.owner, "/p/not-an-id/")
    assert unknown[0] == foreign[0] == malformed[0] == 404
    assert unknown[2] == foreign[2] == malformed[2]
    assert b"Preview not found" in unknown[2] and INDEX.encode() not in foreign[2]
    # A signed-out browser is sent to sign in, in words, and sees nothing of the preview.
    status, headers, body = fetch(h, None, f"/p/{identity}/")
    assert status == 401 and b"Sign in to open this preview" in body
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Content-Security-Policy"] == serving.PAGE_POLICY
    assert identity.encode() not in body and INDEX.encode() not in body
    # Signed out, an unknown id answers exactly like a real one: nothing to tell them apart.
    assert fetch(h, None, "/p/pv_0000000000000000/")[2] == body
    # Agent tokens use the tools; the files are for people.
    status, _headers, body = fetch(h, h.t1, f"/p/{identity}/")
    assert status == 403 and INDEX.encode() not in body


@pytest.mark.parametrize(
    "rest",
    [
        "../index.html",
        "..%2Findex.html",
        "%2e%2e/%2e%2e/control.sqlite3",
        "assets/../../r1/index.html",
        "assets%5C..%5Cindex.html",
        "index.html%00.css",
        ".hidden",
        "assets//app.css",
        "C:/x",
        "x/" * 11 + "y",
        "%ff",
        "missing.html",
        "assets",
    ],
)
def test_paths_never_leave_the_revision_folder(console_hub, rest):
    h = console_hub
    _number, identity = published(h)
    secret = Path(h.gateway.workspace_dir(h.workspace)) / "previews" / "outside.txt"
    secret.write_text("outside the revision")
    status, headers, body = visit(h, h.owner, f"/p/{identity}/{rest}")
    assert status == 404, (rest, status)
    assert (
        b"outside the revision" not in body
        and headers["Content-Security-Policy"] == serving.PAGE_POLICY
    )


def test_a_link_inside_the_revision_is_never_followed(console_hub):
    h = console_hub
    _number, identity = published(h)
    outside = Path(h.gateway.workspace_dir(h.workspace)) / "secret.txt"
    outside.write_text("not for the preview")
    (folder(h, identity) / "link.txt").symlink_to(outside)
    (folder(h, identity) / "linked").symlink_to(outside.parent, target_is_directory=True)
    for rest in ("link.txt", "linked/secret.txt"):
        status, _headers, body = visit(h, h.owner, f"/p/{identity}/{rest}")
        assert status == 404 and b"not for the preview" not in body


# ── states, each with its page ──


def test_every_state_has_its_page_and_status(console_hub):
    h = console_hub
    number, identity = published(h)
    assert h.request(h.member, "POST", f"/api/previews/{identity}/stop", {})[0] == 200
    status, headers, body = visit(h, h.owner, f"/p/{identity}/")
    assert status == 410 and b"This preview is stopped" in body and b"Stopped by a person" in body
    assert INDEX.encode() not in body
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/open-again", {})[0] == 200
    assert visit(h, h.owner, f"/p/{identity}/")[0] == 200
    now = store.stamp(store.clock())
    with h.db._lock, h.db._tx() as conn:
        for preview_id, state, reason in (
            ("pv_00000000000000aa", "failed", "no_index_html"),
            ("pv_00000000000000bb", "building", "storing"),
        ):
            conn.execute(
                "INSERT INTO previews(id,workspace_id,card,name,state,reason,actor,published_by,"
                "by_person,created_at,updated_at) VALUES (?,?,?,?,?,?,'conn:a1','a1',0,?,?)",
                (preview_id, h.workspace, number, state, state, reason, now, now),
            )
    status, _headers, body = fetch(h, h.owner, "/p/pv_00000000000000aa/")
    assert status == 503 and b"could not be published" in body and b"index.html" in body
    status, headers, body = fetch(h, h.owner, "/p/pv_00000000000000bb/")
    assert status == 503 and headers["Retry-After"] == "30" and b"being published" in body


def test_missing_files_on_the_server_say_so_instead_of_an_empty_page(console_hub):
    h = console_hub
    _number, identity = published(h)
    shutil.rmtree(folder(h, identity))
    status, _headers, body = visit(h, h.owner, f"/p/{identity}/")
    assert status == 503 and b"could not be saved on the server" in body


# ── the stop rule, applied when a preview is read or opened ──


def test_the_age_rule_stops_a_preview_once_its_time_has_passed_and_open_again_restarts_it(
    console_hub, monkeypatch
):
    h = console_hub
    number, identity = published(h)
    assert h.request(h.owner, "PUT", "/api/previews/rules", {"max_age_days": 3})[0] == 200
    listed = h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"][0]
    assert listed["state"] == "ready" and listed["stops_at"] is not None
    moment = store.clock()
    monkeypatch.setattr(store, "clock", lambda: moment + timedelta(days=2))
    assert visit(h, h.owner, f"/p/{identity}/")[0] == 200
    monkeypatch.setattr(store, "clock", lambda: moment + timedelta(days=3, seconds=1))
    status, _headers, body = visit(h, h.owner, f"/p/{identity}/")
    assert status == 410 and b"stop rule" in body
    listed = h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"][0]
    assert (listed["state"], listed["reason"], listed["stops_at"]) == ("stopped", "expired", None)
    assert folder(h, identity).is_dir()  # stopping keeps the files
    with h.db._lock:
        kinds = [
            (r["kind"], r["actor"], r["reason"])
            for r in h.db._conn.execute(
                "SELECT * FROM preview_events WHERE preview_id=?", (identity,)
            )
        ]
    assert kinds.count(("expired", "rule", "expired")) == 1
    # Opening it again gives it the rule's full time again.
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/open-again", {})[0] == 200
    assert visit(h, h.owner, f"/p/{identity}/")[0] == 200
    monkeypatch.setattr(store, "clock", lambda: moment + timedelta(days=6, seconds=2))
    assert visit(h, h.owner, f"/p/{identity}/")[0] == 410


def test_the_pull_request_rule_counts_from_the_merge_report(console_hub, monkeypatch):
    h = console_hub
    number, identity = published(h)
    moment = store.clock()
    with boards(h, h.person(h.hub.admin)).store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET pr_url=?, pr_state='merged', pr_reported_at=? WHERE id=?",
            (
                "https://github.com/example/app/pull/3",
                store.stamp(moment + timedelta(days=1)),
                number,
            ),
        )
    assert h.request(h.owner, "PUT", "/api/previews/rules", {"stop_days_after_pr": 7})[0] == 200
    listed = h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"][0]
    assert listed["stops_at"] == store.stamp(moment + timedelta(days=8))
    monkeypatch.setattr(store, "clock", lambda: moment + timedelta(days=8, seconds=1))
    listed = h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"][0]
    assert (listed["state"], listed["reason"]) == ("stopped", "pr_merged")
    assert visit(h, h.owner, f"/p/{identity}/")[0] == 410


def test_with_the_rule_off_nothing_ever_stops_by_itself(console_hub, monkeypatch):
    h = console_hub
    number, identity = published(h)
    moment = store.clock()
    monkeypatch.setattr(store, "clock", lambda: moment + timedelta(days=4000))
    assert visit(h, h.owner, f"/p/{identity}/")[0] == 200
    assert (
        h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"][0]["stops_at"]
        is None
    )


# ── hostile files and restarts ──

HOSTILE = """<!doctype html><title>Hostile</title>
<script>
  let cookie; try { cookie = document.cookie; } catch (e) { cookie = 'blocked'; }
  fetch('/api/previews').then(() => document.title = 'leaked').catch(() => {});
</script>
<form method=post action="/api/previews/{id}/stop"><button>Stop</button></form>
"""


def test_a_hostile_preview_cannot_act_as_the_person_viewing_it(console_hub):
    h = console_hub
    number = card(h)
    shown, _ = service.publish(
        h.gateway,
        h.person(h.hub.admin),
        number,
        "hostile",
        [{"path": "index.html", "text": HOSTILE}],
    )
    identity = shown["id"]
    status, headers, body = visit(h, h.owner, f"/p/{identity}/")
    assert status == 200 and b"document.cookie" in body  # served verbatim, never rewritten
    policy = headers["Content-Security-Policy"]
    assert (
        "sandbox" in policy and "allow-same-origin" not in policy and "connect-src 'none'" in policy
    )
    # What the browser would send for the form above from the sandboxed (opaque) page: an
    # ``Origin: null`` request. Even with the session cookie attached it changes nothing.
    for kind, raw in (
        ("application/x-www-form-urlencoded", b""),
        ("application/json", b"{}"),
    ):
        status, _headers, _body = fetch(
            h,
            h.owner,
            f"/api/previews/{identity}/stop",
            method="POST",
            body=raw,
            headers={"Origin": "null", "Content-Type": kind, "Content-Length": str(len(raw))},
        )
        assert status in (400, 403, 415), (kind, status)
    assert h.request(h.owner, "GET", f"/api/previews/{identity}")[1]["state"] == "ready"


def test_a_preview_is_still_served_by_a_restarted_server(console_hub):
    h = console_hub
    _number, identity = published(h)
    db = ControlDB(h.hub.db.path)
    gateway = Gateway(db, WorkerSupervisor(str(Path(h.hub.db.path).parent), launcher=object()))
    server = gateway.build_server("127.0.0.1", 0)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    try:
        status, _headers, body = visit(h, h.owner, f"/p/{identity}/", port=server.server_address[1])
        assert status == 200 and body.decode() == INDEX
    finally:
        server.shutdown()
        server.server_close()
        db.close()


def test_the_state_pages_quote_nothing_from_the_request(console_hub):
    h = console_hub
    _number, identity = published(h)
    status, _headers, body = visit(
        h, h.owner, f"/p/{identity}/%3Cscript%3Ealert(1)%3C%2Fscript%3E.html"
    )
    assert status == 404 and b"<script>" not in body and b"alert(1)" not in body


# ── the short-lived page link ──


def link(h, auth, identity, rest=""):
    status, headers, _ = fetch(h, auth, f"/p/{identity}/{rest}")
    assert status == 303, status
    location = headers["Location"]
    assert location.startswith(f"/p/{identity}/~") and location.endswith(f"/{rest}")
    return location


def test_the_page_link_opens_the_files_without_a_session_and_only_for_that_preview(console_hub):
    h = console_hub
    _number, identity = published(h)
    _other_number, other = published(h, name="second")
    location = link(h, h.owner, identity)
    # The page's own requests carry no cookie: the link stands in for the session.
    assert fetch(h, None, location)[2].decode() == INDEX
    assert fetch(h, None, location + "assets/app.css")[0] == 200
    token = location.split("/~", 1)[1].rstrip("/")
    with h.db._lock:
        stored = [dict(r) for r in h.db._conn.execute("SELECT * FROM preview_links")]
    assert len(stored) == 1 and token not in json_text(stored)  # only a digest is kept
    # It names one preview: on another it is unknown, and a signed-in browser starts again.
    assert fetch(h, None, f"/p/{other}/~{token}/")[0] == 401
    status, headers, _ = fetch(h, h.owner, f"/p/{other}/~{token}/")
    assert status == 303 and headers["Location"] == f"/p/{other}/"
    for bad in ("short", "x" * 40, "~" * 30):
        assert fetch(h, None, f"/p/{identity}/~{bad}/")[0] == 401


def test_the_page_link_is_checked_again_on_every_request(console_hub, monkeypatch):
    h = console_hub
    _number, identity = published(h)
    location = link(h, h.member, identity)
    assert fetch(h, None, location)[0] == 200
    # Another person's browser never rides on it: it starts again from the plain address.
    status, headers, _ = fetch(h, h.owner, location)
    assert status == 303 and headers["Location"] == f"/p/{identity}/"
    # A stopped preview is stopped under the link too.
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/stop", {})[0] == 200
    assert fetch(h, None, location)[0] == 410
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/open-again", {})[0] == 200
    # A person who leaves the workspace loses the link at once.
    h.db.remove_member(h.workspace, h.member_user, actor=h.hub.admin, dry_run=False)
    assert fetch(h, None, location)[0] == 401
    # And every link ends: after its time it is unknown.
    location = link(h, h.owner, identity)
    moment = store.clock()
    monkeypatch.setattr(store, "clock", lambda: moment + links.TTL + timedelta(seconds=1))
    assert fetch(h, None, location)[0] == 401
    status, headers, _ = fetch(h, h.owner, location)
    assert status == 303 and headers["Location"] == f"/p/{identity}/"
    # Making the next link removes the ones that have ended.
    link(h, h.owner, identity)
    with h.db._lock:
        left = h.db._conn.execute(
            "SELECT count(*) FROM preview_links WHERE expires_at<?", (store.stamp(store.clock()),)
        ).fetchone()[0]
    assert left == 0


def json_text(value):
    return json.dumps(value)
