"""Default entrypoints must find literal artifacts outside the vector pool."""

import pytest

from mempalace.palace import get_collection
from mempalace.searcher import search, search_memories


@pytest.fixture
def lexical_palace(tmp_path, monkeypatch):
    import mempalace.backends.embedding_wrapper as wrapper

    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", "sqlite_exact")
    monkeypatch.setattr(wrapper, "_embed_texts", lambda texts, **kw: [[1.0, 0.0] for _ in texts])
    palace = str(tmp_path / "palace")
    col = get_collection(palace, create=True)
    # More distractors than either vector pool fetches. The exact artifact
    # has no vector signal, as if tool output had never been embedded.
    col.add(
        ids=[f"noise-{i}" for i in range(40)] + ["artifact"],
        documents=[f"Routine engineering note {i}" for i in range(40)]
        + ["Build ZXQ739251 failed reading /srv/archive/kestrel/manifest.lock on port 8473."],
        embeddings=[[0.01, 1.0]] * 40 + [[0.0, 0.0]],
        metadatas=[
            {
                "wing": "build",
                "room": "logs",
                "source_file": f"log-{i}.txt",
                "chunk_index": 0,
                "filed_at": "2026-03-01T12:00:00Z",
            }
            for i in range(41)
        ],
    )
    return palace, col


@pytest.mark.parametrize("query", ["ZXQ739251", "/srv/archive/kestrel/manifest.lock", "8473"])
def test_default_search_finds_zero_vector_artifact(lexical_palace, query):
    palace, _ = lexical_palace
    old = search_memories(query, palace, candidate_strategy="vector")
    assert "error" not in old
    assert "artifact" not in {hit["drawer_id"] for hit in old["results"]}

    default = search_memories(query, palace)
    explicit = search_memories(query, palace, candidate_strategy="union")
    assert "error" not in default
    assert default["results"] == explicit["results"]
    assert default["results"][0]["drawer_id"] == "artifact"
    assert default["results"][0]["distance"] is None


def test_cli_default_finds_zero_vector_artifact(lexical_palace, capsys):
    palace, col = lexical_palace
    search("ZXQ739251", palace, collection=col, candidate_strategy="vector")
    assert "Build ZXQ739251" not in capsys.readouterr().out
    search("ZXQ739251", palace, collection=col)
    assert "Build ZXQ739251" in capsys.readouterr().out


def test_cli_union_respects_room_filter_and_date_window(lexical_palace, capsys):
    palace, col = lexical_palace
    search("ZXQ739251", palace, collection=col, room="logs", since="2026-03-01")
    assert "Build ZXQ739251" in capsys.readouterr().out
    search("ZXQ739251", palace, collection=col, room="other")
    assert "Build ZXQ739251" not in capsys.readouterr().out
    search("ZXQ739251", palace, collection=col, since="2099-01-01")
    assert "Build ZXQ739251" not in capsys.readouterr().out


@pytest.mark.parametrize("source", ["", "log-0.txt"])
def test_lexical_candidates_use_stored_identity_not_source(lexical_palace, capsys, source):
    palace, col = lexical_palace
    col.update(ids=["artifact"], metadatas=[{"source_file": source}], embeddings=[[0.0, 0.0]])
    col.update(ids=["noise-0"], embeddings=[[0.1, 1.0]])
    vector = search_memories("ZXQ739251", palace, candidate_strategy="vector")
    assert "artifact" not in {hit["drawer_id"] for hit in vector["results"]}
    assert "noise-0" in {hit["drawer_id"] for hit in vector["results"]}
    result = search_memories("ZXQ739251", palace)
    assert "artifact" in {hit["drawer_id"] for hit in result["results"]}
    assert result["results"][0]["matched_via"] == "bm25_backend"
    assert all("_stored_id" not in hit for hit in result["results"])
    search("ZXQ739251", palace, collection=col)
    assert "Build ZXQ739251" in capsys.readouterr().out


def test_default_union_filters_both_wings_with_in_scope_drawers(lexical_palace, capsys):
    palace, col = lexical_palace
    col.add(
        ids=["other-artifact"],
        documents=["Hidden ZXQOTHER8540 log entry"],
        embeddings=[[0.0, 0.0]],
        metadatas=[{"wing": "other", "room": "logs", "filed_at": "2026-03-01T12:00:00Z"}],
    )
    query = "ZXQ739251 ZXQOTHER8540"
    result = search_memories(query, palace, wing="build", room="logs", since="2026-03-01")
    ids = {hit["drawer_id"] for hit in result["results"]}
    assert "artifact" in ids and "other-artifact" not in ids
    search(query, palace, collection=col, wing="build", room="logs", since="2026-03-01")
    output = capsys.readouterr().out
    assert "Build ZXQ739251" in output and "Hidden ZXQOTHER8540" not in output


@pytest.mark.parametrize("backend,max_decoded", [("chroma", 500), ("sqlite_exact", 10)])
def test_filtered_lexical_search_bounds_hydration(tmp_path, monkeypatch, backend, max_decoded):
    import importlib

    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", backend)
    col = get_collection(str(tmp_path / "palace"), create=True)
    for start in range(0, 1200, 400):
        col.add(
            ids=[f"d{i}" for i in range(start, start + 400)],
            documents=[f"Commonword record {i}" for i in range(start, start + 400)],
            embeddings=[[1.0, 0.0]] * 400,
            metadatas=[
                {"wing": "target" if i % 2 else "other", "room": "logs"}
                for i in range(start, start + 400)
            ],
        )
    module = importlib.import_module(
        f"mempalace.backends.{'chroma_sqlite' if backend == 'chroma' else backend}"
    )
    decode = module.decode_record
    count = 0

    def counted(*args, **kwargs):
        nonlocal count
        count += 1
        return decode(*args, **kwargs)

    monkeypatch.setattr(module, "decode_record", counted)
    result = col.lexical_search(
        query="commonword record",
        n_results=2,
        where={"$and": [{"wing": "target"}, {"room": "logs"}]},
    )
    assert len(result.hits) == 2
    assert all(hit.metadata["wing"] == "target" for hit in result.hits)
    assert 0 < count <= max_decoded


