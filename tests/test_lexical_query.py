"""Candidate queries stay bounded without losing exact identifiers or paths."""

from mempalace.lexical import select_lexical_terms


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
