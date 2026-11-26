"""The owner reads what each trust level allows and changes a connection's level."""

import json

import pytest

from commonplace import policy as policy_module, sessions, trust_levels
from commonplace.control_db import ControlError
from commonplace.policy import Policy
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_admin_api import cookie, get, member
from tests.test_commonplace_web import request


def pat(hub, name, trust="untrusted", **policy):
    options = {"scopes": ["memory:read", "memory:write"], "read_wings": ["notes"]}
    options.update(policy)
    if trust == "untrusted":
        options.setdefault("write_wing", "inbox")
    cid, token = hub.db.create_token(
        workspace_ref=hub.workspace,
        user_ref=hub.admin,
        name=name,
        client="synthetic",
        policy=Policy.build(trust=trust, **options),
        actor=hub.admin,
        expires_days=None,
    )
    return cid, token


def patch(hub, cid, body, headers):
    status, _, raw = request(
        hub,
        f"/api/connections/{cid}",
        method="PATCH",
        body=json.dumps(body),
        headers={"Content-Type": "application/json", **headers},
    )
    return status, json.loads(raw) if raw else None


def stored_policy(hub, cid):
    return json.loads(hub.db.connection(cid, hub.workspace)["policy"])


def test_every_trust_level_has_one_plain_sentence():
    assert set(trust_levels.TEXT) == set(policy_module.TRUST_LEVELS)
    assert all(text.endswith(".") and "\n" not in text for text in trust_levels.TEXT.values())
    assert set(trust_levels.SETTABLE) == {"untrusted", "standard"}


def test_admin_connections_explain_levels_and_offer_the_change(hub):
    low, _ = pat(hub, "low")
    mid, _ = pat(hub, "mid", trust="standard", write_wing="inbox")
    top, _ = pat(hub, "top", trust="trusted")
    gone, _ = pat(hub, "gone")
    hub.db._conn.execute(
        "UPDATE connections SET revoked_at=? WHERE id=?", ("2026-01-01T00:00:00Z", gone)
    )
    status, data, _ = get(hub, "connections", cookie(hub))
    assert status == 200
    rows = {r["id"]: r for r in data["connections"]}
    assert data["trust_levels"] == [
        {"level": level, "summary": trust_levels.TEXT[level]}
        for level in policy_module.TRUST_LEVELS
    ]
    for cid, level in (
        (low, "untrusted"),
        (mid, "standard"),
        (top, "trusted"),
        (gone, "untrusted"),
    ):
        assert (
            rows[cid]["trust"] == level and rows[cid]["trust_summary"] == trust_levels.TEXT[level]
        )
    assert rows[low]["trust_change"] == {"can_change": True, "options": ["standard"], "reason": ""}
    assert rows[mid]["trust_change"] == {"can_change": True, "options": ["untrusted"], "reason": ""}
    for cid in (top, gone):
        change = rows[cid]["trust_change"]
        assert change["can_change"] is False and change["options"] == [] and change["reason"]
    assert "command line" in rows[top]["trust_change"]["reason"]
    assert "revoked or expired" in rows[gone]["trust_change"]["reason"]


def test_admin_explains_a_level_that_cannot_be_lowered(hub):
    cid, _ = pat(hub, "writer", trust="standard", write_wings=["notes"])
    # A standard writer with no inbox wing cannot become untrusted: the same policy check refuses it.
    status, data, _ = get(hub, "connections", cookie(hub))
    change = next(r for r in data["connections"] if r["id"] == cid)["trust_change"]
    assert change["can_change"] is False and "write_wing" in change["reason"]


