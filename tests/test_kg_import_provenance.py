"""Imports reconcile graph-local ids and history without losing approval evidence."""

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mempalace import fact_provenance, provenance
from mempalace.knowledge_graph import KnowledgeGraph, import_graph_rows

AGENT = {
    "user": "person-a",
    "connection": "agent-a",
    "auth": "pat",
    "client": "codex",
    "trust": "trusted",
    "workspace": "memory",
}
PERSON = {**AGENT, "connection": "web", "client": "web", "auth": "session", "person": True}


def _save(kg, value="old", *, thread="mcp:old", start="2026-01-01", **kwargs):
    return kg.save_fact(
        "Project",
        "uses",
        value,
        wing="project",
        valid_from=start,
        source_thread=thread,
        source_seq_start=1,
        source_seq_end=2,
        single_valued=True,
        **kwargs,
    )["triple_id"]


def _copy_entities(source, target):
    for row in source.dump_rows("entities"):
        target.apply_row("entities", row)


def _triple(kg, triple_id):
    return dict(kg._conn().execute("SELECT * FROM triples WHERE id=?", (triple_id,)).fetchone())


def _snapshot(path):
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("entities", "triples", "fact_sources")
        }


@pytest.mark.parametrize(
    "successor", ["present", "id_conflict", "blocked_id", "different_value", "missing"]
)
def test_import_maps_successors_by_identity_or_reports_the_omitted_link(tmp_path, successor):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        old = _save(source)
        new = _save(source, "new", thread="mcp:new", start="2026-02-01")
        _copy_entities(source, target)
        row = next(r for r in source.dump_rows("triples") if r["id"] == new)
        if successor in ("present", "id_conflict"):
            target.apply_row("triples", {**row, "id": "target-successor"})
        if successor in ("id_conflict", "blocked_id"):
            target.apply_row("triples", {**row, "id": new, "subject": "unrelated"})
        elif successor == "different_value":
            _save(target, "other", thread="mcp:target")
        elif successor == "missing":
            source._conn().execute("UPDATE triples SET superseded_by='absent' WHERE id=?", (old,))
            source._conn().commit()
    source_hash = hashlib.sha256(Path(src).read_bytes()).hexdigest()

    preview = import_graph_rows(src, dst)
    applied = import_graph_rows(src, dst, apply=True)

    assert applied == preview
    assert hashlib.sha256(Path(src).read_bytes()).hexdigest() == source_hash
    with KnowledgeGraph(dst) as target:
        ended = _triple(target, old)
        if successor in ("present", "id_conflict"):
            assert ended["superseded_by"] == "target-successor"
            assert applied["triples"]["unresolved_link_count"] == 0
            if successor == "id_conflict":
                assert _triple(target, new)["subject"] == "unrelated"
        else:
            assert ended["superseded_by"] is None
            assert applied["triples"]["unresolved_link_count"] == 1
            assert applied["triples"]["unresolved_links"] == [
                {"id": old, "superseded_by": "absent" if successor == "missing" else new}
            ]
        assert len(applied["fact_sources"]["skipped"]) == int(
            successor in ("blocked_id", "different_value")
        )
        before = _snapshot(dst)
    retry = import_graph_rows(src, dst, apply=True)
    assert retry["triples"]["added"] == 0
    assert _snapshot(dst) == before


@pytest.mark.parametrize("actor", [AGENT, PERSON], ids=["proposal", "approved"])
@pytest.mark.parametrize("same_id", [False, True], ids=["remapped-id", "same-id"])
def test_import_carries_supersession_end_and_keeps_target_approval(tmp_path, actor, same_id):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(actor):
            old = _save(source)
        target_id = old if same_id else "target-old"
        _copy_entities(source, target)
        target.apply_row("triples", {**source.dump_rows("triples")[0], "id": target_id})
        if actor == PERSON:
            with provenance.verified(PERSON):
                _save(target, thread="mcp:target-approval")
        before_approval = _triple(target, target_id)
        with provenance.verified(actor):
            new = _save(source, "new", thread="mcp:new", start="2026-02-01")
        expected = _triple(source, old)

    preview = import_graph_rows(src, dst)
    assert preview == import_graph_rows(src, dst, apply=True)
    assert preview["triples"]["superseded"] == 1
    assert preview["triples"]["present"] == 1
    with KnowledgeGraph(dst) as target:
        actual = _triple(target, target_id)
        assert actual["status"] == "superseded"
        assert actual["valid_to"] == expected["valid_to"]
        assert actual["superseded_by"] == new
        assert [r["id"] for r in target.query_entity("Project", as_of="2026-02-01")] == [new]
        for col in fact_provenance.COLUMNS:
            assert actual[col] == before_approval[col]
        assert len(target.dump_rows("triples")) == 2


