"""Decision authority survives migration, restatement, replacement and replication."""

import sqlite3

import pytest

from mempalace import fact_provenance, provenance
from mempalace.knowledge_graph import KnowledgeGraph

AGENT = dict(
    user="avery",
    connection="agent:builder",
    client="codex",
    auth="pat",
    trust="trusted",
    workspace="memory",
)
PERSON = {**AGENT, "connection": "web:avery", "client": "web", "auth": "session", "person": True}


def save(kg, value="5%", **args):
    return kg.save_fact(
        "SimSteer",
        "perf_gate",
        value,
        wing="simsteer",
        source_thread="mcp:origin",
        single_valued=True,
        **args,
    )


def facts(kg):
    return {r["object"]: r for r in kg.query_entity("SimSteer")}


def test_agent_owned_by_user_is_still_a_proposal_and_merge_keeps_origin():
    with KnowledgeGraph.empty() as kg:
        with provenance.verified(AGENT):
            first = save(kg)
        with provenance.verified({**AGENT, "connection": "agent:successor"}):
            again = save(kg)
        row = facts(kg)["5%"]
        assert again["triple_id"] == first["triple_id"]
        assert row["approval_status"] == row["fact_status"] == "proposal"
        assert row["origin_kind"] == "agent" and row["origin_id"] == "agent:builder"
        assert row["origin_link"] == "t3-thread://v1/mcp:origin"
        assert row["approved_by"] is None


def test_person_approval_has_separate_evidence_and_agent_cannot_undo_it():
    with KnowledgeGraph.empty() as kg:
        with provenance.verified(AGENT):
            first = save(kg)
        with provenance.verified(PERSON):
            kg.save_fact(
                "SimSteer",
                "perf_gate",
                "5%",
                wing="simsteer",
                source_thread="mcp:approval",
                single_valued=True,
            )
        approved = facts(kg)["5%"]
        assert approved["origin_id"] == "agent:builder"
        assert approved["approved_by"] == "avery"
        assert approved["approval_link"] == "t3-thread://v1/mcp:approval"
        assert approved["fact_status"] == "approved"
        with provenance.verified(AGENT):
            save(kg)
            rival = save(kg, "2%")
            with pytest.raises(ValueError, match="signed-in person"):
                kg.supersede("SimSteer", "perf_gate", "5%", "1%", wing="simsteer")
            with pytest.raises(ValueError, match="signed-in person"):
                kg.invalidate("SimSteer", "perf_gate", "5%", wing="simsteer")
        rows = facts(kg)
        assert rows["5%"]["valid_to"] is None and rows["5%"]["fact_status"] == "approved"
        assert rows["2%"]["fact_status"] == "proposal"
        assert rival["action"] == "added"
        assert (
            kg.sources([first["triple_id"]])[first["triple_id"]][0]["origin_id"] == "agent:builder"
        )


def test_approving_existing_proposal_retires_competitor_without_changing_origin():
    with KnowledgeGraph.empty() as kg:
        with provenance.verified(PERSON):
            save(kg, valid_from="2026-01-01")
        with provenance.verified(AGENT):
            proposed = save(kg, "2%", valid_from="2026-02-01")
        with provenance.verified(PERSON):
            save(kg, "2%", valid_from="2026-03-01")
        rows = facts(kg)
        assert rows["5%"]["fact_status"] == "superseded"
        assert rows["5%"]["superseded_by"] == proposed["triple_id"]
        assert rows["5%"]["valid_to"] == "2026-03-01T00:00:00Z"
        assert rows["2%"]["fact_status"] == "approved"
        assert rows["2%"]["origin_id"] == "agent:builder"


