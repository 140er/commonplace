"""Tokenizer and Okapi-BM25 scoring shared by the searcher and every backend.

Kept dependency-free so importing it costs nothing on the wake-up path.
"""

import math
import re
from functools import lru_cache

TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)
_QUERY_PART_RE = re.compile(r"[/\w]+(?:[.\-:/][/\w]+)*", re.UNICODE)


@lru_cache(maxsize=1)
def _query_stop_words():
    from .i18n import get_stopwords

    # The historical ranking list omits these basic function words. Union
    # selection drops them without changing the full-query hybrid scorer.
    return frozenset(get_stopwords("en")) | {"and", "what", "did"}


def lexical_query_terms(
    query, stop_words=frozenset(), *, min_length=2, trigram=False, closet=False
):
    """RFC 009 shape-only selection. Corpus-dependent selection happens in scope."""
    import unicodedata

    ignored = stop_words if closet else _query_stop_words() | stop_words
    quoted = {
        word.lower()
        for span in re.findall(r'"([^"\n]+)"|“([^”\n]+)”', query or "")
        for text in span
        for word in TOKEN_RE.findall(text)
    }
    candidates = {}
    start, last_end, token_count, seen_tokens = True, 0, 0, 0
    for match in _QUERY_PART_RE.finditer(query or ""):
        if re.search(r"[.!?](?:\s|$)", query[last_end : match.start()]):
            start = True
        part = match.group()
        technical = any(c in part for c in "_/.:") or (
            any(c.isupper() for c in part[1:]) and any(c.islower() for c in part)
        )
        words = TOKEN_RE.findall(part) if trigram else re.findall(r"[^\W_]{2,}", part)
        for word in words:
            seen_tokens += 1
            if seen_tokens > 256:
                break
            term = word.lower()
            if not trigram:
                term = "".join(
                    c for c in unicodedata.normalize("NFD", term) if unicodedata.category(c) != "Mn"
                )
            term = term[:64]
            name = word[0].isupper() and word[1:].islower() and not start
            start = False
            if term in ignored:
                continue
            token_count += 1
            if len(term) < min_length:
                continue
            literal = technical or any(c.isdigit() for c in word) or word.lower() in quoted
            priority = 0 if literal else 1 if name else 2
            candidates[term] = min(candidates.get(term, priority), priority)
        start, last_end = False, match.end()
        if seen_tokens >= 256:
            break
    if not candidates:
        return [], "no_terms_after_stopwords"
    if token_count > 12 and not closet:
        candidates = {term: priority for term, priority in candidates.items() if priority < 2}
        if not candidates:
            return [], "long_query_no_literal"
    if closet:
        return list(candidates), None
    return sorted(candidates, key=lambda term: candidates[term])[:16], None


def ranking_stop_words(result: dict) -> frozenset:
    """Use the worker's explicit locale, rather than the client's config."""
    lang = result.get("ranking_lang")
    if not isinstance(lang, str) or not lang:
        return frozenset()
    from .i18n import get_stopwords

    return frozenset(get_stopwords(lang))


def rerank_merged_hits(query: str, hits: list, stop_words: frozenset = frozenset()) -> None:
    """Normalize BM25 once across a merged pool when every hit has components.

    Older workers have no components and retain their previous score order.
    Vector similarity is already on the cosine scale, including closet boosts.
    """
    if not hits or not all(isinstance(hit.get("rank_components"), dict) for hit in hits):
        return
    for hit in hits:
        parts = hit["rank_components"]
        for key in ("vector_weight", "bm25_weight", "vector_similarity"):
            value = parts.get(key)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                return
    raw = bm25_scores(query, [hit.get("text") or "" for hit in hits], stop_words=stop_words)
    maximum = max(raw, default=0.0)
    for hit, bm25 in zip(hits, raw):
        parts = hit["rank_components"]
        hit["bm25_score"] = round(bm25, 3)
        parts["bm25"] = round(bm25, 6)
        hit["score"] = round(
            parts["vector_weight"] * parts["vector_similarity"]
            + parts["bm25_weight"] * (bm25 / maximum if maximum else 0.0),
            6,
        )


def tokenize(text, stop_words: frozenset = frozenset()) -> list:
    """Lowercased word tokens of two or more characters.

    ``None`` and empty text give no tokens: Chroma can return ``None`` for a
    drawer without text. ``stop_words`` must already be lowercase.
    """
    if not text:
        return []
    tokens = TOKEN_RE.findall(text.lower())
    if stop_words:
        return [t for t in tokens if t not in stop_words]
    return tokens


def bm25_scores(
    query: str,
    documents: list,
    k1: float = 1.5,
    b: float = 0.75,
    stop_words: frozenset = frozenset(),
) -> list:
    """Okapi-BM25 score of ``query`` against each document, in order.

    IDF is computed over the given documents with the smoothed form
    ``log((N - df + 0.5) / (df + 0.5) + 1)``, which never goes negative, so it
    ranks a candidate set by how discriminative each term is within it.
    """
    n_docs = len(documents)
    query_terms = set(tokenize(query, stop_words))
    if not query_terms or n_docs == 0:
        return [0.0] * n_docs

    tokenized = [tokenize(d, stop_words) for d in documents]
    doc_lens = [len(toks) for toks in tokenized]
    if not any(doc_lens):
        return [0.0] * n_docs
    avgdl = sum(doc_lens) / n_docs or 1.0

    df = {term: 0 for term in query_terms}
    for toks in tokenized:
        for term in set(toks) & query_terms:
            df[term] += 1

    idf = {term: math.log((n_docs - df[term] + 0.5) / (df[term] + 0.5) + 1) for term in query_terms}

    scores = []
    for toks, dl in zip(tokenized, doc_lens):
        if dl == 0:
            scores.append(0.0)
            continue
        tf: dict = {}
        for t in toks:
            if t in query_terms:
                tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for term, freq in tf.items():
            num = freq * (k1 + 1)
            den = freq + k1 * (1 - b + b * dl / avgdl)
            score += idf[term] * num / den
        scores.append(score)
    return scores
