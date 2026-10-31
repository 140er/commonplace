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
        + " what did we decide about the deployment configuration and error handling patterns before we review the database migration next week"
    )
    assert lexical_query_terms(query) == ([], "long_query_no_literal")


def test_long_questions_use_names_and_literals_only():
    terms, skip = lexical_query_terms(
        "What did Sarah Okonkwo say about the budget review at our meeting on Tuesday afternoon with the vendor team last spring?"
    )
    assert skip is None and "okonkwo" in terms
    assert "budget" not in terms
    terms, skip = lexical_query_terms(
        'Please find what we wrote down about the "marigold protocol" from the meeting with the vendor team last spring'
    )
    assert skip is None and {"marigold", "protocol"} <= set(terms)


def test_index_normalization_query_caps_and_shape_reasons():
    assert lexical_query_terms("session summary") == ([], "no_terms_after_stopwords")
    assert lexical_query_terms("the " * 300 + "ZQ9") == ([], "no_terms_after_stopwords")
    assert lexical_query_terms("café PORT foo_bar")[0] == ["foo", "bar", "cafe", "port"]
    terms, _ = lexical_query_terms(" ".join(f"id{i}" for i in range(400)))
    assert len(terms) == 16
    assert all(len(t) <= 64 for t in lexical_query_terms("x" * 400)[0])
    assert lexical_query_terms("aa bb marigold", min_length=3, trigram=True)[0] == ["marigold"]


def test_sentence_starts_and_short_words_are_not_names():
    query = "Remind me what we decided about this project last week. Engineering notes should explain the planned deployment configuration and error handling patterns for this component"
    assert lexical_query_terms(query) == ([], "long_query_no_literal")


def test_repeated_word_keeps_its_highest_priority_class():
    terms, _ = lexical_query_terms("discuss brown next Brown manifest /srv/manifest.lock")
    assert terms.index("manifest") < terms.index("brown") < terms.index("discuss")


def test_closet_query_keeps_all_capped_terms_without_drawer_selection():
    terms, skip = lexical_query_terms(" ".join(f"term{i}" for i in range(300)), closet=True)
    assert skip is None and len(terms) == 256