@pytest.mark.parametrize("approve_existing", [False, True], ids=["new-fact", "existing-proposal"])
@pytest.mark.parametrize(
    "proposal_start, approval_start, proposal_end",
    [
        ("2026-10-20", "2026-10-10", "2026-10-20T00:00:00Z"),
        ("2027-01-01", None, "2027-01-01T00:00:00Z"),
        ("2026-10-01", "2026-10-10", "2026-10-10T00:00:00Z"),
    ],
    ids=["backdated-decision", "future-proposal", "earlier-proposal"],
)
def test_person_approval_retires_agent_proposal_regardless_of_its_start(
    monkeypatch, approve_existing, proposal_start, approval_start, proposal_end
):
    monkeypatch.setattr("mempalace.knowledge_graph.utc_now_z", lambda: "2026-10-30T12:00:00Z")
    with KnowledgeGraph.empty() as kg:
        with provenance.verified(AGENT):
            proposed = save(kg, valid_from=proposal_start)
            if approve_existing:
                existing = kg.save_fact(
                    "SimSteer",
                    "perf_gate",
                    "2%",
                    wing="simsteer",
                    source_thread="mcp:origin",
                    valid_from=approval_start,
                )
        with provenance.verified(PERSON):
            approved = save(kg, "2%", valid_from=approval_start)
        rows = facts(kg)
        decision, proposal = rows["2%"], rows["5%"]
        assert approved["action"] == ("merged" if approve_existing else "superseded")
        assert decision["approval_status"] == decision["fact_status"] == "approved"
        assert decision["current"] and decision["valid_to"] is None
        assert decision["approved_by"] == "avery"
        assert proposal["id"] == proposed["triple_id"]
        assert not proposal["current"] and proposal["fact_status"] == "superseded"
        assert proposal["valid_to"] == proposal_end
        assert proposal["superseded_by"] == approved["triple_id"]
        assert proposal["approval_status"] == "proposal"
        if approve_existing:
            assert approved["triple_id"] == existing["triple_id"]
            assert decision["origin_id"] == "agent:builder"
        for as_of in ("2026-10-30T12:00:00Z", "2027-01-01", "2027-02-01"):
            (current,) = kg.query_entity("SimSteer", as_of=as_of)
            assert current["id"] == approved["triple_id"]


def test_only_approved_later_rival_can_make_a_person_decision_historical():
    with KnowledgeGraph.empty() as kg, provenance.verified(PERSON):
        later = save(kg, valid_from="2026-10-20")
        earlier = save(kg, "2%", valid_from="2026-10-10")
        again = save(kg, "2%", valid_from="2026-10-10")
        rows = facts(kg)
        assert earlier["action"] == "historical" and earlier["current"] == later["triple_id"]
        assert again["action"] == "merged" and again["triple_id"] == earlier["triple_id"]
        assert rows["2%"]["valid_to"] == "2026-10-20T00:00:00Z"
        assert rows["2%"]["approval_status"] == "approved" and not rows["2%"]["current"]
        assert rows["5%"]["valid_to"] is None and rows["5%"]["current"]
        assert rows["5%"]["fact_status"] == "approved"


def test_same_range_approval_keeps_visible_evidence_and_original_author():
    with KnowledgeGraph.empty() as kg:
        with provenance.verified(AGENT):
            first = save(kg, source_seq_start=1, source_seq_end=2)
        with provenance.verified(PERSON):
            approved = save(kg, source_seq_start=1, source_seq_end=2)
            repeat = save(kg, source_seq_start=1, source_seq_end=2)
        assert approved["source_added"] and not repeat["source_added"]
        with provenance.verified(AGENT):
            restated = save(kg, source_seq_start=1, source_seq_end=2)
        assert not restated["source_added"]
        (source,) = kg.sources([first["triple_id"]])[first["triple_id"]]
        assert source["source_thread"] == "mcp:origin"
        assert source["source_seq_start"] == 1 and source["source_seq_end"] == 2
        assert source["origin_kind"] == "agent" and source["origin_id"] == "agent:builder"
        assert source["approval_status"] == "approved" and source["approved_by"] == "avery"
        assert source["approval_link"] == "t3-thread://v1/mcp:origin"
        assert facts(kg)["5%"]["origin_id"] == "agent:builder"


def test_legacy_author_stamp_never_implies_approval(tmp_path):
    path = str(tmp_path / "kg.sqlite3")
    with KnowledgeGraph(path) as kg:
        with provenance.verified(PERSON):
            save(kg)
    with sqlite3.connect(path) as conn:
        for column in fact_provenance.COLUMNS:
            conn.execute(f"ALTER TABLE triples DROP COLUMN {column}")
            conn.execute(f"ALTER TABLE fact_sources DROP COLUMN {column}")
    with KnowledgeGraph(path) as kg:
        row = facts(kg)["5%"]
        assert row["origin_kind"] == row["origin_id"] == row["approval_status"] == "unknown"
        assert row["fact_status"] == "unknown" and row["approved_by"] is None
        with provenance.verified(AGENT):
            save(kg)
        assert facts(kg)["5%"]["fact_status"] == "unknown"


