"""The Memory page lists agent-saved notes (the inbox wing) under the usual read checks."""

import json
import subprocess
from pathlib import Path

from commonplace.policy import Policy
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_admin_api import cookie
from tests.test_card228_cross_workspace import IsolatedLauncher
from tests.test_commonplace_memory import _call, _http, _save
from tests.test_commonplace_web import request

NOTE = "  Agent note: café\ttwo  spaces\nsecond line — exact words.  "


def _agent(hub):
    return hub.token(
        "agent",
        profile="standard",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared", "inbox"],
        write_wing="inbox",
        trust="untrusted",
    )


def _person_get(hub, headers, path):
    status, _headers, body = request(hub, path, headers=headers)
    return status, json.loads(body)


def test_person_lists_the_inbox_room_and_reads_the_agent_note_verbatim(hub):
    note_id = _save(hub, _agent(hub), NOTE)
    headers = cookie(hub)
    status, body = _person_get(hub, headers, "/api/memory/rooms")
    assert status == 200
    assert body == {"rooms": [{"wing": "inbox", "name": "Agent notes"}]}
    status, found = _person_get(hub, headers, "/api/memory/search?q=exact%20words&theme=inbox")
    assert status == 200
    assert [(row["id"], row["theme"], row["text"]) for row in found["results"]] == [
        (note_id, "inbox", NOTE)
    ]


def test_inbox_room_obeys_the_callers_read_scope(hub):
    note_id = _save(hub, _agent(hub), NOTE)
    shared_only = hub.token(
        "wing-scoped",
        profile="full",
        scopes=["memory:read"],
        read_wings=["shared"],
        trust="standard",
    )
    status, body = _http(hub, shared_only, "/api/memory/rooms")
    assert (status, body) == (200, {"rooms": []})
    assert _http(hub, shared_only, "/api/memory/search?q=exact&theme=inbox")[0] == 403
    assert note_id not in json.dumps(_http(hub, shared_only, "/api/memory/search?q=exact"))
    no_read = hub.token(
        "write-only",
        profile="full",
        scopes=["memory:write"],
        read_wings=["*"],
        write_wings=["*"],
        trust="standard",
    )
    status, body = _http(hub, no_read, "/api/memory/rooms")
    assert status == 403 and "rooms" not in body
    unknown = _http(hub, "not-a-token", "/api/memory/rooms")
    assert unknown[0] == 401 and "rooms" not in unknown[1]


def test_member_with_literal_wings_sees_no_inbox_room(hub):
    _save(hub, _agent(hub), NOTE)
    user = hub.db.create_user("literal@example.test")
    hub.db.add_member(hub.workspace, user, "member", actor=hub.admin)
    literal = hub.token(
        "literal",
        user=user,
        profile="full",
        scopes=["memory:read"],
        read_wings=["shared", "threads"],
        trust="trusted",
    )
    assert _http(hub, literal, "/api/memory/rooms")[1] == {"rooms": []}
    assert _http(hub, literal, "/api/memory/search?q=exact&theme=inbox")[0] == 403
    stranger = hub.db.create_user("stranger@example.test")
    assert hub.db.role(hub.workspace, stranger) is None


def test_another_workspace_owner_does_not_see_the_inbox_note(hub):
    # Each workspace has its own palace process; the shared in-process one cannot show that.
    hub.gateway.supervisor.launcher = IsolatedLauncher()
    note_id = _save(hub, _agent(hub), NOTE)
    outsider = hub.db.create_user("outsider@example.test")
    elsewhere = hub.db.create_workspace("elsewhere", outsider)
    _cid, foreign = hub.db.create_token(
        workspace_ref=elsewhere,
        user_ref=outsider,
        name="foreign-owner",
        client="test",
        policy=Policy.build(
            profile="full",
            scopes=["memory:read", "memory:write", "private:read"],
            read_wings=["*"],
            write_wings=["*"],
            trust="trusted",
        ),
        actor=outsider,
    )
    assert _http(hub, foreign, "/api/memory/rooms")[1] == {
        "rooms": [{"wing": "inbox", "name": "Agent notes"}]
    }
    status, found = _http(hub, foreign, "/api/memory/search?q=exact&theme=inbox")
    assert status == 200 and found["results"] == []
    assert note_id not in json.dumps(_http(hub, foreign, "/api/memory/search?q=exact"))
    assert _http(hub, foreign, f"/api/memory/{note_id}")[0] == 404


def test_agent_save_still_lands_in_the_inbox_wing(hub):
    note_id = _save(hub, _agent(hub), NOTE)
    owner = hub.owner_token()
    got = _call(hub, owner, "memory_get", id=note_id)["content"][0]["text"]
    assert 'theme="inbox"' in got and json.loads(got.split("\nquoted data: ", 1)[1]) == NOTE


def test_memory_page_lists_notes_only_rooms():
    harness = Path(__file__).with_name("memory_agent_notes.mjs")
    result = subprocess.run(
        ["node", "--test", str(harness)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
