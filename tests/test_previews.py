"""Persistent previews, slice 1: the store, the API for people and the tools for Coworkers.

Real local HTTP and real MCP calls over the console fixture. Time is the module's own clock,
which the tests move; nothing here sleeps.
"""

import base64
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from commonplace.previews import bundle, store
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import mcp, service

INDEX = {"path": "index.html", "text": "<h1>Hello</h1>"}
PAGE = [INDEX, {"path": "assets/app.css", "text": "body{color:#222}"}]


def card(h, title="Build the page", **fields):
    status, body, _ = h.request(h.owner, "POST", "/api/boards/work/cards", {"title": title})
    assert status == 201, body
    return body["number"] if "number" in body else body["id"]


def publish(h, token, number, name="home", files=None):
    return mcp(h, token, "preview_publish", card=number, name=name, files=files or PAGE)


def previews(h, auth=None, **query):
    path = "/api/previews" + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")
    return h.request(auth or h.owner, "GET", path)


def one(h, number, name="home"):
    status, body, _ = previews(h, card=number)
    assert status == 200, body
    return next(p for p in body["previews"] if p["name"] == name)


def prow(h, identity):
    with h.db._lock:
        return h.db._conn.execute("SELECT * FROM previews WHERE id=?", (identity,)).fetchone()


# ── publish and persistence ──


def test_a_coworker_publishes_and_the_preview_is_ready_for_people_of_the_workspace(console_hub):
    h = console_hub
    number = card(h)
    ok, text = publish(h, h.t1, number)
    assert ok, text
    assert "never public" in text
    shown = one(h, number)
    assert shown["state"] == "ready" and shown["reason"] == "published"
    assert shown["revision"] == 1 and shown["file_count"] == 2 and shown["kind"] == "static"
    assert shown["url"] == f"/p/{shown['id']}/" and shown["placement"] == "hub"
    assert shown["by"] == "coworker" and shown["published_by"] == "a1"
    assert shown["availability"] == "reachable" and shown["stops_at"] is None
    # A member of the same workspace sees it too.
    status, body, _ = previews(h, h.member, card=number)
    assert status == 200 and [p["id"] for p in body["previews"]] == [shown["id"]]
    assert body["usage"] == {"ready": 1, "bytes": shown["size_bytes"]}
    assert (
        body["limits"]["max_ready"] == 25
        and "until a person or the Coworker" in body["rule"]["text"]
    )


