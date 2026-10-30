"""Text presentation of fact search results, without importing the search engine."""


def format_facts(facts):
    """CLI presentation, retaining source ranges, approval and successor IDs."""
    if not facts:
        return ""
    lines = ["\n  Facts\n"]
    for fact in facts:
        lines.append(f"  {fact['subject']} | {fact['predicate']} | {fact['object']}")
        lines.append(
            f"    {fact['fact_status']}; origin={fact['origin_kind']}:{fact['origin_id']} "
            f"{fact.get('origin_link') or ''}; approval={fact['approval_status']} "
            f"{fact.get('approved_by') or ''} {fact.get('approval_link') or ''}"
        )
        for source in fact.get("sources") or [fact]:
            link = (
                source.get("source_link")
                or source.get("source_thread")
                or source.get("source_drawer_id")
            )
            if link:
                span = (
                    f" [{source['source_seq_start']}:{source.get('source_seq_end')}]"
                    if source.get("source_seq_start") is not None
                    else ""
                )
                lines.append(f"    Source: {link}{span}")
        if fact.get("superseded_by"):
            lines.append(f"    Superseded by: {fact['superseded_by']}")
    return "\n".join(lines) + "\n"