@pytest.mark.parametrize("origin", ["agent", "unknown"])
def test_unapproved_source_cannot_close_an_approved_target(tmp_path, origin):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(AGENT):
            old = _save(source)
            _save(source, "new", thread="mcp:new", start="2026-02-01")
        with provenance.verified(PERSON):
            kept = _save(target, thread="mcp:approved")
        approved = _triple(target, kept)
        if origin == "unknown":
            for table in ("triples", "fact_sources"):
                for col in fact_provenance.COLUMNS:
                    source._conn().execute(f"ALTER TABLE {table} DROP COLUMN {col}")
            source._conn().commit()

    with provenance.verified(PERSON):  # The importer cannot lend authority to legacy evidence.
        preview = import_graph_rows(src, dst)
        applied = import_graph_rows(src, dst, apply=True)
    assert applied == preview
    assert applied["triples"]["superseded"] == 0
    assert any(
        c.get("reason") == "approved_target" and c["id"] == old and c["target_id"] == kept
        for c in applied["triples"]["conflicts"]
    )
    with KnowledgeGraph(dst) as target:
        assert _triple(target, kept) == approved
        assert len(target.dump_rows("triples")) == 1
        assert all(
            s["approval_status"] != "approved"
            for s in target.sources([kept])[kept]
            if s["source_thread"] != "mcp:approved"
        )


def test_approved_history_with_an_unapproved_successor_cannot_close_target(tmp_path):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(PERSON):
            old = _save(source)
        _copy_entities(source, target)
        target.apply_row("triples", source.dump_rows("triples")[0])
        with provenance.verified(AGENT):
            new = _save(source, "new", thread="mcp:new", start="2026-02-01")
        # A peer snapshot must not turn a proposal into authority to retire
        # the earlier approval just by preserving that earlier row's proof.
        source._conn().execute(
            "UPDATE triples SET status='superseded', valid_to='2026-02-01T00:00:00Z', "
            "superseded_by=? WHERE id=?",
            (new, old),
        )
        source._conn().commit()
    counts = import_graph_rows(src, dst, apply=True)
    assert any(c.get("reason") == "approved_target" for c in counts["triples"]["conflicts"])
    with KnowledgeGraph(dst) as target:
        kept = _triple(target, old)
        assert kept["status"] == "active" and kept["valid_to"] is None
        assert kept["approval_status"] == "approved" and kept["superseded_by"] is None


def test_import_without_a_source_table_keeps_the_legacy_first_observation_unknown(tmp_path):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source:
        tid = _save(source)
        source._conn().execute("DROP TABLE fact_sources")
        for col in fact_provenance.COLUMNS:
            source._conn().execute(f"ALTER TABLE triples DROP COLUMN {col}")
        source._conn().commit()
    counts = import_graph_rows(src, dst, apply=True)
    assert counts["fact_sources"]["added"] == 1
    with KnowledgeGraph(dst) as target:
        (observed,) = target.sources([tid])[tid]
        assert observed["source_thread"] == "mcp:old"
        assert observed["source_seq_start"] == 1 and observed["source_seq_end"] == 2
        assert observed["origin_kind"] == observed["approval_status"] == "unknown"


@pytest.mark.parametrize("failure", ["exception", "process_exit"])
def test_import_rolls_back_entities_ends_links_and_sources_on_interruption(tmp_path, failure):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        _save(source)
        _copy_entities(source, target)
        target.apply_row("triples", {**source.dump_rows("triples")[0], "id": "target-old"})
        _save(source, "new", thread="mcp:new", start="2026-02-01")
        if failure == "exception":
            target._conn().executescript("""
                CREATE TRIGGER interrupt_import BEFORE INSERT ON fact_sources
                WHEN NEW.source_thread='mcp:new'
                BEGIN SELECT RAISE(ABORT, 'interrupted import'); END;
            """)
    before, source_before = _snapshot(dst), _snapshot(src)
    if failure == "exception":
        with pytest.raises(sqlite3.IntegrityError, match="interrupted import"):
            import_graph_rows(src, dst, apply=True)
    else:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import os, sys
import mempalace.knowledge_graph as module
original = module._merge_imported_source
def interrupt(conn, row, triple_id):
    result = original(conn, row, triple_id)
    if row.get('source_thread') == 'mcp:new':
        os._exit(37)
    return result
