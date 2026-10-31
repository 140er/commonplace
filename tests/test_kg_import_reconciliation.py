"""Stale histories and peer snapshots must preserve each local fact instance."""

import argparse

import pytest

from mempalace import fact_provenance, paths, provenance
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
    with KnowledgeGraph(dst) as target:
        sources_before = target.sources(["another-instance"])["another-instance"]
        (legacy_source,) = sources_before
        assert legacy_source["source_thread"] == original["source_thread"]
        assert legacy_source["source_seq_start"] == 1 and legacy_source["source_seq_end"] == 2
        assert legacy_source["approval_status"] == legacy_source["origin_kind"] == "unknown"
    counts = import_graph_rows(src, dst, apply=True)
    assert counts["triples"]["present"] == 1
    with KnowledgeGraph(dst) as target:
        assert _row(target, tid)["approval_status"] == "approved"
        assert _row(target, "another-instance") == before
        assert target.sources(["another-instance"])["another-instance"] == sources_before


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


@pytest.mark.parametrize(
    "source_person, current_person",
    [(False, False), (True, True), (True, False)],
    ids=["divergent-history", "approved-divergence", "no-approval-transfer"],
)
def test_import_reports_its_divergent_instance_instead_of_matching_a_reassertion(
    tmp_path, source_person, current_person
):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(PERSON if source_person else AGENT):
            old = _save(source)
        source._conn().backup(target._conn())
        with provenance.verified(PERSON if source_person else AGENT):
            target.invalidate("Alice", "works_at", "Acme", ended="2026-02-01", wing="shared")
        with provenance.verified(PERSON if current_person else AGENT):
            current = _save(target, thread="mcp:reasserted")
        before = {tid: _row(target, tid) for tid in (old, current)}
        observed = target.sources([old, current])
        with provenance.verified(PERSON if source_person else AGENT):
            _save(source, "Beta", start="2026-03-01", thread="mcp:beta")
        source._conn().execute(
            "INSERT INTO fact_sources (triple_id, source_thread, added_at) VALUES (?, ?, ?)",
            (old, "mcp:extra-history", "2026-01-01T00:00:00Z"),
        )
        source._conn().commit()
        assert before[current]["valid_from"] == _row(source, old)["valid_from"] is None

    preview = import_graph_rows(src, dst)
    applied = import_graph_rows(src, dst, apply=True)

    assert applied == preview
    assert applied["triples"]["id_conflicts"] == [old]
    assert applied["triples"]["added"] == applied["triples"]["present"] == 0
    assert applied["triples"]["superseded"] == 0
    assert applied["fact_sources"]["added"] == 0
    assert len(applied["fact_sources"]["skipped"]) == 3
    with KnowledgeGraph(dst) as target:
        assert {tid: _row(target, tid) for tid in (old, current)} == before
        assert target.sources([old, current]) == observed
        assert [f["id"] for f in target.query_entity("Alice") if f["current"]] == [current]


