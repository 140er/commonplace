"""Write the short handoff from the thread's own words. Owner: part T06.

No model, no paraphrase: goal is the first user message, status the last
assistant message, open points the last todo list (Claude ``TodoWrite`` /
Codex ``update_plan`` items not completed), decisions the user's later
messages; branch and PR come from metadata, else the last ``git checkout -b``
/ ``git switch -c`` / PR URL seen in tool calls or results. Long quotes are
cut at a word boundary with ``…`` and a ``thread_get`` range to read the
rest, so nothing is reworded.
"""

from __future__ import annotations

from .models import HandoffDoc, ThreadMeta

MAX_QUOTE_CHARS = 1_200
MAX_DECISIONS = 8


def build_doc(
    messages: list,
    meta: ThreadMeta,
    *,
    note: str = "",
    stored_thread_id: str = "",
    handoff_id: str = "",
    chain: tuple = (),
) -> HandoffDoc:
    raise NotImplementedError


def render_markdown(doc: HandoffDoc) -> str:
    """The handoff as Markdown: headings Goal, Status, Open points,
    Decisions, Branch/PR, Note, Memory (the pointer and how to recall)."""
    raise NotImplementedError


def successor_prompt(doc: HandoffDoc) -> str:
    """The successor's first message: the markdown plus how to read the old
    thread (``thread_recall(thread_id, query)``, ``thread_get(thread_id,
    start, end)``) and to continue the work, not restart it."""
    raise NotImplementedError
