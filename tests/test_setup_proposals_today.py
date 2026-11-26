"""Setup proposals in Waiting on you (card #675 slice 3): the signed-in owner's queue lists every
waiting setup with the digest to decide it and counts it; nobody else gets the bucket, and a
decided setup leaves it."""

import json

from tests._projects_helpers import projects_hub_fixture  # noqa: F401

GROK = {"provider": "grok", "model": "grok-4.7"}
SETUP = {
    "bots": [{"key": "lead", "name": "Work lead"}],
    "coworkers": [{"key": "w", "board": "work", "bot": "lead", "title": "Work"}],
    "models": {"workspace": {"main": GROK}},
}


def _needs(h, auth):
    status, body = h.request(auth, "GET", "/api/needs-me")[:2]
    assert status == 200, body
    return body


def test_the_owner_sees_each_waiting_setup_with_its_digest_and_it_is_counted(projects_hub):
    h = projects_hub
    before = _needs(h, h.owner)
    assert before["setup_proposals"] == []
    view = json.loads(h.mcp(h.a1, "setup_propose", **SETUP)[1]["text"])
    needs = _needs(h, h.owner)
    (item,) = needs["setup_proposals"]
    assert needs["count"] == before["count"] + 1
    assert (item["kind"], item["id"], item["ref"], item["sha256"]) == (
        "setup",
        view["id"],
        "v1",
        view["sha256"],
    )
    assert item["proposer_name"] == "a1" and item["summary"] == view["summary"]
    assert [line["action"] for line in item["changes"]] == ["create", "create", "set"]
    assert set(item) == {
        "kind",
        "id",
        "ref",
        "sha256",
        "proposed_by",
        "proposer_name",
        "proposed_at",
        "summary",
        "changes",
        "problems",
    }
    # A member cannot read the owner queue, and agents (even the owner's own) get no bucket.
    member = h.request(h.member, "GET", "/api/needs-me")[2]
    assert b"setup_proposals" not in member and view["sha256"].encode() not in member
    assert "setup_proposals" not in _needs(h, h.a1)
    # Deciding it with the digest the queue showed takes it out of the queue.
    status, _ = h.request(
        h.owner, "POST", f"/api/setup-proposals/{item['id']}/apply", {"sha256": item["sha256"]}
    )[:2]
    assert status == 200
    after = _needs(h, h.owner)
    assert after["setup_proposals"] == [] and after["count"] == before["count"]