def test_replication_carries_authority_and_old_peer_cannot_erase_it():
    with KnowledgeGraph.empty() as source, KnowledgeGraph.empty() as target:
        with provenance.verified(PERSON):
            save(source)
        for table in source._REPLICATION_TABLES:
            for row in source.dump_rows(table):
                target.apply_row(table, row)
        assert facts(target)["5%"]["fact_status"] == "approved"
        old = source.dump_rows("triples")[0]
        for key in fact_provenance.COLUMNS:
            old.pop(key)
        target.apply_row("triples", old)
        assert facts(target)["5%"]["fact_status"] == "approved"


def test_normalization_retains_authority_and_origin():
    with KnowledgeGraph.empty() as kg:
        with provenance.verified(PERSON):
            old = save(kg, valid_from="2026-01-01")
        kg.rewrite(old["triple_id"], "performance_gate", "5%", at="2026-02-01")
        new = next(r for r in kg.query_entity("SimSteer") if r["predicate"] == "performance_gate")
        assert new["fact_status"] == "approved" and new["approved_by"] == "avery"
        assert new["origin_link"] == "t3-thread://v1/mcp:origin"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://user:pass@example.com/chat",
        "//outside/chat",
        "https://example.com/\nchat",
        "/app/threads/../..",
        "/app/threads/thr_source/..",
        "/app/threads/%2E%2e/%2e.",
        "/app/threads/thr_source%2f..%2f..",
        "",
    ],
)
def test_source_links_reject_non_chat_or_unsafe_urls(url):
    with pytest.raises(ValueError):
        fact_provenance.source_link("mcp:test", url)


@pytest.mark.parametrize("identity", [AGENT, PERSON], ids=["agent-origin", "person-approval"])
def test_dot_segment_source_link_is_rejected_before_writing_fact(identity):
    with KnowledgeGraph.empty() as kg, provenance.verified(identity):
        with pytest.raises(ValueError, match="path"):
            save(kg, source_url="/app/threads/../..")
        assert kg.query_entity("SimSteer") == []


def test_person_marker_must_be_a_verified_boolean():
    with pytest.raises(ValueError):
        provenance.parse_identity({**AGENT, "person": "true"})
    assert provenance.decode_identity(provenance.encode_identity(PERSON))["person"] is True


def test_normalization_does_not_reuse_successor_with_different_authority():
    with KnowledgeGraph.empty() as kg:
        with provenance.verified(PERSON):
            old = save(kg, valid_from="2026-01-01")
        with provenance.verified(AGENT):
            proposed = kg.add_triple(
                "SimSteer",
                "performance_gate",
                "5%",
                valid_from="2026-01-01",
                source_thread="mcp:origin",
                wing="simsteer",
            )
        new = kg.rewrite(old["triple_id"], "performance_gate", "5%", at="2026-02-01")
        assert new != proposed
        row = next(r for r in kg.query_entity("SimSteer") if r["id"] == new)
        assert row["fact_status"] == "approved"


def test_new_handoff_preview_keeps_goal_status_and_provenance_visible():
    from commonplace.handoff.models import HandoffDoc
    from commonplace.handoff.web import _preview
    from commonplace.handoff.writer import render_markdown

    doc = HandoffDoc(
        goal="Ship <SimSteer>",
        status="Checking",
        stored_thread_id="thr_abc",
        source_actor="conn:agent",
    )
    preview = _preview(render_markdown(doc))
    assert "Ship &lt;SimSteer&gt;" in preview and "Checking" in preview
    assert "Provenance" in preview and "proposal" in preview and "/app/threads/thr_abc" in preview


def test_import_preserves_decision_source_and_explicit_approval(tmp_path):
    from mempalace.knowledge_graph import import_graph_rows

    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as kg, provenance.verified(PERSON):
        save(kg)
    import_graph_rows(src, dst, apply=True)
    with KnowledgeGraph(dst) as kg:
        row = facts(kg)["5%"]
        assert row["fact_status"] == "approved" and row["approved_by"] == "avery"
        assert row["origin_link"] == "t3-thread://v1/mcp:origin"


def test_stale_proposal_snapshot_cannot_erase_user_approval():
    with KnowledgeGraph.empty() as kg:
        with provenance.verified(AGENT):
            save(kg)
        stale = kg.dump_rows("triples")[0]
        with provenance.verified(PERSON):
            save(kg)
        kg.apply_row("triples", stale)
        row = facts(kg)["5%"]
        assert row["fact_status"] == "approved"
        assert row["approved_by"] == "avery" and row["approval_link"] == "t3-thread://v1/mcp:origin"