def _seed_rows(kg, rows):
    conn = kg._conn()
    with conn:
        for entity in {r[i] for r in rows for i in (1, 3)}:
            conn.execute(
                "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)", (entity, entity)
            )
        conn.executemany(
            "INSERT INTO triples (id, subject, predicate, object, valid_from, valid_to, status, "
            "superseded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


@pytest.mark.parametrize("exact", [False, True], ids=["pending-open-end", "exact-history"])
def test_import_reports_a_second_source_claim_without_rewriting_the_first(tmp_path, exact):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    # Put both Acme instances before either successor, reproducing snapshots
    # whose row order differs from the order in which the facts were filed.
    first = ("t_a1", "alice", "works_at", "acme", None, "2026-03-01", "superseded", "t_b1")
    # The target id is not held by another source instance, so both source
    # histories may attempt a remap. Only the first may claim that target.
    target_id = "target-first"
    target_first = (target_id, *first[1:])
    beta = ("t_b1", "alice", "works_at", "beta", "2026-03-01", "2026-04-01", "active", None)
    second_end = "2026-03-01" if exact else "2026-05-01"
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        _seed_rows(
            source,
            [
                first,
                ("t_a3", "alice", "works_at", "acme", None, second_end, "superseded", "t_g1"),
                beta,
                ("t_g1", "alice", "works_at", "gamma", "2026-05-01", None, "active", None),
            ],
        )
        _seed_rows(
            target,
            [target_first, beta]
            if exact
            else [
                (target_id, "alice", "works_at", "acme", None, None, "active", None),
            ],
        )
        source._conn().execute(
            "UPDATE triples SET approval_status='approved', approved_by='person-a', "
            "approval_link='/app/threads/thr_approval' WHERE id='t_a3'"
        )
        source._conn().executemany(
            "INSERT INTO fact_sources (triple_id, source_thread, added_at, approval_status, "
            "approved_by, approval_link) VALUES (?, ?, '2026-01-01', ?, ?, ?)",
            [
                ("t_a1", "mcp:first", "unknown", None, None),
                ("t_a3", "mcp:second", "approved", "person-a", "/app/threads/thr_approval"),
            ],
        )
        source._conn().commit()

    preview = import_graph_rows(src, dst)
    applied = import_graph_rows(src, dst, apply=True)

    assert applied == preview
    assert applied["triples"]["superseded"] == int(not exact)
    [conflict] = applied["triples"]["conflicts"]
    assert conflict["reason"] == "target_already_claimed"
    assert conflict["id"] == "t_a3" and conflict["target_id"] == target_id
    assert conflict["claimed_by"] == "t_a1"
    assert applied["fact_sources"]["skipped"] == [2]
    with KnowledgeGraph(dst) as target:
        old = _row(target, target_id)
        assert old["valid_to"] == "2026-03-01" and old["superseded_by"] == "t_b1"
        assert old["approval_status"] == "unknown"
        assert [s["source_thread"] for s in target.sources([target_id])[target_id]] == ["mcp:first"]
        assert not target._conn().execute("SELECT 1 FROM triples WHERE id='t_a3'").fetchone()


@pytest.mark.parametrize("approved", [False, True], ids=["proposal", "approved"])
@pytest.mark.parametrize(
    "change",
    [
        "stale",
        "reopen",
        "clear-successor",
        "reactivate",
        "extend-end",
        "shorten-end",
        "replace-successor",
    ],
)
def test_replication_rejects_changes_to_an_established_end_or_successor(approved, change):
    with KnowledgeGraph.empty() as target:
        with provenance.verified(PERSON if approved else AGENT):
            old = _save(target)
            stale = target.dump_rows("triples")[0]
            successor = _save(target, "Beta", start="2026-03-01", thread="mcp:beta")
        before = _row(target, old)
        peer = next(row for row in target.dump_rows("triples") if row["id"] == old)
        if change == "stale":
            peer = stale
        elif change == "reopen":
            peer["valid_to"] = None
        elif change == "clear-successor":
            peer["superseded_by"] = None
        elif change == "reactivate":
            peer["status"] = "active"
        elif change == "extend-end":
            peer["valid_to"] = "2026-04-01"
        elif change == "shorten-end":
            peer["valid_to"] = "2026-02-01"
        else:
            peer["superseded_by"] = "another-successor"
        report = target.apply_row("triples", peer)
        assert not report["applied"] and report["reason"] == "lifecycle_regression"
        assert _row(target, old) == before
        assert [f["id"] for f in target.query_entity("Alice") if f["current"]] == [successor]


def test_replication_can_complete_an_invalidation_without_replacing_its_end():
    with KnowledgeGraph.empty() as target, provenance.verified(PERSON):
        old = _save(target)
        target.invalidate("Alice", "works_at", "Acme", ended="2026-03-01", wing="shared")
        successor = target.add_triple(
            "Alice", "works_at", "Beta", wing="shared", source_thread="mcp:beta"
        )
        before = _row(target, old)
        peer = next(row for row in target.dump_rows("triples") if row["id"] == old)
        peer.update(status="superseded", superseded_by=successor)
        assert target.apply_row("triples", peer)["applied"]
        after = _row(target, old)
        assert after["valid_to"] == before["valid_to"]
        assert after["status"] == "superseded" and after["superseded_by"] == successor
        for column in fact_provenance.COLUMNS:
            assert after[column] == before[column]


def test_replication_from_an_old_peer_without_lifecycle_keys_keeps_local_history():
    with KnowledgeGraph.empty() as target, provenance.verified(PERSON):
        old = _save(target)
        _save(target, "Beta", start="2026-03-01", thread="mcp:beta")
        before = _row(target, old)
        peer = next(row for row in target.dump_rows("triples") if row["id"] == old)
        for column in ("status", "valid_to", "superseded_by", *fact_provenance.COLUMNS):
            peer.pop(column)
        assert target.apply_row("triples", peer)["applied"]
        assert _row(target, old) == before


@pytest.mark.parametrize("apply", [False, True], ids=["preview", "apply"])
def test_cli_labels_an_already_closed_approved_conflict_accurately(tmp_path, capsys, apply):
    from mempalace.cli import cmd_kg

    src = str(tmp_path / "source.db")
    palace = tmp_path / "palace"
    dst = str(palace / paths.KNOWLEDGE_GRAPH_FILENAME)
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(AGENT):
            old = _save(source)
        source._conn().backup(target._conn())
        with provenance.verified(PERSON):
            _save(target, thread="mcp:approval")
            target.invalidate(
                "Alice",
                "works_at",
                "Acme",
                ended="2026-03-01T00:00:00Z",
                wing="shared",
            )
        with provenance.verified(AGENT):
            _save(source, "Beta", start="2026-03-01", thread="mcp:beta")
        before = _row(target, old)
    cmd_kg(
        argparse.Namespace(
            kg_action="import-legacy",
            palace=str(palace),
            source=src,
            yes=apply,
        )
    )
    output = capsys.readouterr().out
    assert "approved target kept as is" in output and "approved target kept open" not in output
    with KnowledgeGraph(dst) as target:
        assert _row(target, old) == before


@pytest.mark.parametrize("closed", [False, True], ids=["open-exact", "closed-exact"])
def test_import_exact_match_cannot_move_old_approval_onto_a_reassertion(tmp_path, closed):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        with provenance.verified(PERSON):
            old = _save(source)
        source._conn().backup(target._conn())
        with provenance.verified(PERSON):
            target.invalidate("Alice", "works_at", "Acme", ended="2026-02-01", wing="shared")
        with provenance.verified(AGENT):
            current = _save(target, thread="mcp:reasserted")
            if closed:
                target.invalidate("Alice", "works_at", "Acme", ended="2026-03-01", wing="shared")
        if closed:
            with provenance.verified(PERSON):
                source.invalidate("Alice", "works_at", "Acme", ended="2026-03-01", wing="shared")
        before = {tid: _row(target, tid) for tid in (old, current)}
        observed = target.sources([old, current])

    preview = import_graph_rows(src, dst)
    applied = import_graph_rows(src, dst, apply=True)
    assert applied == preview
    assert applied["triples"]["id_conflicts"] == [old]
    assert applied["triples"]["present"] == applied["triples"]["added"] == 0
    assert applied["triples"]["superseded"] == 0
    assert applied["fact_sources"]["added"] == 0
    assert len(applied["fact_sources"]["skipped"]) == 1
    with KnowledgeGraph(dst) as target:
        assert {tid: _row(target, tid) for tid in (old, current)} == before
        assert target.sources([old, current]) == observed
        assert _row(target, current)["approval_status"] == "proposal"


def test_import_does_not_claim_an_open_id_held_by_another_source_row(tmp_path):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    current = ("t_a2", "alice", "works_at", "acme", None, None, "active", None)
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        _seed_rows(
            source,
            [
                ("t_a1", "alice", "works_at", "acme", None, "2026-03-01", "superseded", "t_b1"),
                ("t_b1", "alice", "works_at", "beta", "2026-03-01", "2026-04-01", "active", None),
                current,
            ],
        )
        _seed_rows(target, [current])
        source._conn().execute(
            "UPDATE triples SET approval_status='approved', approved_by='person-a', "
            "approval_link='/app/threads/thr_old' WHERE id='t_a1'"
        )
        source._conn().execute(
            "INSERT INTO fact_sources (triple_id, source_thread, added_at) "
            "VALUES ('t_a1', 'mcp:old', '2026-01-01')"
        )
        source._conn().commit()
        before = _row(target, "t_a2")
    preview = import_graph_rows(src, dst)
    applied = import_graph_rows(src, dst, apply=True)
    assert applied == preview
    assert applied["triples"]["added"] == 2
    assert applied["triples"]["present"] == 1
    assert applied["triples"]["superseded"] == 0
    assert applied["triples"]["conflicts"] == applied["triples"]["id_conflicts"] == []
    with KnowledgeGraph(dst) as target:
        assert _row(target, "t_a2") == before
        assert target.sources(["t_a2"])["t_a2"] == []
        assert _row(target, "t_a1")["superseded_by"] == "t_b1"
        assert target.sources(["t_a1"])["t_a1"][0]["source_thread"] == "mcp:old"


@pytest.mark.parametrize("target_id", [None, "t_d1", "t_d2", "target-history"])
def test_import_identical_source_duplicates_share_observations_without_another_merge(
    tmp_path, target_id
):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    content = ("alice", "likes", "tea", None, "2026-01-01", "active", None)
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        _seed_rows(source, [("t_d1", *content), ("t_d2", *content)])
        if target_id:
            _seed_rows(target, [(target_id, *content)])
        # Duplicate source metadata is not another lifecycle/approval merge.
        # Put the extra approval on the alias, whichever native id is present.
        alias_id = "t_d1" if target_id == "t_d2" else "t_d2"
        source._conn().execute(
            "UPDATE triples SET approval_status='approved', approved_by='person-a', "
            "approval_link='/app/threads/thr_duplicate' WHERE id=?",
            (alias_id,),
        )
        source._conn().executemany(
            "INSERT INTO fact_sources (triple_id, source_thread, added_at) "
            "VALUES (?, ?, '2026-01-01')",
            [("t_d1", "mcp:first"), ("t_d2", "mcp:duplicate"), ("t_d2", "mcp:first")],
        )
        source._conn().commit()
    preview = import_graph_rows(src, dst)
    applied = import_graph_rows(src, dst, apply=True)
    assert applied == preview
    assert applied["triples"]["added"] == int(target_id is None)
    assert applied["triples"]["present"] == 2 - int(target_id is None)
    assert applied["triples"]["conflicts"] == applied["triples"]["id_conflicts"] == []
    assert applied["triples"]["superseded"] == 0
    assert applied["fact_sources"] == {"added": 2, "present": 1, "skipped": []}
    with KnowledgeGraph(dst) as target:
        [stored] = target.dump_rows("triples")
        assert stored["id"] == (target_id or "t_d1")
        assert stored["approval_status"] == "unknown"
        assert {s["source_thread"] for s in target.sources([stored["id"]])[stored["id"]]} == {
            "mcp:first",
            "mcp:duplicate",
        }
        before = _row(target, stored["id"])
        observations = target.dump_rows("fact_sources")
    retry = import_graph_rows(src, dst, apply=True)
    assert retry["triples"]["present"] == 2
    assert retry["triples"]["conflicts"] == []
    assert retry["fact_sources"] == {"added": 0, "present": 3, "skipped": []}
    with KnowledgeGraph(dst) as target:
        assert _row(target, stored["id"]) == before
        assert target.dump_rows("fact_sources") == observations


def test_import_exact_match_rejects_an_id_owned_by_a_different_source_instance(tmp_path):
    src, dst = str(tmp_path / "source.db"), str(tmp_path / "target.db")
    history = ("alice", "likes", "tea", None, "2026-01-01", "active", None)
    with KnowledgeGraph(src) as source, KnowledgeGraph(dst) as target:
        _seed_rows(
            source,
            [
                ("t_old", *history),
                ("t_other", "bob", "likes", "coffee", None, "2026-01-01", "active", None),
            ],
        )
        _seed_rows(target, [("t_other", *history)])
        source._conn().execute(
            "UPDATE triples SET approval_status='approved', approved_by='person-a', "
            "approval_link='/app/threads/thr_old' WHERE id='t_old'"
        )
        source._conn().execute(
            "INSERT INTO fact_sources (triple_id, source_thread, added_at) "
            "VALUES ('t_old', 'mcp:old', '2026-01-01')"
        )
        source._conn().commit()
        before = _row(target, "t_other")
    applied = import_graph_rows(src, dst, apply=True)
    assert applied["triples"]["id_conflicts"] == ["t_other"]
    assert applied["triples"]["added"] == 1 and applied["triples"]["present"] == 0
    with KnowledgeGraph(dst) as target:
        assert _row(target, "t_other") == before
        assert target.sources(["t_other"])["t_other"] == []
        assert target.sources(["t_old"])["t_old"][0]["source_thread"] == "mcp:old"


@pytest.mark.parametrize("legacy", [False, True], ids=["snapshot-id", "legacy-triple-id"])
def test_replication_observation_cannot_approve_a_different_reassertion(legacy):
    with KnowledgeGraph.empty() as source, KnowledgeGraph.empty() as target:
        with provenance.verified(PERSON):
            old = _save(source)
        source._conn().backup(target._conn())
        with provenance.verified(PERSON):
            target.invalidate("Alice", "works_at", "Acme", ended="2026-02-01", wing="shared")
        with provenance.verified(AGENT):
            current = _save(target, thread="mcp:reasserted")
        before = {tid: _row(target, tid) for tid in (old, current)}
        observed = target.sources([old, current])
        [peer] = source.dump_rows("fact_sources")
        assert peer["_triple"]["id"] == old
        if legacy:
            peer["_triple"].pop("id")
        report = target.apply_row("fact_sources", peer)
        assert not report["applied"] and report["reason"] == "no_matching_triple"
        assert {tid: _row(target, tid) for tid in (old, current)} == before
        assert target.sources([old, current]) == observed
