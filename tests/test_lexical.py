"""Golden tokenizer and BM25 values for ``mempalace.lexical``.

The scores were produced by the five copies this module replaced (the
searcher and the chroma, sqlite_exact, pgvector and qdrant backends), which
all agreed bit for bit on this corpus. Any change here changes ranking on
every backend, so it must be deliberate.
"""

import pytest

from mempalace.lexical import bm25_scores, lexical_query_terms, tokenize

CORPUS = [
    "Kubernetes rollout notes: we rolled back the deploy on Friday.",
    "The cat sat on the mat. The mat was red.",
    "Caf\xe9 r\xe9sum\xe9 na\xefve — d\xe9j\xe0 vu \xfcber stra\xdfe",
    "東京で会議がありました 会議 会議",
    "stray \udcff byte and caf\xe9 again",
    "",
    None,
    "deploy deploy deploy kubernetes",
    "a b c d",
    "Rust CLI: cargo clippy --all-targets warnings fixed",
]

TOKENS = [
    ["kubernetes", "rollout", "notes", "we", "rolled", "back", "the", "deploy", "on", "friday"],
    ["the", "cat", "sat", "on", "the", "mat", "the", "mat", "was", "red"],
    ["caf\xe9", "r\xe9sum\xe9", "na\xefve", "d\xe9j\xe0", "vu", "\xfcber", "stra\xdfe"],
    ["東京で会議がありました", "会議", "会議"],
    # A lone surrogate (a stray source byte, kept by the lossless decode) is
    # not a word character, so it never becomes a token.
    ["stray", "byte", "and", "caf\xe9", "again"],
    [],
    [],
    ["deploy", "deploy", "deploy", "kubernetes"],
    [],
    ["rust", "cli", "cargo", "clippy", "all", "targets", "warnings", "fixed"],
]

GOLDEN = {
    "kubernetes deploy": [1.965713858106934, 0, 0, 0, 0, 0, 0, 4.152877067572238, 0, 0],
    "the mat": [0.982856929053467, 4.015397748905191, 0, 0, 0, 0, 0, 0, 0, 0],
    "caf\xe9": [0, 0, 1.214218193957073, 0, 1.440236058395825, 0, 0, 0, 0, 0],
    "会議": [0, 0, 0, 3.220781349628192, 0, 0, 0, 0, 0, 0],
    "stra\xdfe \xfcber": [0, 0, 3.265709424252474, 0, 0, 0, 0, 0, 0, 0],
    "clippy warnings": [0, 0, 0, 0, 0, 0, 0, 0, 0, 3.028107283441865],
    "": [0] * 10,
    "a": [0] * 10,
    "nothing matches here": [0] * 10,
}


def test_tokens_match_the_golden_corpus():
    assert [tokenize(d) for d in CORPUS] == TOKENS


@pytest.mark.parametrize("query", list(GOLDEN))
def test_bm25_scores_match_the_golden_corpus(query):
    assert bm25_scores(query, CORPUS) == pytest.approx(GOLDEN[query], rel=1e-12, abs=0.0)


def test_ranking_order_matches_the_golden_corpus():
    scores = bm25_scores("kubernetes deploy", CORPUS)
    ranked = sorted(range(len(CORPUS)), key=lambda i: -scores[i])
    assert ranked[:2] == [7, 0]


def test_stop_words_drop_from_query_and_documents():
    stop = frozenset({"the", "on", "was"})
    assert tokenize(CORPUS[1], stop) == ["cat", "sat", "mat", "mat", "red"]
    # Only stop words in the query: no scores at all.
    assert bm25_scores("the on", CORPUS, stop_words=stop) == [0.0] * len(CORPUS)


def test_literal_name_and_other_priority_is_stable():
    terms, skip = lexical_query_terms(
        'please find deployment getUser ID734 "/srv/manifest.lock" Sarah Okonkwo'
    )
    assert skip is None
    assert terms.index("getuser") < terms.index("deployment")
    assert terms.index("id734") < terms.index("sarah")
    assert terms.index("sarah") < terms.index("deployment")


@pytest.mark.parametrize("starter", ["Remind", "Engineering", "ERROR", "follow-up"])
def test_long_prose_does_not_turn_initial_capital_caps_or_hyphens_into_literals(starter):
    query = (
        starter
        + " what did we decide about the deployment configuration and error handling patterns before we review the database migration next week implementation verification architecture latency"
    )
    assert lexical_query_terms(query) == ([], "long_query_no_literal")


