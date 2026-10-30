"""Fact-first ranking (RFC 009). Drawers never share this ranking or limit."""

import heapq
import logging
import math
from collections import defaultdict
from datetime import datetime, time, timezone

from .. import fact_provenance
from ..fact_render import ordered_sources
from ..i18n import get_stopwords
from ..lexical import tokenize
from ..query_sanitizer import sanitize_query

# All scoring constants live here. Ended facts retain a visible history score;
# current facts precede history with the same subject and predicate.
STATUS_WEIGHTS = {"approved": 1.0, "proposal": 0.85, "unknown": 0.75}
ENDED_WEIGHT = 0.25
DECAY_HALF_LIFE_DAYS = 180
DECAY_FLOOR = 0.5
FACT_CANDIDATE_LIMIT = 50
FACT_COMMON_TERM_FRACTION = 0.5
# English question/predicate glue is always ignored. Explicit locales add their
# own stop words through the existing searcher resolver.
FACT_STOP_WORDS = frozenset(get_stopwords("en")) | frozenset(
    "a an and are as at be been being but by can could did do does for from "
    "had has have how if in into is it its me my of on or our she should so "
    "than that the their them then there these they this those to was we were "
    "what when where which who why will with would you your".split()
)
logger = logging.getLogger(__name__)


def _instant(value, *, end=False):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10 and end:
            stamp = datetime.combine(stamp.date(), time(23, 59, 59))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    except (ValueError, TypeError, AttributeError):
        return None


def _words(text, stop_words=FACT_STOP_WORDS):
    return set(tokenize(text.replace("_", " "), stop_words))


def search_facts(kg, query, *, wings=None, sensitivities=None, limit=5, as_of=None, now=None):
    """Readable, whole-word fact matches, with original source and authority.

    KG visibility and temporal predicates run in SQL before ranking. No fact
    score can consume a drawer slot. A source's latest observation controls
    decay; approved facts never decay, including approved history.
    """
    from . import _resolve_stop_words

    stop_words = FACT_STOP_WORDS | _resolve_stop_words(None)
    words = _words(sanitize_query(query)["clean_query"], stop_words)
    if not words:
        return []
    now = now or datetime.now(timezone.utc)
    at = _instant(as_of) if as_of else now
    facts = []
    rows = kg.search_fact_rows(
        words,
        candidate_limit=FACT_CANDIDATE_LIMIT,
        common_term_fraction=FACT_COMMON_TERM_FRACTION,
        wings=wings,
        sensitivities=sensitivities,
        as_of=as_of,
    )
    if not rows:
        return []
    total = rows[0]["documents"]
    frequencies = rows[0]["frequencies"]
    idf = {
        word: math.log(
            1 + (total - frequencies.get(word, 0) + 0.5) / (frequencies.get(word, 0) + 0.5)
        )
        for word in words
    }
    total_weight = sum(idf.values())
    for row in rows:
        text = " ".join((row["subject_name"], row["predicate"], row["object_name"]))
        lexical = sum(idf[word] for word in words & _words(text, stop_words)) / total_weight
        if lexical == 0:  # FTS token boundaries can be broader than our tokenizer.
            continue
        start, end = _instant(row["valid_from"]), _instant(row["valid_to"], end=True)
        current = (
            (start is None or start <= at)
            and (end is None or end > at)
            and not row["superseded_by"]
            and row["status"] == "active"
        )
        approval = row["approval_status"] or "unknown"
        observed = [s for source in row["sources"] if (s := _instant(source.get("added_at")))]
        observed = max(observed) if observed else start or _instant(row["extracted_at"]) or now
        age_days = max(0, (now - observed).total_seconds() / 86400)
        decay = (
            1.0
            if approval == "approved"
            else max(DECAY_FLOOR, 2 ** (-age_days / DECAY_HALF_LIFE_DAYS))
        )
        weight = (
            STATUS_WEIGHTS.get(approval, STATUS_WEIGHTS["unknown"]) if current else ENDED_WEIGHT
        )
        authority = fact_provenance.fields(row)
        if row["superseded_by"]:
            authority["fact_status"] = "superseded"
        elif not current and authority["fact_status"] in STATUS_WEIGHTS:
            authority["fact_status"] = "historical" if end and end <= at else "future"
        sources = ordered_sources(row["sources"])
        for source in sources:
            source["source_link"] = fact_provenance.source_link(
                source.get("source_thread"), source.get("origin_link")
            )
        facts.append(
            {
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "predicate",
                        "valid_from",
                        "valid_to",
                        "status",
                        "superseded_by",
                        "wing",
                        "sensitivity",
                        "source_thread",
                        "source_seq_start",
                        "source_seq_end",
                        "source_drawer_id",
                        "source_file",
                        "seen_count",
                    )
                },
                **authority,
                "subject": row["subject_name"],
                "object": row["object_name"],
                "source_link": fact_provenance.source_link(
                    row["source_thread"], row["origin_link"]
                ),
                "sources": sources,
                "current": current,
                "lexical_relevance": lexical,
                "status_weight": weight,
                "decay": decay,
                "score": lexical * weight * decay,
                "last_observed_at": observed.isoformat(),
            }
        )
    # Merge per-key queues by score. History waits for its own current values,
    # without promoting unrelated, weak current matches above relevant history.
    groups = defaultdict(list)
    for fact in facts:
        groups[(fact["subject"], fact["predicate"])].append(fact)
    queues = list(groups.values())
    heap = []
    for index, group in enumerate(queues):
        group.sort(key=lambda f: (not f["current"], -f["score"], f["id"]))
        heap.append((-group[0]["score"], group[0]["id"], index, 0))
    heapq.heapify(heap)
    ranked = []
    while heap and (limit is None or len(ranked) < max(1, limit)):
        _, _, group_index, offset = heapq.heappop(heap)
        group = queues[group_index]
        ranked.append(group[offset])
        if offset + 1 < len(group):
            successor = group[offset + 1]
            heapq.heappush(heap, (-successor["score"], successor["id"], group_index, offset + 1))
    return ranked


def palace_facts(query, palace_path, *, wing=None, limit=5):
    """A local owner's read; missing graphs never create a file."""
    try:
        from ..knowledge_graph import open_graph
        from ..paths import knowledge_graph_path

        with open_graph(knowledge_graph_path(palace_path), read=True) as kg:
            return {"facts": search_facts(kg, query, wings=[wing] if wing else None, limit=limit)}
    except Exception:
        logger.warning("Fact index unavailable; preserving drawer search", exc_info=True)
        return {"facts": [], "facts_error": "unavailable"}