def test_files_and_rows_survive_without_the_publishing_session(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    folder = store.revision_dir(h.gateway, h.workspace, identity, 1)
    assert (folder / "index.html").read_text() == "<h1>Hello</h1>"
    assert (folder / "assets" / "app.css").read_text() == "body{color:#222}"
    # Read it back through a brand new connection, as a restarted server would.
    fresh = sqlite3.connect(
        h.db.path
        if hasattr(h.db, "path")
        else h.db._conn.execute("PRAGMA database_list").fetchone()[2]
    )
    fresh.row_factory = sqlite3.Row
    row = fresh.execute(
        "SELECT state,current_revision FROM previews WHERE id=?", (identity,)
    ).fetchone()
    assert (row["state"], row["current_revision"]) == ("ready", 1)
    # The publishing token is gone; people still list and read it.
    status, body, _ = h.request(h.owner, "GET", f"/api/previews/{identity}")
    assert status == 200 and body["revisions"][0]["revision"] == 1


def test_publishing_the_same_bundle_again_changes_nothing_and_a_new_one_adds_a_revision(
    console_hub,
):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    first = one(h, number)
    ok, text = publish(h, h.t1, number)
    assert ok and "unchanged" in text and one(h, number)["revision"] == 1
    changed = [INDEX, {"path": "assets/app.css", "text": "body{color:#000}"}]
    assert publish(h, h.t1, number, files=changed)[0]
    second = one(h, number)
    assert second["id"] == first["id"] and second["revision"] == 2
    status, body, _ = h.request(h.owner, "GET", f"/api/previews/{first['id']}")
    assert [r["revision"] for r in body["revisions"]] == [2, 1]
    assert store.revision_dir(h.gateway, h.workspace, first["id"], 1).is_dir()
    assert store.revision_dir(h.gateway, h.workspace, first["id"], 2).is_dir()


def test_a_card_note_records_every_publish(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    owner = service(h, h.person(h.hub.admin))
    shown = owner.task_show(number)
    assert "Published preview home (revision 1)" in json.dumps(shown)


# ── who may see and do what ──


def test_other_workspaces_and_unknown_ids_get_the_same_answer(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    missing = h.request(h.owner, "GET", "/api/previews/pv_0000000000000000")
    foreign = h.request(h.other, "GET", f"/api/previews/{identity}")
    assert foreign[0] == missing[0] == 404 and foreign[2] == missing[2]
    assert h.request(h.other, "GET", f"/api/previews?card={number}")[0] == 404
    for action in ("stop", "open-again"):
        assert h.request(h.other, "POST", f"/api/previews/{identity}/{action}", {})[0] == 404
    assert h.request(h.other, "DELETE", f"/api/previews/{identity}", {"confirm": identity})[0] in (
        403,
        404,
    )
    assert one(h, number)["state"] == "ready"
    anonymous = h.request(None, "GET", "/api/previews")
    assert anonymous[0] == 401


def test_a_card_the_caller_cannot_see_hides_its_previews(console_hub):
    h = console_hub
    service(h, h.person(h.hub.admin)).board_add(slug="hidden", wing="hidden")
    status, body, _ = h.request(h.owner, "POST", "/api/boards/hidden/cards", {"title": "Secret"})
    assert status == 201, body
    number = body.get("number") or body.get("id")
    # The workspace owner's own token has every wing; the console agents see "shared" only.
    token = h.hub.owner_token("owner-agent")
    assert publish(h, token, number)[0]
    ok, text = mcp(h, h.t1, "preview_list", card=number)
    assert not ok and "not found" in text
    ok, text = publish(h, h.t1, number)
    assert not ok and "not found" in text
    ok, text = mcp(h, h.t1, "preview_list")
    assert ok and "No previews" in text
    # The owner's own listing shows the card's preview; the console agent's tools do not.
    status, body, _ = previews(h)
    assert status == 200 and [p["card"] for p in body["previews"]] == [number]


def test_an_agent_token_cannot_use_the_people_routes(console_hub):
    h = console_hub
    number = card(h)
    status, body, _ = h.request(h.t1, "GET", "/api/previews")
    assert status == 403 and body["reason"] == "person_only"
    status, body, _ = h.request(
        h.t1, "POST", "/api/previews", {"card": number, "name": "x", "files": PAGE}
    )
    assert status == 403 and body["reason"] == "person_only"


def test_a_person_can_publish_over_the_api(console_hub):
    h = console_hub
    number = card(h)
    status, body, _ = h.request(
        h.owner, "POST", "/api/previews", {"card": number, "name": "by-hand", "files": PAGE}
    )
    assert status == 201 and body["state"] == "ready" and body["by"] == "person"
    status, again, _ = h.request(
        h.owner, "POST", "/api/previews", {"card": number, "name": "by-hand", "files": PAGE}
    )
    assert status == 200 and again["revision"] == 1
    assert h.request(h.owner, "POST", "/api/previews", {"card": number})[0] == 400


def test_untrusted_tokens_do_not_see_the_write_tools(console_hub):
    h = console_hub
    token = h.hub.token(
        "untrusted",
        user=h.hub.admin,
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
        trust="untrusted",
        write_wing="inbox",
    )
    names = h.hub.tool_names(token)
    assert (
        "preview_list" in names and "preview_publish" not in names and "preview_stop" not in names
    )


# ── refusals, each with its reason ──


def refused(h, number, files, reason, name="bad"):
    ok, text = publish(h, h.t1, number, name=name, files=files)
    assert not ok and f"[{reason}]" in text, text
    return text


def test_every_bundle_problem_names_its_reason_and_serves_nothing(console_hub, monkeypatch):
    h = console_hub
    monkeypatch.setattr(store, "MAX_PER_CARD_HOUR", 1000)
    number = card(h)
    refused(h, number, [{"path": "about.html", "text": "x"}], "no_index_html", "a")
    refused(
        h, number, [{"path": f"f{i}.txt", "text": "x"} for i in range(501)], "too_many_files", "b"
    )
    big = "A" * (bundle.MAX_BUNDLE_BYTES + 1)
    refused(h, number, [INDEX, {"path": "big.txt", "text": big}], "too_large", "c")
    for index, path in enumerate(
        ["/etc/passwd", "../x.html", "a//b.html", "a\\b.html", ".hidden", "a/./b", "x/" * 11 + "y"]
    ):
        refused(h, number, [INDEX, {"path": path, "text": "x"}], "bad_path", f"p{index}")
    refused(
        h,
        number,
        [INDEX, {"path": "a.txt", "text": "1"}, {"path": "a.txt", "text": "2"}],
        "bad_path",
        "dup",
    )
    refused(
        h,
        number,
        [INDEX, {"path": "a", "text": "1"}, {"path": "a/b", "text": "2"}],
        "bad_path",
        "clash",
    )
    leak = "ghp_" + "0" * 36
    text = refused(
        h,
        number,
        [INDEX, {"path": "env.js", "text": f"const t='{leak}'"}],
        "contains_secret_like_text",
        "s",
    )
    assert "env.js" in text and "github_token" in text and leak not in text
    status, body, _ = previews(h, card=number)
    assert {p["state"] for p in body["previews"]} == {"failed"}
    assert all(p["url"] is None for p in body["previews"])
    assert body["usage"] == {"ready": 0, "bytes": 0}
    assert not any(store.root(h.gateway, h.workspace).glob("*/r*"))


def test_malformed_requests_are_input_errors_not_stored_failures(console_hub):
    h = console_hub
    number = card(h)
    for files in (
        "nope",
        [{"path": "index.html"}],
        [{"path": "index.html", "text": "x", "base64": "eA=="}],
        [{"path": "index.html", "base64": "***"}],
        [{"path": "index.html", "text": 5}],
    ):
        ok, text = mcp(h, h.t1, "preview_publish", card=number, name="m", files=files)
        assert not ok, text
    ok, text = mcp(h, h.t1, "preview_publish", card=number, name="  ", files=PAGE)
    assert not ok and "name" in text
    assert previews(h, card=number)[1]["previews"] == []


def test_base64_files_are_stored_as_bytes(console_hub):
    h = console_hub
    number = card(h)
    data = bytes(range(256))
    files = [INDEX, {"path": "img/pixel.bin", "base64": base64.b64encode(data).decode()}]
    assert publish(h, h.t1, number, files=files)[0]
    identity = one(h, number)["id"]
    assert (
        store.revision_dir(h.gateway, h.workspace, identity, 1) / "img" / "pixel.bin"
    ).read_bytes() == data


def test_a_failed_republish_leaves_the_ready_preview_serving(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    refused(h, number, [{"path": "nope.html", "text": "x"}], "no_index_html", "home")
    shown = one(h, number)
    assert shown["state"] == "ready" and shown["revision"] == 1


# ── stop, open again, delete ──


def test_stop_keeps_files_and_open_again_restores_them(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    ok, text = mcp(h, h.t1, "preview_stop", id=identity)
    assert ok and "stopped_by_coworker" in text
    shown = one(h, number)
    assert shown["state"] == "stopped" and shown["reason"] == "stopped_by_coworker"
    assert shown["url"] and store.revision_dir(h.gateway, h.workspace, identity, 1).is_dir()
    status, body, _ = h.request(h.member, "POST", f"/api/previews/{identity}/open-again", {})
    assert status == 200 and body["state"] == "ready" and body["reason"] == "published"
    status, body, _ = h.request(h.member, "POST", f"/api/previews/{identity}/stop", {})
    assert body["reason"] == "stopped_by_person"
    # Stopping a stopped preview is harmless and says what it is.
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/stop", {})[1]["state"] == "stopped"


def test_a_coworker_stops_only_what_it_published(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    ok, text = mcp(h, h.t2, "preview_stop", id=identity)
    assert not ok and "cannot" in text
    assert one(h, number)["state"] == "ready"


def test_delete_needs_an_owner_and_the_repeated_id_and_is_final(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    assert (
        h.request(h.member, "DELETE", f"/api/previews/{identity}", {"confirm": identity})[0] == 403
    )
    assert h.request(h.owner, "DELETE", f"/api/previews/{identity}", {"confirm": "yes"})[0] == 400
    assert store.revision_dir(h.gateway, h.workspace, identity, 1).is_dir()
    status, body, _ = h.request(
        h.owner, "DELETE", f"/api/previews/{identity}", {"confirm": identity}
    )
    assert status == 200 and body["state"] == "stopped" and body["reason"] == "files_deleted"
    assert body["url"] is None and body["revisions"][0]["files_deleted"] is True
    assert not (store.root(h.gateway, h.workspace) / identity).exists()
    status, again, _ = h.request(h.owner, "POST", f"/api/previews/{identity}/open-again", {})
    assert status == 409 and again["reason"] == "files_deleted"
    assert previews(h, card=number)[1]["usage"] == {"ready": 0, "bytes": 0}
    # A new publish under the same name works and starts a new revision.
    assert publish(h, h.t1, number)[0]
    assert one(h, number)["state"] == "ready" and one(h, number)["revision"] == 2


def test_a_coworker_has_no_way_to_delete_or_set_rules(console_hub):
    h = console_hub
    names = h.hub.tool_names(h.t1)
    assert {"preview_publish", "preview_list", "preview_stop"} <= set(names)
    assert not [n for n in names if n.startswith("preview_") and ("delete" in n or "rule" in n)]


# ── limits ──


def test_the_ready_limit_refuses_without_evicting_and_stopping_makes_room(console_hub, monkeypatch):
    h = console_hub
    monkeypatch.setattr(store, "MAX_READY", 2)
    number = card(h)
    for name in ("a", "b"):
        assert publish(h, h.t1, number, name=name)[0]
    text = refused(h, number, PAGE, "workspace_quota_full", "c")
    assert "Stop or delete an older preview" in text
    ids = {p["name"]: p for p in previews(h, card=number)[1]["previews"]}
    assert ids["a"]["state"] == ids["b"]["state"] == "ready" and ids["c"]["state"] == "failed"
    assert mcp(h, h.t1, "preview_stop", id=ids["a"]["id"])[0]
    assert publish(h, h.t1, number, name="c")[0]
    assert one(h, number, "c")["state"] == "ready"
    status, body, _ = h.request(h.owner, "POST", f"/api/previews/{ids['a']['id']}/open-again", {})
    assert status == 409 and body["reason"] == "workspace_quota_full"


def test_the_size_limit_counts_every_revision(console_hub, monkeypatch):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    size = one(h, number)["size_bytes"]
    monkeypatch.setattr(store, "MAX_WORKSPACE_BYTES", size + 5)
    refused(
        h,
        number,
        [INDEX, {"path": "assets/app.css", "text": "body{color:#fff}"}],
        "workspace_quota_full",
        "home",
    )
    assert one(h, number)["revision"] == 1


def test_publishing_is_limited_per_card_per_hour(console_hub, monkeypatch):
    h = console_hub
    monkeypatch.setattr(store, "MAX_PER_CARD_HOUR", 2)
    number = card(h)
    for index in range(2):
        assert publish(h, h.t1, number, files=[{"path": "index.html", "text": str(index)}])[0]
    ok, text = publish(h, h.t1, number, files=[{"path": "index.html", "text": "again"}])
    assert not ok and "[rate_limited]" in text
    other = card(h, "Another card")
    assert publish(h, h.t1, other)[0]
    moment = store.clock()
    monkeypatch.setattr(store, "clock", lambda: moment + timedelta(hours=1, minutes=1))
    assert publish(h, h.t1, number, files=[{"path": "index.html", "text": "later"}])[0]


# ── honest states ──


def test_a_publish_that_never_finished_reads_as_failed_not_stuck(console_hub, monkeypatch):
    h = console_hub
    number = card(h)
    now = store.stamp(store.clock())
    with h.db._lock, h.db._tx() as conn:
        conn.execute(
            "INSERT INTO previews(id,workspace_id,card,name,state,reason,actor,published_by,by_person,"
            "created_at,updated_at) VALUES ('pv_stuck',?,?,'stuck','building','storing','conn:a1','a1',0,?,?)",
            (h.workspace, number, now, now),
        )
    assert one(h, number, "stuck")["state"] == "building"
    moment = store.clock()
    monkeypatch.setattr(
        store, "clock", lambda: moment + store.BUILDING_STALE + timedelta(seconds=1)
    )
    shown = one(h, number, "stuck")
    assert shown["state"] == "failed" and shown["reason"] == "publish_interrupted"
    assert shown["reason_text"] and shown["next"]


def test_every_reason_has_text_and_a_next_step_where_one_exists():
    for code, (text, nxt) in store.REASONS.items():
        assert text.strip() and text.endswith((".", ")")), code
        if code.startswith(("stopped_", "pr_", "expired", "no_", "too_", "bad_", "contains_")):
            assert nxt.strip(), code


def test_the_storage_failing_is_a_reasoned_failure(console_hub, monkeypatch):
    h = console_hub
    number = card(h)

    original = Path.mkdir

    def broken(self, *args, **kwargs):
        if ".tmp-" in str(self):
            raise OSError("disk full: /secret/place")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", broken)
    ok, text = publish(h, h.t1, number)
    assert not ok and "[storage_unavailable]" in text and "/secret/place" not in text
    monkeypatch.undo()
    shown = one(h, number)
    assert shown["state"] == "failed" and shown["reason"] == "storage_unavailable"


# ── nothing leaks ──


def test_responses_name_no_paths_hosts_or_secrets(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    texts = [
        h.request(h.owner, "GET", f"/api/previews/{identity}")[2].decode(),
        h.request(h.owner, "GET", "/api/previews")[2].decode(),
        mcp(h, h.t1, "preview_list", card=number)[1],
        publish(h, h.t1, number)[1],
    ]
    workspace_dir = str(h.gateway.workspace_dir(h.workspace))
    for text in texts:
        assert workspace_dir not in text and str(h.hub.supervisor.root) not in text
        assert "workspace_id" not in text and "conn:" not in text and "Bearer" not in text


def test_the_event_record_is_append_only(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    with h.db._lock:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            h.db._conn.execute("UPDATE preview_events SET reason='x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            h.db._conn.execute("DELETE FROM preview_events")


# ── the stop rule ──


def test_the_rule_is_off_by_default_and_only_the_owner_sets_it(console_hub):
    h = console_hub
    status, body, _ = h.request(h.member, "GET", "/api/previews/rules")
    assert status == 200 and body["stop_days_after_pr"] is None and body["max_age_days"] is None
    assert h.request(h.member, "PUT", "/api/previews/rules", {"max_age_days": 30})[0] == 403
    status, body, _ = h.request(h.owner, "PUT", "/api/previews/rules", {"stop_days_after_pr": 7})
    assert status == 200 and body["stop_days_after_pr"] == 7
    assert (
        "7 days after its pull request is merged or closed" in body["text"]
        and "files are kept" in body["text"]
    )
    for bad in (
        {"stop_days_after_pr": 0},
        {"max_age_days": True},
        {"max_age_days": "30"},
        {"other": 1},
    ):
        assert h.request(h.owner, "PUT", "/api/previews/rules", bad)[0] == 400
    status, body, _ = h.request(h.owner, "PUT", "/api/previews/rules", {"stop_days_after_pr": None})
    assert body["stop_days_after_pr"] is None and "until a person" in body["text"]
    assert h.request(h.other, "GET", "/api/previews/rules")[1]["stop_days_after_pr"] is None


def test_listing_by_pull_request_finds_the_cards_previews_and_records_the_pull_request(console_hub):
    h = console_hub
    number = card(h)
    url = "https://github.com/example/app/pull/9"
    head = "a" * 40
    with service(h, h.person(h.hub.admin)).store.transaction() as conn:
        conn.execute("UPDATE tasks SET pr_url=?, pr_head=? WHERE id=?", (url, head, number))
    assert publish(h, h.t1, number)[0]
    status, body, _ = previews(h, pr=url)
    assert status == 200 and [p["name"] for p in body["previews"]] == ["home"]
    assert body["previews"][0]["pr"] == {"url": url, "head": head}
    status, body, _ = previews(h, pr="https://github.com/example/app/pull/404")
    assert status == 200 and body["previews"] == []
    assert h.request(h.other, "GET", f"/api/previews?pr={url}")[1]["previews"] == []


# ── pure checks (no hub) ──


def test_find_kinds_detects_unmistakable_credentials_and_ignores_build_output():
    from commonplace import redact

    token = "ghp_" + "0" * 36
    big = "x" * 40_000 + "\n-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"
    assert redact.find_kinds(f"const t = '{token}';", bundle.SECRET_KINDS) == ["github_token"]
    assert redact.find_kinds(big, bundle.SECRET_KINDS) == [
        "private_key"
    ]  # past the redactor's own limit
    minified = "var a=1;/*7f3c9e0a1b2d4c5e6f708192a3b4c5d6*/ password=;token: x;auth=1"
    assert redact.find_kinds(minified, bundle.SECRET_KINDS) == []
    assert redact.find_kinds(b"bytes", bundle.SECRET_KINDS) == []


@pytest.mark.parametrize(
    "path", ["index.html", "a/b/c.js", "assets/app.v2.min.js", "ünï.txt", "x/" * 9 + "y"]
)
def test_good_paths_pass(path):
    assert bundle.clean_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/a",
        "a/",
        "a//b",
        "a/../b",
        "./a",
        "a/.b",
        "a\\b",
        "a\x00b",
        "a\nb",
        "é" * 128,
        "x/" * 10 + "y",
        5,
        None,
    ],
)
def test_bad_paths_are_refused(path):
    with pytest.raises(bundle.Refused) as caught:
        bundle.clean_path(path)
    assert caught.value.reason == "bad_path"


def test_a_token_that_lost_write_access_cannot_stop_or_reopen(console_hub):
    h = console_hub
    number = card(h)
    token = h.hub.token(
        "readonly",
        user=h.hub.admin,
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=[],
        trust="standard",
    )
    now = store.stamp(store.clock())
    with h.db._lock, h.db._tx() as conn:
        conn.execute(
            "INSERT INTO previews(id,workspace_id,card,name,state,reason,actor,published_by,by_person,"
            "created_at,updated_at) VALUES ('pv_ro',?,?,'ro','ready','published','conn:readonly','readonly',0,?,?)",
            (h.workspace, number, now, now),
        )
    ok, text = mcp(h, token, "preview_stop", id="pv_ro")
    assert not ok and "cannot" in text
    assert prow(h, "pv_ro")["state"] == "ready"


def test_a_credential_in_a_file_that_is_not_utf8_is_still_refused(console_hub):
    h = console_hub
    number = card(h)
    leak = ("ghp_" + "0" * 36).encode()
    data = base64.b64encode(b"\xff\xfe" + leak).decode()
    ok, text = publish(h, h.t1, number, files=[INDEX, {"path": "blob.bin", "base64": data}])
    assert not ok and "[contains_secret_like_text]" in text and "blob.bin" in text


def test_the_publish_attempt_is_reserved_before_the_bundle_is_read(console_hub, monkeypatch):
    h = console_hub
    monkeypatch.setattr(store, "MAX_PER_CARD_HOUR", 1)
    number = card(h)
    # A bundle that fails still used its attempt.
    refused(h, number, [{"path": "nope.html", "text": "x"}], "no_index_html", "one")
    ok, text = publish(h, h.t1, number, name="two")
    assert not ok and "[rate_limited]" in text


def test_a_publish_that_dies_after_the_files_are_placed_leaves_nothing_and_can_be_retried(
    console_hub, monkeypatch
):
    from commonplace.previews import service

    h = console_hub
    number = card(h)
    person = h.person(h.hub.admin)
    original = store.event

    def dying(conn, workspace, preview_id, card_number, kind, *args, **kwargs):
        if kind == "published":
            raise RuntimeError("database went away")
        return original(conn, workspace, preview_id, card_number, kind, *args, **kwargs)

    monkeypatch.setattr(store, "event", dying)
    with pytest.raises(RuntimeError):
        service.publish(h.gateway, person, number, "home", PAGE)
    monkeypatch.setattr(store, "event", original)
    assert not any(store.root(h.gateway, h.workspace).glob("*/r*"))
    shown, created = service.publish(h.gateway, person, number, "home", PAGE)
    assert created and shown["state"] == "ready" and shown["revision"] == 1


def test_a_directory_left_by_an_earlier_crash_does_not_block_the_next_publish(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    stale = store.revision_dir(h.gateway, h.workspace, identity, 2)
    stale.mkdir(parents=True)
    (stale / "left-over.txt").write_text("from a publish that died")
    assert publish(h, h.t1, number, files=[{"path": "index.html", "text": "second"}])[0]
    assert one(h, number)["revision"] == 2
    assert (stale / "index.html").read_text() == "second" and not (stale / "left-over.txt").exists()


def test_a_delete_that_cannot_remove_every_file_says_so_and_records_it(console_hub, monkeypatch):
    from commonplace.previews import service

    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    monkeypatch.setattr(service, "_remove_tree", lambda path: None)
    status, body, _ = h.request(
        h.owner, "DELETE", f"/api/previews/{identity}", {"confirm": identity}
    )
    assert status == 200 and body["reason"] == "files_deleted" and body["files_remaining"] is True
    with h.db._lock:
        kinds = [
            r[0]
            for r in h.db._conn.execute(
                "SELECT kind FROM preview_events WHERE preview_id=?", (identity,)
            )
        ]
    assert "delete_incomplete" in kinds
    monkeypatch.undo()
    status, body, _ = h.request(
        h.owner, "DELETE", f"/api/previews/{identity}", {"confirm": identity}
    )
    assert status == 200 and "files_remaining" not in body
    assert not (store.root(h.gateway, h.workspace) / identity).exists()
