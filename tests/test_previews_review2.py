"""Persistent previews, review round 2 (server and serving): the limit that was hit is named,
stale failure details are cleared, a credential-shaped file name is refused by kind, a Coworker
cannot reopen what a person stopped, the key is told to the person, and the page policy lets a
single-file page run.

Real local HTTP and real MCP calls over the console fixture; time is the preview module's own
clock. Nothing sleeps.
"""

import http.client

import pytest

from commonplace import extensions
from commonplace.previews import bundle, service, serving, store, tools
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import mcp
from tests.test_previews import card, one, prow, publish


def fetch(h, auth, path):
    conn = http.client.HTTPConnection("127.0.0.1", h.hub.port, timeout=30)
    try:
        conn.request("GET", path, None, dict(auth) if isinstance(auth, dict) else {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


# ── P4: the limit that was hit is the one named ──


def test_the_count_limit_says_stopping_makes_room(console_hub, monkeypatch):
    h = console_hub
    monkeypatch.setattr(store, "MAX_READY", 1)
    number = card(h)
    assert publish(h, h.t1, number, name="a")[0]
    ok, text = publish(h, h.t1, number, name="b")
    assert not ok and "[workspace_quota_full]" in text
    assert "already serves 25 previews" in text and "Stop a preview" in text
    assert "does not free space" not in text and "500 MiB" not in text
    shown = one(h, number, "b")
    assert "already serves 25 previews" in shown["reason_text"]
    assert "Stop a preview that is no longer needed" in shown["next"]


def test_the_space_limit_says_stopping_does_not_free_space(console_hub, monkeypatch):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    monkeypatch.setattr(store, "MAX_WORKSPACE_BYTES", one(h, number)["size_bytes"] + 5)
    ok, text = publish(h, h.t1, number, name="b", files=[{"path": "index.html", "text": "x" * 50}])
    assert not ok and "[workspace_space_full]" in text
    assert "used its 500 MiB of preview space" in text
    assert "Stopping a preview does not free space" in text
    assert "Tell the workspace owner" in text and "Stop a preview that is no longer" not in text
    shown = one(h, number, "b")
    assert shown["reason"] == "workspace_space_full"
    assert "Stopping a preview does not free space" in shown["next"]
    assert "Tell the workspace owner" in shown["next"]
    assert "ask the Coworker to publish again" not in shown["next"]
    # The same refusal through the people's route is a 409 with the same words.
    ids = [
        p["id"] for p in h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"]
    ]
    assert len(ids) == 2
    assert service.REFUSAL_STATUS["workspace_space_full"] == 409


def test_both_limits_name_the_space_when_the_bytes_would_not_fit(monkeypatch):
    full = {"ready": store.MAX_READY, "bytes": store.MAX_WORKSPACE_BYTES}
    monkeypatch.setattr(store, "usage", lambda _conn, _workspace: full)
    with pytest.raises(bundle.Refused) as raised:
        store.check_room(None, "w", 1, needs_ready_slot=True)
    assert raised.value.reason == "workspace_space_full"


# ── minors: stale failure details, credential-shaped file names, reopen by a Coworker ──


def test_a_stale_failed_file_does_not_outlive_a_later_publish(console_hub):
    h = console_hub
    number = card(h)
    leak = "ghp_" + "0" * 36
    ok, _ = publish(
        h,
        h.t1,
        number,
        files=[{"path": "index.html", "text": "x"}, {"path": "env.js", "text": f"t='{leak}'"}],
    )
    assert not ok
    assert one(h, number)["failure"] == {"file": "env.js", "kind": "github_token"}
    assert publish(h, h.t1, number)[0]
    shown = one(h, number)
    assert shown["state"] == "ready" and shown["failure"] is None
    row = prow(h, shown["id"])
    assert (row["failure_file"], row["failure_kind"]) == ("", "")


def test_a_storage_failure_after_a_refused_file_shows_no_old_file(console_hub, monkeypatch):
    h = console_hub
    number = card(h)
    leak = "ghp_" + "0" * 36
    assert not publish(
        h,
        h.t1,
        number,
        files=[{"path": "index.html", "text": "x"}, {"path": "env.js", "text": f"t='{leak}'"}],
    )[0]
    assert one(h, number)["failure"]["file"] == "env.js"

    def broken(*_args):
        raise bundle.Refused("storage_unavailable")

    monkeypatch.setattr(store, "write_files", broken)
    ok, text = publish(h, h.t1, number)
    assert not ok and "[storage_unavailable]" in text
    shown = one(h, number)
    assert shown["state"] == "failed" and shown["reason"] == "storage_unavailable"
    assert shown["failure"] is None


def test_a_file_name_that_looks_like_a_credential_is_refused_by_kind_only(console_hub):
    h = console_hub
    number = card(h)
    leak = "ghp_" + "0" * 36
    ok, text = publish(
        h,
        h.t1,
        number,
        files=[{"path": "index.html", "text": "x"}, {"path": f"assets/{leak}.css", "text": "a"}],
    )
    assert not ok and "[contains_secret_like_text]" in text and "kind=github_token" in text
    assert leak not in text
    shown = one(h, number)
    assert shown["reason"] == "contains_secret_like_text" and shown["failure"] is None
    row = prow(h, shown["id"])
    assert leak not in " ".join(str(v) for v in tuple(row))
    with pytest.raises(bundle.Refused) as raised:
        bundle.clean_path(f"{leak}/index.html")
    assert raised.value.detail == {"kind": "github_token"}
    # Served addresses are held to the same rule and never read a file for it.
    assert serving.file_path(f"{leak}/index.html") is None


def test_a_coworker_cannot_reopen_what_a_person_stopped_even_through_the_service(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    assert h.request(h.member, "POST", f"/api/previews/{identity}/stop", {})[0] == 200
    coworker = h.principal(h.t1)
    with pytest.raises(bundle.Refused) as raised:
        service.reopen(h.hub.gateway, coworker, identity)
    assert raised.value.reason == "person_stopped"
    assert one(h, number)["state"] == "stopped"
    # A Coworker still reopens what it stopped itself, and a person reopens either.
    assert h.request(h.owner, "POST", f"/api/previews/{identity}/open-again", {})[0] == 200
    assert one(h, number)["state"] == "ready"
    assert mcp(h, h.t1, "preview_stop", id=identity)[0]
    assert one(h, number)["reason"] == "stopped_by_coworker"
    assert service.reopen(h.hub.gateway, coworker, identity)["state"] == "ready"


# ── P3 and the copy: the key is told to the person ──


def test_the_sign_in_page_tells_the_key_and_the_ready_row_says_it(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    shown = one(h, number)
    status, _headers, body = fetch(h, None, f"/p/{shown['id']}/")
    text = body.decode()
    assert status == 401
    assert "key for 30 minutes" in text and "share the preview, not the address" in text
    assert "30 minutes" in shown["reason_text"]


def test_the_being_published_page_does_not_claim_it_refreshes(console_hub):
    h = console_hub
    number = card(h)
    assert publish(h, h.t1, number)[0]
    identity = one(h, number)["id"]
    with h.db._lock, h.db._tx() as conn:
        conn.execute(
            "UPDATE previews SET state='building',reason='storing',updated_at=? WHERE id=?",
            (store.stamp(store.clock()), identity),
        )
    status, _headers, body = fetch(h, h.owner, f"/p/{identity}/")
    text = body.decode()
    assert status == 503
    assert "by itself" not in text
    assert "Reload this page, or open the card, to see when it is ready." in text


def test_the_page_policy_lets_a_single_file_page_run_and_the_tool_says_what_it_is():
    policy = serving.FILE_POLICY
    assert "script-src 'self' 'unsafe-inline'" in policy
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "'unsafe-eval'" not in policy and "allow-top-navigation" not in policy
    assert "allow-same-origin" not in policy and "connect-src 'none'" in policy
    text = extensions.get("preview_publish").description
    assert "static" in text and "inline" in text and "relative" in text
    assert "version" in text and "revision" not in text and "30 minutes" in text


def test_the_tools_speak_of_versions_kib_and_what_is_served(console_hub):
    h = console_hub
    number = card(h)
    ok, text = publish(h, h.t1, number)
    assert ok and "version 1" in text and "revision" not in text
    assert "30 minutes" in text and "never public" not in text
    ok, listed = mcp(h, h.t1, "preview_list", card=number)
    assert ok and "previews served" in listed and "previews open" not in listed
    assert " 0 MiB of" not in listed and "bytes of 500 MiB used" in listed
    assert tools._size(2048) == "2 KiB" and tools._size(10) == "10 bytes"
    assert tools._size(3 * 1024 * 1024) == "3 MiB"


# ── copy ──


def test_a_member_changing_the_rule_is_told_who_may(console_hub):
    h = console_hub
    status, body, _ = h.request(h.member, "PUT", "/api/previews/rules", {"max_age_days": 5})
    assert status == 403 and body["error"] == "Only the workspace owner can change this rule."
    status, body, _ = h.request(h.member, "POST", "/api/previews/pv_0000000000000000/stop", {})
    assert status == 404


def test_the_rule_sentence_has_one_or_at_most_and_a_whichever():
    both = service.rule_text({"stop_days_after_pr": 7, "max_age_days": 30})
    assert both.count(" or ") == 1  # only the one inside "merged or closed"
    assert both.endswith("whichever comes first. Its files are kept.")
    one_part = service.rule_text({"stop_days_after_pr": None, "max_age_days": 30})
    assert one_part == (
        "A preview stops being served 30 days after it was last made live. Its files are kept."
    )
