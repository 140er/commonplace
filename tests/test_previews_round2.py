"""Persistent previews, review round 1 fixes (server and serving): honest page links, person
words on the card, served pages that lead somewhere, and a Coworker that cannot undo a person.

Real local HTTP over the console fixture; time is the preview module's own clock. Nothing sleeps.
"""

import http.client
import sqlite3
import threading
from datetime import timedelta

from commonplace.previews import links, serving, store
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import mcp, service as boards

PAGE = [
    {"path": "index.html", "text": "<h1>Hello</h1>"},
    {"path": "assets/app.css", "text": "body{color:#222}"},
]


def fetch(h, auth, path):
    conn = http.client.HTTPConnection("127.0.0.1", h.hub.port, timeout=30)
    headers = dict(auth) if isinstance(auth, dict) else {}
    try:
        conn.request("GET", path, None, headers)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def card(h, title="Build the page"):
    status, body, _ = h.request(h.owner, "POST", "/api/boards/work/cards", {"title": title})
    assert status == 201, body
    return body["number"]


def published(h, name="home", files=None):
    number = card(h)
    ok, text = mcp(h, h.t1, "preview_publish", card=number, name=name, files=files or PAGE)
    assert ok, text
    listed = h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"]
    return number, next(p for p in listed if p["name"] == name)["id"]


def link_count(h):
    with h.db._lock:
        return h.db._conn.execute("SELECT count(*) FROM preview_links").fetchone()[0]


def history(h, number):
    shown = boards(h, h.person(h.hub.admin)).task_show(number)
    return (
        [e.get("text") or "" for e in shown.get("events", [])]
        if isinstance(shown, dict)
        else [str(shown)]
    )


# ── M1: the page link is a short-lived key, and every text says so ──


def test_a_page_link_lasts_thirty_minutes_and_a_person_keeps_at_most_three(
    console_hub, monkeypatch
):
    h = console_hub
    _number, identity = published(h)
    assert links.TTL == timedelta(minutes=30)
    status, headers, _ = fetch(h, h.owner, f"/p/{identity}/")
    assert status == 303
    location = headers["Location"]
    # Within its time the exact address opens the preview without a session: it is a key.
    assert fetch(h, None, location)[0] == 200
    moment = store.clock()
    monkeypatch.setattr(store, "clock", lambda: moment + timedelta(minutes=29))
    assert fetch(h, None, location)[0] == 200
    monkeypatch.setattr(store, "clock", lambda: moment + timedelta(minutes=30, seconds=1))
    assert fetch(h, None, location)[0] == 401
    monkeypatch.setattr(store, "clock", lambda: moment)
    made = [fetch(h, h.owner, f"/p/{identity}/")[1]["Location"] for _ in range(4)]
    assert link_count(h) <= links.KEEP
    assert fetch(h, None, made[0])[0] == 401 and fetch(h, None, made[-1])[0] == 200


def test_no_key_is_made_for_an_address_that_names_no_file(console_hub):
    h = console_hub
    _number, identity = published(h)
    before = link_count(h)
    for rest in ("missing.html", "assets", "assets/nope.css", "..%2Findex.html"):
        status, headers, body = fetch(h, h.owner, f"/p/{identity}/{rest}")
        assert status == 404 and "Location" not in headers, rest
    assert link_count(h) == before


def test_the_page_policy_refuses_form_posts_and_the_texts_name_the_key():
    assert "form-action 'none'" in serving.FILE_POLICY
    from commonplace import extensions

    text = extensions.get("preview_publish").description
    assert "30 minutes" in text and "static" in text and "relative" in text
    assert "no network requests, cookies or storage" in text


# ── M2, M3: what a stored preview says to people on the card ──


def test_a_refused_attempt_says_when_it_started_and_what_a_person_can_do(console_hub):
    h = console_hub
    number = card(h)
    leak = "ghp_" + "0" * 36
    ok, text = mcp(
        h,
        h.t1,
        "preview_publish",
        card=number,
        name="home",
        files=[
            {"path": "index.html", "text": "x"},
            {"path": "config/env.js", "text": f"t='{leak}'"},
        ],
    )
    # The Coworker gets its own words; the value is never repeated.
    assert not ok and "Remove the credential from that file" in text and leak not in text
    shown = h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"][0]
    assert shown["state"] == "failed" and shown["published_at"] is None and shown["started_at"]
    assert shown["failure"] == {"file": "config/env.js", "kind": "github_token"}
    assert shown["reason_text"].startswith("Nothing was published")
    assert (
        "Ask the Coworker that published it" in shown["next"]
        and "Reply on this card." in shown["next"]
    )
    assert "bundle" not in (shown["reason_text"] + shown["next"]).lower()
    assert "Try again" not in shown["next"]
    # The card's history says so too.
    assert any("Preview home was not published" in line for line in history(h, number))


def test_a_ready_preview_still_has_its_publish_time_and_no_start_time(console_hub):
    h = console_hub
    number, identity = published(h)
    shown = h.request(h.owner, "GET", f"/api/previews/{identity}")[1]
    assert shown["published_at"] and shown["started_at"] is None and shown["failure"] is None


# ── M4: served pages lead somewhere ──


