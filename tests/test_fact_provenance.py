"""Decision authority survives migration, restatement, replacement and replication."""

import sqlite3

import pytest

from mempalace import fact_provenance, provenance
from mempalace.knowledge_graph import KnowledgeGraph

AGENT = dict(
    user="jeffrey",
    connection="sol:builder",
    client="codex",
    auth="pat",
    trust="trusted",
    workspace="memory",
)
PERSON = {**AGENT, "connection": "web:jeffrey", "client": "web", "auth": "session", "person": True}


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
        with provenance.verified({**AGENT, "connection": "opus:successor"}):
            again = save(kg)
        row = facts(kg)["5%"]
        assert again["triple_id"] == first["triple_id"]
        assert row["approval_status"] == row["fact_status"] == "proposal"
        assert row["origin_kind"] == "agent" and row["origin_id"] == "sol:builder"
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
        assert approved["origin_id"] == "sol:builder"
        assert approved["approved_by"] == "jeffrey"
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
        assert kg.sources([first["triple_id"]])[first["triple_id"]][0]["origin_id"] == "sol:builder"


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
        assert rows["2%"]["origin_id"] == "sol:builder"


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
        assert new["fact_status"] == "approved" and new["approved_by"] == "jeffrey"
        assert new["origin_link"] == "t3-thread://v1/mcp:origin"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://user:pass@example.com/chat",
        "//outside/chat",
        "https://example.com/\nchat",
        "",
    ],
)
def test_source_links_reject_non_chat_or_unsafe_urls(url):
    with pytest.raises(ValueError):
        fact_provenance.source_link("mcp:test", url)


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
        assert row["fact_status"] == "approved" and row["approved_by"] == "jeffrey"
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
        assert (
            row["approved_by"] == "jeffrey" and row["approval_link"] == "t3-thread://v1/mcp:origin"
        )