def test_long_questions_use_names_and_literals_only():
    terms, skip = lexical_query_terms(
        "What did Sarah Okonkwo say about the budget review at our meeting on Tuesday afternoon with the vendor team last spring planning implementation verification architecture latency?"
    )
    assert skip is None and "okonkwo" in terms
    assert "budget" not in terms
    terms, skip = lexical_query_terms(
        'Please find what we wrote down about the "marigold protocol" from the meeting with the vendor team last spring'
    )
    assert skip is None and "marigold protocol" in terms


def test_index_normalization_query_caps_and_shape_reasons():
    assert lexical_query_terms("session summary") == ([], "no_terms_after_stopwords")
    assert lexical_query_terms("the " * 300 + "ZQ9") == ([], "no_terms_after_stopwords")
    assert lexical_query_terms("café PORT foo_bar")[0] == ["foo_bar", "cafe", "port"]
    terms, _ = lexical_query_terms(" ".join(f"id{i}" for i in range(400)))
    assert len(terms) == 16
    assert all(len(t) <= 64 for t in lexical_query_terms("x" * 400)[0])
    assert lexical_query_terms("aa bb marigold", min_length=3, trigram=True)[0] == ["marigold"]


def test_sentence_starts_and_short_words_are_not_names():
    query = "Remind me what we decided about this project last week. Engineering notes should explain the planned deployment configuration and error handling patterns for this component"
    assert lexical_query_terms(query) == ([], "long_query_no_literal")


def test_repeated_word_keeps_its_highest_priority_class():
    terms, _ = lexical_query_terms("discuss brown next Brown manifest /srv/manifest.lock")
    assert terms.index("/srv/manifest.lock") < terms.index("brown") < terms.index("discuss")


def test_closet_query_keeps_all_capped_terms_without_drawer_selection():
    terms, skip = lexical_query_terms(" ".join(f"term{i}" for i in range(300)), closet=True)
    assert skip is None and len(terms) == 256


def test_ascii_fast_path_equals_sqlite_unicode61_property():
    import random

    from mempalace.lexical import IndexWordNormalizer

    rng = random.Random(739251)
    alphabet = "".join(chr(i) for i in range(128) if chr(i) != "_")
    with IndexWordNormalizer() as normalize:
        for _ in range(1000):
            text = "".join(rng.choices(alphabet, k=rng.randrange(300)))
            assert normalize(text) == normalize.native(text)
        assert normalize("foo_bar café cafe 東京 中文 a 1") == [
            "foo",
            "bar",
            "cafe",
            "cafe",
            "東京",
            "中文",
            "a",
            "1",
        ]


def test_index_phrase_tf_and_match_floor():
    from mempalace.lexical import index_bm25_scores

    scores = index_bm25_scores(["foo_bar"], ["foo bar", "bar foo", "foo_bar foo_bar"])
    assert scores[0] > 0 and scores[1] == 0 and scores[2] > scores[0]
    assert index_bm25_scores(["cafe"], ["café"])[0] > 0
    assert index_bm25_scores(["needle"], ["mismatch"], [{"needle"}])[0] > 0