module._merge_imported_source = interrupt
module.import_graph_rows(sys.argv[1], sys.argv[2], apply=True)
""",
                src,
                dst,
            ],
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == 37, result.stderr.decode()
    assert _snapshot(dst) == before
    assert _snapshot(src) == source_before


def test_provenance_round_trip_import_then_replication_retains_all_observations(tmp_path):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source:
        with provenance.verified(AGENT):
            tid = _save(source)
            _save(source, thread="mcp:restatement")
        with provenance.verified(PERSON):
            _save(source, thread="mcp:approval")
            _save(source)  # Approval of an existing range keeps the agent author.
        expected = source.sources([tid])[tid]
        expected_row = _triple(source, tid)
    imported = import_graph_rows(src, dst, apply=True)
    assert imported["fact_sources"]["added"] == 3
    with KnowledgeGraph(dst) as target, KnowledgeGraph.empty() as replica:
        assert target.sources([tid])[tid] == expected
        for table in target._REPLICATION_TABLES:
            for row in target.dump_rows(table):
                replica.apply_row(table, row)
                replica.apply_row(table, row)  # Idempotent replay of both approvals and proposals.
        assert replica.sources([tid])[tid] == expected
        assert target.sources([tid])[tid][0]["origin_kind"] == "agent"
        for col in fact_provenance.COLUMNS:
            assert _triple(replica, tid)[col] == expected_row[col]
    retry = import_graph_rows(src, dst, apply=True)
    assert retry["fact_sources"]["added"] == 0 and retry["fact_sources"]["present"] == 3


def test_replication_maps_source_to_identical_target_and_does_not_reuse_local_source_id():
    with KnowledgeGraph.empty() as source, KnowledgeGraph.empty() as target:
        with provenance.verified(AGENT):
            tid = _save(source)
        _copy_entities(source, target)
        target.apply_row("triples", {**source.dump_rows("triples")[0], "id": "target-fact"})
        other = target.add_triple("Other", "likes", "Tea", source_thread="mcp:unrelated")
        before = target.sources([other])[other]
        source_row = source.dump_rows("fact_sources")[0]
        assert source_row["id"] == target.dump_rows("fact_sources")[0]["id"]
        target.apply_row("fact_sources", source_row)
        target.apply_row("fact_sources", source_row)
        assert target.sources([other])[other] == before
        assert target.sources(["target-fact"])["target-fact"] == source.sources([tid])[tid]
        assert len(target.dump_rows("fact_sources")) == 2


@pytest.mark.parametrize("transport", ["import", "replicate"])
def test_duplicate_source_gains_person_approval_without_erasing_author_or_target_proof(
    tmp_path, transport
):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(AGENT):
            _save(source)
            target_id = _save(target)
        original_source = target.sources([target_id])[target_id][0]
        with provenance.verified(PERSON):
            _save(source)
        if transport == "import":
            assert import_graph_rows(src, dst)["fact_sources"]["present"] == 1
            import_graph_rows(src, dst, apply=True)
        else:
            for row in source.dump_rows("fact_sources"):
                target.apply_row("fact_sources", row)
        (approved_source,) = target.sources([target_id])[target_id]
        assert approved_source["origin_id"] == original_source["origin_id"] == "agent-a"
        assert approved_source["added_at"] == original_source["added_at"]
        assert approved_source["approval_status"] == "approved"
        assert _triple(target, target_id)["approval_status"] == "approved"
        proof = _triple(target, target_id)
        with provenance.verified(AGENT):
            _save(source, thread="mcp:restatement")
        # A stale proposal for the same range cannot erase either approval.
        source._conn().execute(
            "UPDATE fact_sources SET approval_status='proposal', approved_by=NULL, approval_link=NULL"
        )
        source._conn().execute(
            "UPDATE triples SET approval_status='proposal', approved_by=NULL, approval_link=NULL"
        )
        source._conn().commit()
        if transport == "import":
            import_graph_rows(src, dst, apply=True)
        else:
            for row in source.dump_rows("fact_sources"):
                target.apply_row("fact_sources", row)
        for col in ("approval_status", "approved_by", "approval_link"):
            assert _triple(target, target_id)[col] == proof[col]
            assert target.sources([target_id])[target_id][0][col] == approved_source[col]


@pytest.mark.parametrize("transport", ["import", "replicate"])
@pytest.mark.parametrize("evidence", ["legacy", "label_only", "unsafe_link"])
def test_imported_evidence_never_infers_person_approval(tmp_path, transport, evidence):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(PERSON):
            tid = _save(source)
        if evidence == "legacy":
            for table in ("triples", "fact_sources"):
                for col in fact_provenance.COLUMNS:
                    source._conn().execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        else:
            for table in ("triples", "fact_sources"):
                source._conn().execute(
                    f"UPDATE {table} SET approval_link=?",
                    (None if evidence == "label_only" else "javascript:approval",),
                )
        source._conn().commit()
        if transport == "import":
            with provenance.verified(PERSON):
                import_graph_rows(src, dst, apply=True)
        else:
            # dump_rows requires the current schema, as an old peer's own
            # snapshot would. Migration leaves legacy authority unknown.
            source._init_db()
            for table in source._REPLICATION_TABLES:
                for row in source.dump_rows(table):
                    with provenance.verified(PERSON):
                        target.apply_row(table, row)
        assert _triple(target, tid)["approval_status"] == "unknown"
        (observed,) = target.sources([tid])[tid]
        assert observed["approval_status"] == "unknown"
        assert "approved_by" not in observed and "approval_link" not in observed


def test_replication_refuses_source_without_matching_triple_identity():
    with KnowledgeGraph.empty() as source, KnowledgeGraph.empty() as target:
        _save(source)
        row = source.dump_rows("fact_sources")[0]
        with pytest.raises(ValueError, match="no matching target triple"):
            target.apply_row("fact_sources", row)
        row.pop("_triple")
        with pytest.raises(ValueError, match="missing triple identity"):
            target.apply_row("fact_sources", row)
        assert target.dump_rows("fact_sources") == []
