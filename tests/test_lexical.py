"""Golden tokenizer and BM25 values for ``mempalace.lexical``.

The scores were produced by the five copies this module replaced (the
searcher and the chroma, sqlite_exact, pgvector and qdrant backends), which
all agreed bit for bit on this corpus. Any change here changes ranking on
every backend, so it must be deliberate.
"""

import pytest

from mempalace.lexical import bm25_scores, select_lexical_terms, tokenize

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


def test_literal_terms_precede_long_words_and_stop_words_are_removed():
    query = "the authentication configuration verification implementation deployment " * 10
    query += " ZXQ739251 /srv/archive/kestrel/manifest.lock FooBar port_8473"
    terms, skip = select_lexical_terms(query)
    assert skip is None and len(terms) <= 8
    assert {"zxq739251", "manifest", "foobar", "port_8473"} <= set(terms)
    assert "the" not in terms


def test_long_plain_query_has_explicit_skip_reason():
    terms, skip = select_lexical_terms(
        "please help me find where we put the old file and tell me how to use it now"
    )
    assert terms == [] and skip == "long_query_no_literal"


def test_long_words_alone_do_not_make_prose_a_literal_query():
    terms, skip = select_lexical_terms(
        "review the deployment configuration authentication middleware and database "
        "optimization before discussing implementation verification operational followup"
    )
    assert terms == [] and skip == "long_query_no_literal"


def test_short_keywords_and_sentence_punctuation():
    assert select_lexical_terms("the lamp port.") == (["lamp", "port"], None)


def test_frequency_probe_removes_common_terms_and_ranks_rare_literals():
    def frequencies(terms, *, limit):
        assert limit == 2001
        assert len(terms) <= 32
        return {term: {"zq9": 1, "okonkwo": 2}.get(term, 2001) for term in terms}, 60000

    terms, skip = select_lexical_terms(
        'What did Sarah Okonkwo say about "marigold protocol" in /srv/source/file.py ZQ9',
        frequencies=frequencies,
    )
    assert terms == ["zq9", "okonkwo"] and skip is None


def test_frequency_cutoff_uses_five_percent_or_two_thousand():
    assert select_lexical_terms(
        "common rare", frequencies=lambda terms, **kw: ({"common": 1001, "rare": 1}, 20000)
    ) == (["rare"], None)
    assert select_lexical_terms(
        "common", frequencies=lambda terms, **kw: ({"common": 2001}, 60000)
    ) == ([], "no_discriminative_terms")


def test_empty_selection_reports_reason_and_minimum_length_precedes_cap():
    assert select_lexical_terms("session summary") == ([], "no_lexical_terms")
    query = " ".join([f"{i:02d}" for i in range(20)]) + " Marigold"
    assert select_lexical_terms(query, min_length=3) == (["marigold"], None)


def test_long_literal_probe_avoids_surrounding_prose_after_a_match():
    calls = []

    def frequencies(terms, *, limit):
        calls.append(terms)
        return {term: 1 if term == "zq9" else 0 for term in terms}, 20000

    terms, skip = select_lexical_terms(
        "review deployment configuration and authentication middleware before we discuss the implementation verification and followup ZQ9",
        frequencies=frequencies,
    )
    assert terms == ["zq9"] and skip is None
    assert calls == [["zq9"]]
