"""Stale histories and peer snapshots must preserve each local fact instance."""

import pytest

from mempalace import fact_provenance, provenance
from mempalace.knowledge_graph import KnowledgeGraph, import_graph_rows

AGENT = {
    "user": "person-a",
    "connection": "agent-a",
    "client": "test",
    "auth": "pat",
    "trust": "trusted",
    "workspace": "test",
}
PERSON = {**AGENT, "connection": "web-a", "auth": "session", "person": True}


def _save(kg, value="Acme", *, start=None, thread="mcp:first"):
    return kg.save_fact(
        "Alice",
        "works_at",
        value,
        valid_from=start,
        wing="shared",
        single_valued=True,
        source_thread=thread,
        source_seq_start=1,
        source_seq_end=2,
    )["triple_id"]


def _row(kg, triple_id):
    return dict(kg._conn().execute("SELECT * FROM triples WHERE id=?", (triple_id,)).fetchone())


def _copy_entities(source, target):
    for row in source.dump_rows("entities"):
        target.apply_row("entities", row)


@pytest.mark.parametrize(
    "source_person, current_person",
    [(False, False), (True, True), (True, False)],
    ids=["reopen", "approved-reopen", "old-approval-stays-on-history"],
)
def test_import_exact_history_preserves_the_reasserted_current_instance(
    tmp_path, source_person, current_person
):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(PERSON if source_person else AGENT):
            old = _save(source)
            successor = _save(source, "Beta", start="2026-03-01", thread="mcp:beta")
        source._conn().backup(target._conn())
        with provenance.verified(PERSON if source_person else AGENT):
            target.invalidate("Alice", "works_at", "Beta", ended="2026-06-01", wing="shared")
        with provenance.verified(PERSON if current_person else AGENT):
            current = _save(target, thread="mcp:reasserted")
        # Different graph-local ids and sorting must not route old approval,
        # observations or links onto the reassertion instead of exact history.
        target._conn().execute("UPDATE triples SET id='zz-history' WHERE id=?", (old,))
        target._conn().execute(
            "UPDATE fact_sources SET triple_id='zz-history' WHERE triple_id=?", (old,)
        )
        target._conn().execute("UPDATE triples SET id='aa-current' WHERE id=?", (current,))
        target._conn().execute(
            "UPDATE fact_sources SET triple_id='aa-current' WHERE triple_id=?", (current,)
        )
        target._conn().commit()
        before = _row(target, "aa-current")
        observed = target.sources(["aa-current"])["aa-current"]
        assert before["valid_from"] is None and _row(source, old)["valid_from"] is None
        source._conn().execute(
            "INSERT INTO fact_sources (triple_id, source_thread, added_at) VALUES (?, ?, ?)",
            (old, "mcp:extra-history", "2026-01-01T00:00:00Z"),
        )
        source._conn().commit()

    preview = import_graph_rows(src, dst)
    applied = import_graph_rows(src, dst, apply=True)

    assert applied == preview
    assert applied["triples"]["present"] == 1
    assert applied["triples"]["superseded"] == 0
    assert applied["triples"]["end_not_carried"] == []
    assert applied["triples"]["id_conflicts"] == [successor]
    with KnowledgeGraph(dst) as target:
        assert _row(target, "aa-current") == before
        assert target.sources(["aa-current"])["aa-current"] == observed
        assert [f["id"] for f in target.query_entity("Alice") if f["current"]] == ["aa-current"]
        assert [s["source_thread"] for s in target.sources(["zz-history"])["zz-history"]] == [
            "mcp:first",
            "mcp:extra-history",
        ]


def test_import_merges_only_one_exact_instance(tmp_path):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(AGENT):
            tid = _save(source)
        _copy_entities(source, target)
        original = source.dump_rows("triples")[0]
        target.apply_row("triples", original)
        target.apply_row("triples", {**original, "id": "another-instance"})
        before = _row(target, "another-instance")
        with provenance.verified(PERSON):
            _save(source, thread="mcp:approval")
    counts = import_graph_rows(src, dst, apply=True)
    assert counts["triples"]["present"] == 1
    with KnowledgeGraph(dst) as target:
        assert _row(target, tid)["approval_status"] == "approved"
        assert _row(target, "another-instance") == before
        assert target.sources(["another-instance"])["another-instance"] == []


