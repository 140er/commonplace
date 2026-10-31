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
    return frozenset(get_stopwords("en")) | frozenset(
        "a an the and or but if then than as at by for from in into of on onto to with "
        "without about above after again against all am are be because been before being "
        "below between both can could did do does doing down during each few further had "
        "has have having he her here hers herself him himself his how i is it its itself "
        "just me more most my myself no nor not now off once only other our ours ourselves "
        "out over own same she should so some such that their theirs them themselves these "
        "they this those through too under until up very was we were what when where which "
        "while who whom why will would you your yours yourself yourselves".split()
    )


class LexicalTerm(str):
    """A capped index term carrying internal prefix semantics."""

    def __new__(cls, text):
        term = super().__new__(cls, text[:64])
        term.prefix = len(text) > 64
        return term


class IndexWordNormalizer:
    """SQLite's unicode61 words, shared by selection, tf and document length.

    A private tokenizer contains no corpus or cross-search cache. The ASCII
    fast path is equivalent to unicode61, including single-character words.
    """

    def __init__(self):
        self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.conn is not None:
            self.conn.close()

    def native(self, text):
        import sqlite3

        if self.conn is None:
            self.conn = sqlite3.connect(":memory:")
            try:
                self.conn.execute("CREATE VIRTUAL TABLE words USING fts3tokenize(unicode61)")
            except sqlite3.OperationalError:
                self.conn.execute(
                    "CREATE VIRTUAL TABLE text_words USING fts5(text, tokenize=unicode61)"
                )
                self.conn.execute(
                    "CREATE VIRTUAL TABLE words USING fts5vocab(text_words, instance)"
                )
                self.fts5 = True
        if getattr(self, "fts5", False):
            self.conn.execute("DELETE FROM text_words")
            self.conn.execute("INSERT INTO text_words(text) VALUES (?)", (text,))
            return [row[0] for row in self.conn.execute("SELECT term FROM words ORDER BY offset")]
        return [
            row[0]
            for row in self.conn.execute(
                "SELECT token FROM words WHERE input=? ORDER BY position", (text,)
            )
        ]

    def __call__(self, text):
        text = (text or "").encode("utf-8", "replace").decode("utf-8")
        if text.isascii() and "_" not in text and "\x00" not in text:
            return re.findall(r"[a-z0-9]+", text.lower())
        return self.native(text)


def lexical_query_terms(
    query, stop_words=frozenset(), *, min_length=2, trigram=False, closet=False
):
    """RFC 009 shape-only selection; literals remain a single index phrase."""
    ignored = stop_words if closet else _query_stop_words() | stop_words
    query = query or ""
    # Quoted spans take precedence over bare words, retaining punctuation.
    parts = re.finditer(r'"([^"\n]+)"|“([^”\n]+)”|[/\w]+(?:[.\-:/][/\w]+)*', query)
    candidates = {}
    start, last_end, token_count, seen_tokens = True, 0, 0, 0
    with IndexWordNormalizer() as normalize:
        for match in parts:
            if re.search(r"[.!?](?:\s|$)", query[last_end : match.start()]):
                start = True
            part = match.group(1) or match.group(2) or match.group()
            quoted = match.group(1) is not None or match.group(2) is not None
            words = TOKEN_RE.findall(part.lower()) if trigram else normalize(part)
            remaining = 256 - seen_tokens
            if remaining <= 0:
                break
            seen_tokens += len(words)
            words = words[:remaining]
            token_count += sum(word not in ignored for word in words)
            literal = (
                quoted
                or any(c in part for c in "_/.:")
                or any(c.isdigit() for c in part)
                or (any(c.isupper() for c in part[1:]) and any(c.islower() for c in part))
            )
            name = part[0].isupper() and part[1:].islower() and not start
            # Keep the original literal: FTS5 applies its own tokenizer and
            # treats a quoted bareword containing separators as a phrase.
            terms = (
                [LexicalTerm(part.lower())] if literal else [LexicalTerm(word) for word in words]
            )
            for term in terms:
                if term in ignored or len(term) < min_length:
                    continue
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


def index_bm25_scores(terms, documents, matched_terms=None, *, trigram=False):
    """Scope-local BM25 with phrase tf and an index-confirmed match floor.

    ``matched_terms`` parallels documents. It records only terms whose bounded
    index query returned that document; a tokenizer mismatch cannot erase it.
    """
    from collections import Counter

    if not documents or not terms:
        return [0.0] * len(documents)
    with IndexWordNormalizer() as native_normalize:
        normalize = tokenize if trigram else native_normalize
        phrases = [tuple(normalize(term)) for term in terms]
        tokenized = [normalize(text) for text in documents]
    lengths = [len(tokens) for tokens in tokenized]
    avgdl = sum(lengths) / len(lengths) or 1.0
    frequencies = []
    for i, tokens in enumerate(tokenized):
        counts = Counter(tokens)
        evidence = matched_terms[i] if matched_terms is not None else ()
        row = []
        for term, phrase in zip(terms, phrases):
            size = len(phrase)
            if trigram and any(c in term for c in "_/.: "):
                # Trigram literals are whole substrings, including separators.
                # Keep the historical word rules for ordinary word matches.
                freq = (documents[i] or "").lower().count(term)
            elif getattr(term, "prefix", False) and size:
                freq = sum(
                    tuple(tokens[j : j + size - 1]) == phrase[:-1]
                    and tokens[j + size - 1].startswith(phrase[-1])
                    for j in range(len(tokens) - size + 1)
                )
            elif size == 1:
                freq = counts[phrase[0]]
            elif size:
                freq = sum(
                    tuple(tokens[j : j + size]) == phrase for j in range(len(tokens) - size + 1)
                )
            else:
                freq = 0
            row.append(max(freq, 1 if term in evidence else 0))
        frequencies.append(row)
    n = len(documents)
    idf = [
        math.log((n - df + 0.5) / (df + 0.5) + 1)
        for df in (sum(row[j] > 0 for row in frequencies) for j in range(len(terms)))
    ]
    return [
        sum(
            weight * freq * 2.5 / (freq + 1.5 * (0.25 + 0.75 * dl / avgdl))
            for weight, freq in zip(idf, row)
            if freq
        )
        for row, dl in zip(frequencies, lengths)
    ]


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