def test_lexical_date_window_uses_wider_candidate_pool(lexical_palace):
    palace, col = lexical_palace
    col.add(
        ids=[f"in-window-{i}" for i in range(100)],
        documents=[f"Routine current engineering note {i}" for i in range(100)],
        embeddings=[[0.01, 1.0]] * 100,
        metadatas=[{"wing": "build", "room": "logs", "filed_at": "2026-03-01"}] * 100,
    )
    col.add(
        ids=[f"old-{i}" for i in range(25)],
        documents=[f"ZXQ739251 ZXQ739251 old record {i}" for i in range(25)],
        embeddings=[[0.0, 0.0]] * 25,
        metadatas=[{"wing": "build", "room": "logs", "filed_at": "2025-01-01"}] * 25,
    )
    result = search_memories("ZXQ739251", palace, since="2026-01-01", n_results=5)
    assert "artifact" in {hit["drawer_id"] for hit in result["results"]}
    assert (
        next(hit for hit in result["results"] if hit["drawer_id"] == "artifact")["matched_via"]
        == "bm25_backend"
    )


def test_long_plain_query_skips_lexical_and_keeps_vector_results(lexical_palace, monkeypatch):
    palace, col = lexical_palace
    query = (
        "review the deployment configuration authentication middleware and database "
        "optimization before discussing implementation verification operational followup"
    )
    vector = search_memories(query, palace, candidate_strategy="vector")
    monkeypatch.setattr(
        col, "lexical_search", lambda **kw: pytest.fail("long plain query used lexical")
    )
    default = search_memories(query, palace)
    assert default["lexical_skipped"] == "long_query_no_literal"
    assert default["results"] == vector["results"]


def test_long_literal_query_still_finds_lexical_only_needle(lexical_palace):
    palace, _ = lexical_palace
    query = (
        "review the deployment configuration authentication middleware and database "
        "optimization before discussing implementation verification operational followup ZXQ739251"
    )
    result = search_memories(query, palace)
    needle = next(hit for hit in result["results"] if hit["drawer_id"] == "artifact")
    assert needle["matched_via"] == "bm25_backend"
    assert "lexical_skipped" not in result


def test_default_cli_keeps_vector_results_without_lexical_support(
    lexical_palace, monkeypatch, capsys
):
    from mempalace.backends import UnsupportedCapabilityError

    palace, col = lexical_palace

    def unsupported(**kwargs):
        raise UnsupportedCapabilityError("lexical search is unavailable")

    monkeypatch.setattr(col, "lexical_search", unsupported)
    search("engineering", palace, collection=col)
    assert "Routine engineering note" in capsys.readouterr().out


@pytest.mark.parametrize("backend", ["chroma", "sqlite_exact"])
def test_long_vector_query_preserves_public_lexical_closet_boost(tmp_path, monkeypatch, backend):
    import mempalace.backends.embedding_wrapper as wrapper
    from mempalace.palace import get_closets_collection

    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", backend)
    monkeypatch.setattr(wrapper, "_embed_texts", lambda texts, **kw: [[1.0, 0.0] for _ in texts])
    palace = str(tmp_path / "palace")
    drawers = get_collection(palace, create=True)
    drawers.add(
        ids=["needle"],
        documents=["Nightly backup moved after the outage."],
        embeddings=[[1.0, 0.0]],
        metadatas=[{"source_file": "backup.md"}],
    )
    closets = get_closets_collection(palace, create=True)
    closets.add(
        ids=["pointer"],
        documents=["backup storage cluster outage|nightly job|→needle"],
        embeddings=[[1.0, 0.0]],
        metadatas=[{"source_file": "backup.md"}],
    )
    query = "where did we decide to move the nightly backup job after that storage cluster outage happened last month"
    assert closets.lexical_search(query=query).hits
    for strategy in ("vector", "union"):
        result = search_memories(query, palace, candidate_strategy=strategy)
        assert result["results"][0]["closet_boost"] == 0.4
        assert result["results"][0]["matched_via"] == "drawer+closet"


@pytest.mark.parametrize(
    "query,text",
    [
        (
            "What did Sarah Okonkwo say about the budget review at our meeting on Tuesday afternoon?",
            "Okonkwo signed off on it.",
        ),
        (
            'Please find what we wrote down about the "marigold protocol" from the meeting with the vendor team last spring',
            "Marigold protocol: rotate keys every Friday.",
        ),
        (
            "Traceback at /srv/archive/project/src/module/file.py line 42 in handler: KeyError PR-38 E42 ZQ9",
            "Ticket ZQ9 tracks the flaky import.",
        ),
    ],
)
def test_long_question_shapes_find_lexical_only_needles(lexical_palace, query, text):
    palace, col = lexical_palace
    col.update(ids=["artifact"], documents=[text], embeddings=[[0.0, 0.0]])
    result = search_memories(query, palace)
    needle = next(hit for hit in result["results"] if hit["drawer_id"] == "artifact")
    assert needle["matched_via"] == "bm25_backend"


def test_stop_words_only_query_reports_skip(lexical_palace):
    palace, _ = lexical_palace
    assert search_memories("session summary", palace)["lexical_skipped"] == "no_lexical_terms"
