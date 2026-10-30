"""Deterministic speech from the already scoped Needs you queue."""

from .display import NEEDS

_COUNTS = (
    "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen "
    "Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen Twenty"
).split()


def digest(groups):
    """Reuse queue membership and card needs; never rewrite a card's words."""
    cards = {
        c["number"]: c for bucket in groups.values() if isinstance(bucket, list) for c in bucket
    }
    ordered = sorted(cards.values(), key=lambda c: (NEEDS.index(c["need"]), c["number"]))
    count = len(ordered)
    items = [{k: c[k] for k in ("number", "title", "need", "board")} for c in ordered[:5]]
    remaining = count - len(items)
    if not count:
        text = "Nothing needs you today."
    else:
        total = _COUNTS[count] if count < len(_COUNTS) else str(count)
        lines = ["One thing needs you." if count == 1 else f"{total} things need you."]
        lines.extend(f"{item['title']} on {item['board']}." for item in items)
        if remaining:
            lines.append(f"And {remaining} more.")
        text = "\n".join(lines)
    return {"text": text, "count": count, "items": items, "remaining": remaining}