@pytest.mark.parametrize("backend", ["chroma", "sqlite_exact", "compact"])
@pytest.mark.parametrize("saturated", [False, True])
def test_index_literals_are_single_phrases_among_subword_confusers(
    tmp_path, monkeypatch, backend, saturated
):
    from mempalace.backends import embedding_wrapper
    from mempalace.palace import get_closets_collection, get_collection
    from mempalace.searcher import search_memories

    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", backend)
    monkeypatch.setattr(
        embedding_wrapper, "_embed_texts", lambda texts, **kw: [[1.0, 0.0]] * len(texts)
    )
    palace = str(tmp_path / "palace")
    col = get_collection(palace, create=True)
    shapes = [
        ("foo_bar", "foo_bar", ["foo", "bar"]),
        ("src/main.py", "src/main.py", ["src", "main", "py"]),
        ("build.alpha.7", "build.alpha.7", ["build", "alpha", "7"]),
        ("server:8473", "server:8473", ["server", "8473"]),
        ('"marigold protocol"', "marigold protocol", ["marigold", "protocol"]),
        ("café", "café", ["coffee", "crema"]),
        ("cafe", "cafe", ["coffee", "crema"]),
        ("项目部署配置", "项目部署配置", ["项目", "部署", "配置"]),
        ("x" * 81, "x" * 81, ["xenon", "xavier"]),
    ]
    count = 1005 if saturated else 20
    rows = [(f"n{i}", text, [0.0, 0.0]) for i, (_, text, _) in enumerate(shapes)]
    for i in range(count):
        for j, word in enumerate(dict.fromkeys(w for _, _, words in shapes for w in words)):
            rows.append((f"d{i}-{j}", word + " ordinary unrelated commentary", [0.01, 1.0]))
    for start in range(0, len(rows), 500):
        batch = rows[start : start + 500]
        col.add(
            ids=[r[0] for r in batch],
            documents=[r[1] for r in batch],
            embeddings=[r[2] for r in batch],
            metadatas=[{"wing": "public"}] * len(batch),
        )
    for i, (query, _, _) in enumerate(shapes):
        lexical = col.union_lexical_search(query=query, n_results=5, where={"wing": "public"})
        assert f"n{i}" in {h.id for h in lexical.hits}, (backend, query, lexical)
        if i < 5:
            assert lexical.selected_terms == [query.strip('"')]
        result = search_memories(query, palace, wing="public", n_results=5, include_facts=False)
        match = next(h for h in result["results"] if h["drawer_id"] == f"n{i}")
        assert match["matched_via"] == "bm25_backend" and match["bm25_score"] > 0
    # Closet queries also normalize both sides of an underscore identically.
    closets = get_closets_collection(palace, create=True)
    closets.add(
        ids=["closet-needle", "closet-confuser"],
        documents=["foo_bar|team|→n0", "foo alpha|team|→n1"],
        embeddings=[[1.0, 0.0]] * 2,
        metadatas=[{"wing": "public"}] * 2,
    )
    scoped = closets.union_lexical_search(
        query="foo_bar", n_results=1, where={"wing": "public"}, closet=True
    )
    assert scoped.hits[0].id == "closet-needle" and scoped.hits[0].score > 0


def test_function_words_do_not_make_everyday_question_long():
    terms, skip = lexical_query_terms(
        "can you remind me what we agreed on for the release notes of the mobile app"
    )
    assert skip is None and {"agreed", "release", "mobile", "app"} <= set(terms)


@pytest.mark.parametrize("backend", ["sqlite_exact", "compact"])
def test_unicode61_closet_accents_match_develop_without_match_floor(tmp_path, monkeypatch, backend):
    from mempalace.palace import get_closets_collection

    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", backend)
    col = get_closets_collection(str(tmp_path), create=True)
    col.add(
        ids=["accent", "plain", "underscore", "confuser"],
        documents=["café opening hours", "cafe menu update", "foo_bar identifier", "foo alpha"],
        embeddings=[[1.0, 0.0]] * 4,
        metadatas=[{"wing": "public"}] * 4,
    )
    for query in ("cafe", "foo_bar"):
        before = col.lexical_search(query=query, where={"wing": "public"})
        after = col.union_lexical_search(query=query, where={"wing": "public"}, closet=True)
        assert [h.id for h in after.hits] == [h.id for h in before.hits]
        assert all(h.score > 0 for h in after.hits)


def test_chroma_literal_keeps_separators_and_word_normalization(tmp_path, monkeypatch):
    from mempalace.backends import embedding_wrapper
    from mempalace.palace import get_collection
    from mempalace.searcher import search_memories

    monkeypatch.setenv("MEMPALACE_BACKEND_EXPLICIT", "chroma")
    monkeypatch.setattr(
        embedding_wrapper, "_embed_texts", lambda texts, **kw: [[1.0, 0.0]] * len(texts)
    )
    col = get_collection(str(tmp_path), create=True)
    col.add(
        ids=[f"v{i}" for i in range(30)] + ["needle"],
        documents=["foo bar" + "!" * (i + 1) for i in range(30)] + ["foo_bar"],
        embeddings=[[0.01, 1.0]] * 30 + [[0.0, 0.0]],
        metadatas=[{"wing": "public"}] * 31,
    )
    result = search_memories(
        "foo_bar", str(tmp_path), wing="public", n_results=1, include_facts=False
    )
    assert result["results"][0]["drawer_id"] == "needle"
    assert result["results"][0]["matched_via"] == "bm25_backend"
    from mempalace.lexical import index_bm25_scores

    assert index_bm25_scores(["cafe"], ["café"], trigram=True) == [0]
    assert index_bm25_scores(["src/main.py"], ["src main py", "src/main.py"], trigram=True)[0] == 0
