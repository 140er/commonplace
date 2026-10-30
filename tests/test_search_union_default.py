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
            {"wing": "build", "room": "logs", "source_file": f"log-{i}.txt", "chunk_index": 0}
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
    search("ZXQ739251", palace, collection=col, room="other")
    assert "Build ZXQ739251" not in capsys.readouterr().out
    search("ZXQ739251", palace, collection=col, since="2099-01-01")
    assert "Build ZXQ739251" not in capsys.readouterr().out
