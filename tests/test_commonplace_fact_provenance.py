"""Only a person session can approve a fact through the actual gateway."""

import json

import pytest

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture  # noqa: F401

FACT = dict(
    subject="SimSteer",
    predicate="perf_gate",
    object="5%",
    theme="shared",
    source={"thread": "mcp:agent-proposal", "seq_start": 1},
)


def _call(hub, token, args):
    return hub.rpc(token, "tools/call", {"name": "fact_save", "arguments": args})["result"]


def test_owner_agent_token_does_not_establish_user_approval(hub):
    owner = hub.owner_token(name="build-agent")
    assert not _call(hub, owner, FACT).get("isError")
    row = hub.call(owner, "mempalace_kg_query", entity="SimSteer")[0]["facts"][0]
    assert row["origin_kind"] == "agent" and row["origin_id"] == "build-agent"
    assert row["fact_status"] == "proposal" and row["approved_by"] is None
    assert row["origin_link"] == "t3-thread://v1/mcp:agent-proposal"


@pytest.mark.parametrize(
    "claim",
    [
        {"origin_kind": "user"},
        {"approval_status": "approved"},
        {"person": True},
        {"approved_by": "Avery"},
        {"status": "approved"},
    ],
)
def test_agent_cannot_self_approve(hub, claim):
    token = hub.owner_token()
    result = _call(hub, token, {**FACT, **claim})
    assert result.get("isError") and "authenticated" in result["content"][0]["text"]
    assert hub.call(token, "mempalace_kg_query", entity="SimSteer")[0]["count"] == 0


def test_missing_source_is_refused(hub):
    result = _call(hub, hub.owner_token(), {k: v for k, v in FACT.items() if k != "source"})
    assert result.get("isError") and "source thread" in result["content"][0]["text"]


def test_invalid_source_error_mentions_optional_url(hub):
    result = _call(hub, hub.owner_token(), {**FACT, "source": {"unexpected": "value"}})
    assert result.get("isError") and "url" in result["content"][0]["text"]


def test_human_save_approves_with_proof_and_later_agent_save_retains_it(hub):
    token = hub.owner_token(name="original-agent")
    assert not _call(hub, token, FACT).get("isError")
    link = sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    session = sessions.redeem_login_link(hub.db, link)
    status, body = hub.raw(
        None,
        {**FACT, "source": {"thread": "mcp:user-approval", "seq_start": 9}},
        path="/api/facts",
        headers={"Cookie": f"{sessions.COOKIE}={session}"},
    )
    assert status == 201, body
    saved = json.loads(body)
    assert saved["fact_status"] == "approved" and saved["approved_by"] == hub.admin
    assert saved["origin_id"] == "original-agent"
    assert saved["approval_link"] == "t3-thread://v1/mcp:user-approval"
    assert not _call(hub, token, FACT).get("isError")
    row = hub.call(token, "mempalace_kg_query", entity="SimSteer")[0]["facts"][0]
    assert row["approval_status"] == "approved" and row["origin_id"] == "original-agent"
    assert len(row["sources"]) == 2
    assert row["sources"][1]["origin_kind"] == "user"


def test_fact_http_keeps_agent_authority_and_enforces_scope(hub):
    owner = hub.owner_token()
    status, body = hub.raw(owner, FACT, path="/api/facts")
    assert status == 201 and json.loads(body)["fact_status"] == "proposal"
    reader = hub.token(
        "reader", profile="full", scopes=["kg:read"], read_wings=["shared"], trust="standard"
    )
    status, _ = hub.raw(reader, FACT, path="/api/facts")
    assert status == 403


def test_cross_origin_human_approval_is_refused(hub):
    link = sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    session = sessions.redeem_login_link(hub.db, link)
    status, _ = hub.raw(
        None,
        FACT,
        path="/api/facts",
        headers={"Cookie": f"{sessions.COOKIE}={session}", "Origin": "https://unrelated.example"},
    )
    assert status == 403


def test_external_chat_requires_link_and_keeps_supplied_link_on_every_source(hub):
    token = hub.owner_token()
    external = {**FACT, "source": {"thread": "external-session", "seq_start": 4}}
    refused = _call(hub, token, external)
    assert refused.get("isError") and "source.url" in refused["content"][0]["text"]
    external["source"]["url"] = "https://chat.example/threads/external-session"
    assert not _call(hub, token, external).get("isError")
    row = hub.call(token, "mempalace_kg_query", entity="SimSteer")[0]["facts"][0]
    assert row["origin_link"] == external["source"]["url"]
    assert row["sources"][0]["origin_link"] == external["source"]["url"]