def test_import_does_not_choose_between_two_open_instances(tmp_path):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(PERSON):
            old = _save(source)
        _copy_entities(source, target)
        opened = {
            **source.dump_rows("triples")[0],
            "approval_status": "proposal",
            "approved_by": None,
            "approval_link": None,
        }
        target.apply_row("triples", opened)
        target.apply_row("triples", {**opened, "id": "second-open"})
        before = [_row(target, old), _row(target, "second-open")]
        with provenance.verified(PERSON):
            _save(source, "Beta", start="2026-03-01", thread="mcp:beta")
    counts = import_graph_rows(src, dst, apply=True)
    assert old in counts["triples"]["id_conflicts"]
    assert counts["triples"]["superseded"] == 0
    with KnowledgeGraph(dst) as target:
        assert [_row(target, old), _row(target, "second-open")] == before


@pytest.mark.parametrize("failure", ["id_conflict", "missing", "conflict"])
def test_import_leaves_target_open_when_its_successor_is_not_accepted(tmp_path, failure):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        old = _save(source)
        source._conn().backup(target._conn())
        new = _save(source, "Beta", start="2026-03-01", thread="mcp:beta")
        expected_successor = new
        if failure == "id_conflict":
            other = target.add_triple("Bob", "likes", "Tea")
            target._conn().execute("UPDATE triples SET id=? WHERE id=?", (new, other))
            target._conn().commit()
        elif failure == "missing":
            expected_successor = "missing-successor"
            source._conn().execute(
                "UPDATE triples SET superseded_by=? WHERE id=?",
                (expected_successor, old),
            )
            source._conn().commit()
        else:
            target.add_triple("Alice", "works_at", "Third", wing="shared")
        before = _row(target, old)

    preview = import_graph_rows(src, dst)
    applied = import_graph_rows(src, dst, apply=True)
    assert applied == preview
    assert applied["triples"]["superseded"] == 0
    assert applied["triples"]["end_not_carried"] == [
        {"id": old, "target_id": old, "superseded_by": expected_successor},
    ]
    with KnowledgeGraph(dst) as target:
        assert _row(target, old) == before
        assert "Acme" in [f["object"] for f in target.query_entity("Alice") if f["current"]]
        if failure == "id_conflict":
            assert _row(target, new)["subject"] == "bob"


@pytest.mark.parametrize("authority", ["agent", "legacy", "approval-label-only"])
def test_replication_cannot_close_an_approved_local_fact_or_change_its_author(authority):
    with KnowledgeGraph.empty() as target:
        with provenance.verified(PERSON):
            tid = _save(target)
        before = _row(target, tid)
        peer = {
            **target.dump_rows("triples")[0],
            "valid_to": "2026-03-01",
            "status": "superseded",
            "superseded_by": "missing-successor",
            "origin_kind": "agent",
            "origin_id": "peer",
            "origin_link": "t3-thread://v1/mcp:peer",
            "approval_status": "proposal",
            "approved_by": None,
            "approval_link": None,
            "source_thread": "mcp:peer",
        }
        if authority == "legacy":
            for col in fact_provenance.COLUMNS:
                peer.pop(col)
        elif authority == "approval-label-only":
            peer["approval_status"] = "approved"
        report = target.apply_row("triples", peer)
        assert not report["applied"] and report["reason"] == "approved_target"
        assert report["unresolved_links"] == [{"id": tid, "superseded_by": "missing-successor"}]
        assert _row(target, tid) == before


def test_replication_preserves_authorship_and_first_source_when_accepting_person_approval():
    with KnowledgeGraph.empty() as target:
        with provenance.verified(AGENT):
            tid = _save(target)
        before = _row(target, tid)
        peer = {
            **target.dump_rows("triples")[0],
            "origin_kind": "user",
            "origin_id": "person-b",
            "origin_link": "t3-thread://v1/mcp:peer",
            "source_thread": "mcp:peer",
            "source_seq_start": 8,
            "source_seq_end": 9,
            "source_file": "peer.txt",
            "source_drawer_id": "peer-drawer",
            "adapter_name": "peer-adapter",
            "extracted_at": "2026-01-01",
            "approval_status": "approved",
            "approved_by": "person-b",
            "approval_link": "t3-thread://v1/mcp:peer",
        }
        report = target.apply_row("triples", peer)
        assert report["applied"]
        actual = _row(target, tid)
        for col in (
            "origin_kind",
            "origin_id",
            "origin_link",
            "source_thread",
            "source_seq_start",
            "source_seq_end",
            "source_file",
            "source_drawer_id",
            "adapter_name",
            "extracted_at",
            "hub_stamp",
        ):
            assert actual[col] == before[col]
        assert actual["approval_status"] == "approved" and actual["approved_by"] == "person-b"