def test_every_served_page_links_on_and_the_links_are_fixed_addresses(console_hub):
    h = console_hub
    number, identity = published(h)
    status, _headers, body = fetch(h, None, f"/p/{identity}/")
    assert status == 401 and b'href="/app/login"' in body and b"Sign in to Commonplace" in body
    status, _headers, body = fetch(h, h.owner, "/p/pv_0000000000000000/")
    assert status == 404 and b'href="/app/"' in body and identity.encode() not in body
    status, _headers, body = fetch(h, h.owner, f"/p/{identity}/missing.html")
    assert status == 404 and f'href="/p/{identity}/"'.encode() in body
    assert f'href="/app/#/card/{number}"'.encode() in body
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/stop", {})[0] == 200
    status, _headers, body = fetch(h, h.owner, f"/p/{identity}/")
    assert status == 410 and f'href="/app/#/card/{number}"'.encode() in body
    assert b"Use Open again on this card to serve it." in body


# ── behaviour ──


def test_a_coworker_cannot_bring_back_a_preview_a_person_stopped(console_hub):
    h = console_hub
    number, identity = published(h)
    assert h.request(h.member, "POST", f"/api/previews/{identity}/stop", {})[0] == 200
    ok, text = mcp(
        h,
        h.t1,
        "preview_publish",
        card=number,
        name="home",
        files=[{"path": "index.html", "text": "v2"}],
    )
    assert not ok and "[person_stopped]" in text and "a person can use Open again" in text
    shown = h.request(h.owner, "GET", f"/api/previews/{identity}")[1]
    assert (shown["state"], shown["reason"], shown["revision"]) == (
        "stopped",
        "stopped_by_person",
        1,
    )
    # A person can still publish it again, and Open again works as before.
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/open-again", {})[0] == 200


def test_the_same_files_again_serve_the_kept_revision_instead_of_a_copy(console_hub):
    h = console_hub
    number, identity = published(h)
    ok, _ = mcp(h, h.t1, "preview_stop", id=identity)
    assert ok
    ok, text = mcp(h, h.t1, "preview_publish", card=number, name="home", files=PAGE)
    assert ok and "unchanged" in text
    shown = h.request(h.owner, "GET", f"/api/previews/{identity}")[1]
    assert (shown["state"], shown["revision"], len(shown["revisions"])) == ("ready", 1, 1)


def test_stop_and_open_again_are_written_on_the_card(console_hub):
    h = console_hub
    number, identity = published(h)
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/stop", {})[0] == 200
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/open-again", {})[0] == 200
    lines = history(h, number)
    assert any("Stopped: preview home." in line for line in lines)
    assert any("Served again: preview home." in line for line in lines)
    assert not any("/p/" in line for line in lines)


def test_a_name_that_holds_a_credential_is_refused(console_hub):
    h = console_hub
    number = card(h)
    ok, text = mcp(h, h.t1, "preview_publish", card=number, name="ghp_" + "0" * 36, files=PAGE)
    assert not ok and "credential" in text
    assert h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"] == []


def test_a_first_path_part_starting_with_a_tilde_is_refused(console_hub):
    h = console_hub
    number = card(h)
    ok, text = mcp(
        h,
        h.t1,
        "preview_publish",
        card=number,
        name="home",
        files=[{"path": "index.html", "text": "x"}, {"path": "~abc/a.css", "text": "x"}],
    )
    assert not ok and "[bad_path]" in text


def test_a_linked_folder_inside_the_revision_is_not_followed(console_hub):
    h = console_hub
    _number, identity = published(h)
    folder = store.revision_dir(h.gateway, h.workspace, identity, 1)
    (folder / "alias").symlink_to(folder / "assets", target_is_directory=True)
    status, headers, _ = fetch(h, h.owner, f"/p/{identity}/alias/app.css")
    assert status == 404 and "Location" not in headers


def test_a_preview_table_from_an_earlier_slice_gains_the_failure_columns():
    class Db:
        _lock = threading.RLock()
        _conn = sqlite3.connect(":memory:")

    Db._conn.execute(
        "CREATE TABLE previews (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, card INTEGER NOT NULL, "
        "name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'static', placement TEXT NOT NULL DEFAULT 'hub', "
        "state TEXT NOT NULL, reason TEXT NOT NULL, current_revision INTEGER NOT NULL DEFAULT 0, "
        "pr_url TEXT NOT NULL DEFAULT '', pr_head TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL, "
        "published_by TEXT NOT NULL, by_person INTEGER NOT NULL, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, UNIQUE (workspace_id, card, name))"
    )
    Db._conn.execute(
        "INSERT INTO previews(id,workspace_id,card,name,state,reason,actor,published_by,by_person,"
        "created_at,updated_at) VALUES ('pv_1','w',1,'n','failed','no_index_html','conn:a','a',0,'t','t')"
    )
    store.ensure_schema(Db)
    store.ensure_schema(Db)
    columns = [r[1] for r in Db._conn.execute("PRAGMA table_info(previews)")]
    assert {"failure_file", "failure_kind"} <= set(columns)
    assert Db._conn.execute("SELECT failure_file, failure_kind FROM previews").fetchall() == [
        ("", "")
    ]