def test_owner_changes_trust_through_the_policy_path_and_it_is_recorded(hub):
    cid, token = pat(hub, "agent-one")
    before = stored_policy(hub, cid)
    status, body = patch(hub, cid, {"trust": "standard"}, cookie(hub))
    assert status == 200 and body["connection"]["id"] == cid
    after = stored_policy(hub, cid)
    assert after["trust"] == "standard"
    assert {k: v for k, v in before.items() if k != "trust"} == {
        k: v for k, v in after.items() if k != "trust"
    }
    assert hub.db.authenticate_token(token).policy.trust == "standard"
    entry = next(
        r for r in hub.db.audit_entries(hub.workspace) if r["action"] == "connection.trust"
    )
    assert entry["connection_id"] == cid and entry["user_id"] == hub.admin
    assert entry["decision"] == "ok" and entry["reason"] == "untrusted -> standard"
    status, _ = patch(hub, cid, {"trust": "untrusted"}, cookie(hub))
    assert status == 200 and stored_policy(hub, cid)["trust"] == "untrusted"


def test_member_and_agent_token_are_refused(hub):
    cid, token = pat(hub, "agent-two")
    member(hub)
    assert patch(hub, cid, {"trust": "standard"}, cookie(hub, "member@example.com"))[0] == 403
    assert patch(hub, cid, {"trust": "standard"}, {"Authorization": f"Bearer {token}"})[0] == 403
    assert patch(hub, cid, {"trust": "standard"}, {})[0] == 401
    assert stored_policy(hub, cid)["trust"] == "untrusted"
    assert not [r for r in hub.db.audit_entries(hub.workspace) if r["action"] == "connection.trust"]


def test_server_refuses_trusted_unknown_and_mixed_requests(hub):
    cid, _ = pat(hub, "agent-three")
    for body in (
        {"trust": "trusted"},
        {"trust": "root"},
        {"trust": 1},
        {"trust": "standard", "read_wings": []},
    ):
        status, payload = patch(hub, cid, body, cookie(hub))
        assert status in (400, 409), body
        assert payload["error"]
        assert stored_policy(hub, cid)["trust"] == "untrusted"


def test_server_refuses_trusted_connections_and_machinery(hub):
    top, _ = pat(hub, "top-agent", trust="trusted")
    status, payload = patch(hub, top, {"trust": "standard"}, cookie(hub))
    assert status == 409 and "command line" in payload["error"]
    assert stored_policy(hub, top)["trust"] == "trusted"
    seat, _ = pat(hub, "seat-agent")
    hub.db._conn.execute(
        "UPDATE connections SET client='commonplace-orchestrator' WHERE id=?", (seat,)
    )
    status, payload = patch(hub, seat, {"trust": "standard"}, cookie(hub))
    assert status == 409 and stored_policy(hub, seat)["trust"] == "untrusted"
    _, data, _ = get(hub, "connections", cookie(hub))
    assert (
        next(r for r in data["connections"] if r["id"] == seat)["trust_change"]["can_change"]
        is False
    )


def test_lowering_runs_the_same_policy_validation(hub):
    cid, _ = pat(hub, "writer", trust="standard", write_wings=["notes"])
    status, payload = patch(hub, cid, {"trust": "untrusted"}, cookie(hub))
    assert status == 409 and "write_wing" in payload["error"]
    assert stored_policy(hub, cid)["trust"] == "standard"


def test_revoked_connection_cannot_change(hub):
    cid, _ = pat(hub, "gone-agent")
    hub.db._conn.execute(
        "UPDATE connections SET revoked_at=? WHERE id=?", ("2026-01-01T00:00:00Z", cid)
    )
    status, payload = patch(hub, cid, {"trust": "standard"}, cookie(hub))
    assert status == 409 and "revoked or expired" in payload["error"]


def test_db_path_needs_the_signed_in_owner(hub):
    cid, _ = pat(hub, "agent-four")
    uid = member(hub, "other@example.com")
    owner = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    other = sessions.person_principal(hub.db, uid, hub.workspace)
    for actor in (None, other, hub.admin):
        with pytest.raises(ControlError):
            hub.db.set_connection_trust(cid, "standard", actor=actor, workspace_ref=hub.workspace)
    assert stored_policy(hub, cid)["trust"] == "untrusted"
    hub.db.set_connection_trust(cid, "standard", actor=owner, workspace_ref=hub.workspace)
    assert stored_policy(hub, cid)["trust"] == "standard"