def test_person_approved_replication_may_close_an_approved_target_with_a_local_successor():
    with KnowledgeGraph.empty() as target:
        with provenance.verified(PERSON):
            tid = _save(target)
            successor = target.add_triple(
                "Alice",
                "works_at",
                "Beta",
                wing="shared",
                source_thread="mcp:beta",
            )
        before = _row(target, tid)
        peer = {
            **target.dump_rows("triples")[0],
            "status": "superseded",
            "valid_to": "2026-03-01",
            "superseded_by": successor,
            "approved_by": "person-b",
            "approval_link": "t3-thread://v1/mcp:peer",
        }
        report = target.apply_row("triples", peer)
        assert report["applied"] and report["unresolved_links"] == []
        after = _row(target, tid)
        assert after["status"] == "superseded" and after["superseded_by"] == successor
        assert after["valid_to"] == "2026-03-01"
        for col in fact_provenance.COLUMNS:
            assert after[col] == before[col]


def test_replication_omits_missing_successor_and_resolves_it_on_replay():
    with KnowledgeGraph.empty() as source, KnowledgeGraph.empty() as target:
        old = _save(source)
        new = _save(source, "Beta", start="2026-03-01", thread="mcp:beta")
        _copy_entities(source, target)
        old_row, new_row = source.dump_rows("triples")
        report = target.apply_row("triples", old_row)
        assert report["applied"]
        assert report["unresolved_links"] == [{"id": old, "superseded_by": new}]
        assert _row(target, old)["superseded_by"] is None
        target.apply_row("triples", new_row)
        replay = target.apply_row("triples", old_row)
        assert replay["applied"] and replay["unresolved_links"] == []
        assert _row(target, old)["superseded_by"] == new


@pytest.mark.parametrize("approved", [False, True], ids=["proposal", "approved"])
def test_replication_keeps_an_open_target_until_the_successor_arrives(approved):
    with KnowledgeGraph.empty() as source, KnowledgeGraph.empty() as target:
        with provenance.verified(PERSON if approved else AGENT):
            old = _save(source)
        _copy_entities(source, target)
        target.apply_row("triples", source.dump_rows("triples")[0])
        before = _row(target, old)
        with provenance.verified(PERSON if approved else AGENT):
            new = _save(source, "Beta", start="2026-03-01", thread="mcp:beta")
        old_row, new_row = source.dump_rows("triples")
        report = target.apply_row("triples", old_row)
        assert not report["applied"] and report["reason"] == "unresolved_successor"
        assert _row(target, old) == before
        target.apply_row("triples", new_row)
        assert target.apply_row("triples", old_row)["applied"]
        assert _row(target, old)["superseded_by"] == new


def test_replication_skips_a_reused_fact_id_with_different_identity():
    with KnowledgeGraph.empty() as target:
        tid = _save(target)
        before = _row(target, tid)
        peer = {**target.dump_rows("triples")[0], "predicate": "likes"}
        report = target.apply_row("triples", peer)
        assert not report["applied"] and report["reason"] == "id_conflict"
        assert _row(target, tid) == before


def test_replication_skips_an_orphan_observation_and_continues_to_the_next_row():
    with KnowledgeGraph.empty() as source, KnowledgeGraph.empty() as target:
        orphan = _save(source)
        known = source.add_triple("Bob", "likes", "Tea", source_thread="mcp:known")
        _copy_entities(source, target)
        target.apply_row(
            "triples", next(r for r in source.dump_rows("triples") if r["id"] == known)
        )
        reports = [
            target.apply_row("fact_sources", row) for row in source.dump_rows("fact_sources")
        ]
        assert not reports[0]["applied"] and reports[0]["reason"] == "no_matching_triple"
        assert reports[0]["triple_id"] == orphan
        assert reports[1]["applied"] and reports[1]["triple_id"] == known
        assert target.sources([known])[known] == source.sources([known])[known]
        assert len(target.dump_rows("fact_sources")) == 1
