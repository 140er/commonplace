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

    return frozenset(get_stopwords("en"))


def select_lexical_terms(
    query: str, stop_words: frozenset = frozenset()
) -> tuple[list[str], str | None]:
    """Bound candidate retrieval to eight discriminative terms.

    IDs, paths and mixed-case names precede long words, then other words.
    Queries longer than twelve tokens need one of those true literals;
    ordinary prose retains vector candidates without a lexical index query.
    Existing FTS indexes have no cheaply readable vocabulary frequencies, so
    length is the rarity proxy; computing frequencies would scan the index.
    This affects candidate gathering only, never the stored text or BM25 rank.
    """
    ignored = _query_stop_words() | stop_words
    candidates = {}
    for part in _QUERY_PART_RE.findall(query or ""):
        strong = (
            any(c.isdigit() for c in part)
            or any(c.isupper() for c in part[1:])
            or any(c in part for c in "_-/.:")
        )
        for word in TOKEN_RE.findall(part):
            term = word.lower()
            if term in ignored:
                continue
            priority = 0 if strong else (1 if len(term) >= 8 else 2)
            candidates[term] = min(priority, candidates.get(term, priority))
    if len(tokenize(query)) > 12 and not any(priority == 0 for priority in candidates.values()):
        return [], "long_query_no_literal"
    terms = sorted(candidates, key=lambda term: (candidates[term], -len(term), term))
    return terms[:8], None


def rerank_merged_hits(query: str, hits: list) -> None:
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
    raw = bm25_scores(query, [hit.get("text") or "" for hit in hits])
    maximum = max(raw, default=0.0)
    for hit, bm25 in zip(hits, raw):
        parts = hit["rank_components"]
        hit["bm25_score"] = round(bm25, 3)
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
