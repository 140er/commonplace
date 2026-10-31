"""Text presentation of fact search results, without importing the search engine."""

import logging


def ordered_sources(sources):
    """Stable observation order independent of SQLite aggregation or remote order."""
    return sorted(
        sources,
        key=lambda s: (
            s.get("added_at") or "",
            s.get("source_thread") or "",
            s.get("source_seq_start") if s.get("source_seq_start") is not None else -1,
            s.get("source_seq_end") if s.get("source_seq_end") is not None else -1,
            s.get("source_drawer_id") or "",
            s.get("source_file") or "",
        ),
    )


def format_facts(facts):
    """CLI presentation, retaining source ranges, approval and successor IDs."""
    try:
        return _format_facts(facts)
    except Exception:
        logging.getLogger(__name__).warning("Fact rendering unavailable", exc_info=True)
        return "\n  facts_error: unavailable\n"


def _format_facts(facts):
    if not facts:
        return ""
    lines = ["\n  Facts\n"]
    for fact in facts:
        lines.append(
            f"  {fact.get('subject', '?')} | {fact.get('predicate', '?')} | {fact.get('object', '?')}"
        )
        lines.append(
            f"    {fact.get('fact_status', 'unknown')}; origin={fact.get('origin_kind', 'unknown')}:{fact.get('origin_id', 'unknown')} "
            f"{fact.get('origin_link') or ''}; approval={fact.get('approval_status', 'unknown')} "
            f"{fact.get('approved_by') or ''} {fact.get('approval_link') or ''}"
        )
        for source in ordered_sources(fact.get("sources") or [fact]):
            link = (
                source.get("source_link")
                or source.get("source_thread")
                or source.get("source_drawer_id")
            )
            if link:
                span = (
                    f" [{source.get('source_seq_start')}:{source.get('source_seq_end')}]"
                    if source.get("source_seq_start") is not None
                    else ""
                )
                lines.append(f"    Source: {link}{span}")
        if fact.get("superseded_by"):
            lines.append(f"    Superseded by: {fact.get('superseded_by')}")
    return "\n".join(lines) + "\n"
