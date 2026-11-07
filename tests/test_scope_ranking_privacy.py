"""Full scoped responses, including facts and degraded Chroma, ignore hidden wings."""

import json
import random
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from mempalace import mcp_server
from mempalace.backends import chroma
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.searcher.facts import search_facts
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_hnsw_capacity import _seed_chroma_db


def _append_drawers(palace, documents, wing):
    with sqlite3.connect(str(palace / "chroma.sqlite3")) as conn:
        start = conn.execute("SELECT coalesce(max(id), 0) FROM embeddings").fetchone()[0] + 1
        for row_id, text in enumerate(documents, start):
            conn.execute(
                "INSERT INTO embeddings(id,segment_id,embedding_id,seq_id) VALUES(?,?,?,?)",
                (row_id, "seg-meta", f"drawer-{row_id}", b"\x00" * 8),
            )
            conn.executemany(
                "INSERT INTO embedding_metadata(id,key,string_value) VALUES(?,?,?)",
                [(row_id, "wing", wing), (row_id, "chroma:document", text)],
            )
            conn.execute(
                "INSERT INTO embedding_fulltext_search(rowid,string_value) VALUES(?,?)",
                (row_id, text),
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS metadata_values ON embedding_metadata(key,string_value)"
        )


@pytest.mark.parametrize("count", [41, 21_001])
@pytest.mark.parametrize("seed", [0, 1])
def test_fresh_workspace_scoped_token_full_response_ignores_hidden_mutations(
    hub, config, kg, monkeypatch, count, seed
):
    """Real auth/gateway/worker; two principals, facts and >20k-drawer fallback."""
    from pathlib import Path

    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", "chroma")
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp_server, "_vector_disabled", True)
    monkeypatch.setattr(mcp_server, "_vector_disabled_reason", "unavailable")
    monkeypatch.setattr(
        mcp_server,
        "_get_collection",
        lambda *a, **kw: pytest.fail("vector-disabled search opened the vector index"),
    )
    user = hub.db.create_user("new-reader@example.com")
    workspace = hub.db.create_workspace("fresh-ranking-workspace", user)
    hub.db.add_member(workspace, hub.admin, "owner", actor=user)
    public = hub.token(
        "public-reader",
        user=user,
        workspace=workspace,
        scopes=["memory:read", "kg:read"],
        read_wings=["public"],
    )
    other = hub.token(
        "other-reader",
        user=user,
        workspace=workspace,
        scopes=["memory:read", "kg:read"],
        read_wings=["public", "hidden"],
    )
    palace = Path(config.palace_path)
    _seed_chroma_db(str(palace), sqlite_count=0, segment_id="vector")
    rng = random.Random(seed)
    words = ["kestrel", "marigold", "migration", "lantern"]
    _append_drawers(
        palace,
        [" ".join(rng.choices(words, k=rng.randint(1, 6))) + f" note{i}" for i in range(count)],
        "public",
    )
    for i in range(75):
        kg.add_triple(f"Project {i}", "uses", rng.choice(words), wing="public")
    kg._conn().execute("UPDATE triples SET approval_status='approved'")
    queries = ["kestrel marigold", "migration lantern", "hiddenword kestrel"]

    def response(token, query):
        # Keep the JSON-RPC id fixed: compare the complete client response.
        status, raw = hub.raw(
            token,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "mempalace_search",
                    "arguments": {"query": query, "limit": 5, "include_facts": True},
                },
            },
        )
        assert status == 200
        payload = json.loads(raw)
        result = json.loads(payload["result"]["content"][0]["text"])
        assert "error" not in result and "facts_error" not in result
        assert result["results"] and result["facts"]
        assert result["fallback"] == "bm25_only_via_sqlite"
        if token == public:
            assert all(f["wing"] == "public" for f in result["facts"])
        return payload

    before = [response(public, query) for query in queries]
    response(other, "marigold migration")
    assert [response(public, query) for query in queries] == before
    for repeated in ("kestrel", "marigold"):
        _append_drawers(palace, [f"{repeated} hiddenword " * 12] * 1200, "hidden")
        for i in range(80):
            kg.add_triple(f"Hidden {repeated} {i}", "uses", repeated, wing="hidden")
        response(other, repeated)
        assert [response(public, query) for query in queries] == before


@pytest.mark.parametrize("hidden", ["wing", "sensitivity", "time"])
def test_fact_df_and_count_cover_only_the_readable_scope(hidden):
    with KnowledgeGraph.empty() as kg:
        visible = kg.add_triple("Kestrel", "uses", "marigold", wing="public")
        kg.add_triple("Other", "uses", "lantern", wing="public")
        scope = dict(wings=["public"], sensitivities=["internal"], as_of="2026-01-01")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def rows():
            return kg.search_fact_rows(
                {"kestrel", "marigold", "hiddenword"},
                candidate_limit=50,
                common_term_fraction=0.5,
                **scope,
            )

        initial = rows()
        assert len(initial) == 1 and initial[0]["id"] == visible
        assert initial[0]["documents"] == 2
        assert initial[0]["frequencies"] == {"hiddenword": 0, "kestrel": 1, "marigold": 1}
        before = search_facts(kg, "kestrel marigold hiddenword", now=now, **scope)
        for i in range(60):
            kg.add_triple(
                f"Hidden {i}",
                "uses",
                "kestrel hiddenword",
                wing="hidden" if hidden == "wing" else "public",
                sensitivity="private" if hidden == "sensitivity" else "internal",
                valid_from="2027-01-01" if hidden == "time" else None,
            )
        assert rows() == initial
        assert search_facts(kg, "kestrel marigold hiddenword", now=now, **scope) == before


@pytest.mark.parametrize("queue", ["clients", "guards"])
def test_pending_close_failures_retry_automatically_with_capped_backoff(monkeypatch, queue):
    pending = {1: ("palace", object())} if queue == "clients" else {}
    guards = {"palace"} if queue == "guards" else set()
    monkeypatch.setattr(chroma, "_PENDING_CLIENT_CLOSES", pending)
    monkeypatch.setattr(chroma, "_PENDING_GUARD_RELEASES", guards)
    monkeypatch.setattr(chroma, "_pending_close_worker", threading.current_thread())
    attempts, waits = [], []

    def drain(*args, **kwargs):
        attempts.append(1)
        if len(attempts) <= 7:
            raise RuntimeError("transient cleanup failure")
        pending.clear()
        guards.clear()
        return 1

    if queue == "clients":
        monkeypatch.setattr(chroma, "_drain_pending_clients", drain)
        monkeypatch.setattr(chroma, "_drain_pending_guard_releases", lambda **kw: 0)
    else:
        monkeypatch.setattr(chroma, "_drain_pending_guard_releases", drain)
    event = threading.Event()
    monkeypatch.setattr(event, "wait", waits.append)
    monkeypatch.setattr(chroma.threading, "Event", lambda: event)
    chroma._close_pending_clients()
    assert len(attempts) == 8
    assert waits == [0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0]
    assert not pending and not guards
    assert chroma._pending_close_worker is None
