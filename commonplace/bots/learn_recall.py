"""Retrieval of learned items for one turn: part of ``Learned``
(``docs/design/bot-learning-memory.md``, section 7).

Candidates are the asking person's own active items and the shared items of the projects the turn
names (``shared_boards``), nothing else: the store takes the user id from the turn, so another
person's private items are never read, and a shared item is read only while both owner switches
are on and the reader can read that project right now. Ranking is local full-text relevance of the
new message times a decay weight; pinned items and up to three standing preferences are included
without lexical overlap. Whole items only, within a small count and size budget. Nothing is
returned unless the workspace switch is on, the bot's mode is ``ask`` or ``auto`` and the person is
not paused. Nothing here stores or logs the person's words.
"""

import json
import re
from datetime import datetime, timezone
from typing import NamedTuple

from commonplace.bots.store import BotStore, Missing

MAX_ITEMS = 8
MAX_CHARS = 2400  # the quoted items of one turn, whole items only
STANDING = 3  # preference / instruction items taken even without a word in common
STANDING_KINDS = ("preference", "instruction")
MAX_SHARED = 3  # shared items of one turn; they are taken only on a word in common
HALF_LIFE_DAYS = 90
RETIRE_DAYS = 180  # an unpinned item unseen and unused this long is no longer retrieved
MAX_TERMS = 24
_WORD = re.compile(r"\w+")
# Words that carry no topic: without this a question would "match" every item that says "the".
_STOP = frozenset(
    "the and for are but not you your with this that have from was were what when how can will "
    "would could should about into than then them they their there here just also please "
    "een het van voor niet maar met dit dat deze zijn wat hoe kan ook naar bij als dan nog "
    "wel jij jouw mijn ons onze".split()
)


class Recall(NamedTuple):
    items: list  # dicts: id, scope, kind, said, text; best first
    left_out: int = 0  # relevant items that did not fit the budget


NOTHING = Recall([], 0)


def fts_query(text) -> str | None:
    """An OR of quoted words of ``text`` for FTS5 (never raw input in the query), or None."""
    terms, seen = [], set()
    for word in _WORD.findall(text.casefold() if isinstance(text, str) else ""):
        if len(word) < 3 or word in _STOP or word in seen:
            continue
        seen.add(word)
        terms.append(f'"{word}"')
        if len(terms) >= MAX_TERMS:
            break
    return " OR ".join(terms) or None


def _parse(value) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _age_days(row, now) -> float:
    latest = max(row["last_seen_at"], row["last_used_at"] or "")
    return max(0.0, (now - _parse(latest)).total_seconds() / 86400)


def shown(row, reader=None) -> dict:
    """An item as the model reads it: the words, where they sit and when they were said. A shared
    item also says whether the reader or a teammate said it (never a name)."""
    item = {
        "id": row["id"],
        "scope": row["scope"],
        "kind": row["kind"],
        "said": row["source_at"][:10],
        "text": row["text"],
    }
    if row["scope"] == "shared":
        item["by"] = "you" if row["user_id"] == reader else "a teammate"
    return item


def cost(item) -> int:
    return len(json.dumps(item, ensure_ascii=False))


class RecallMixin:
    def recall(
        self, bot_id, user_id, query, *, can_read=None, shared_boards=(), now=None
    ) -> Recall:
        """The items to put in this turn's prompt, or nothing.

        ``can_read(slug)`` says whether the person may read a project right now: an item tagged
        with one is only used while they can (without it, no tagged item is used).
        ``shared_boards`` names the projects whose shared items may be used (the bot's own
        project, or for a bot without one the projects the person reads); none by default.
        ``now`` is the injected clock of tests.
        """
        now = now or datetime.now(timezone.utc)
        with self._bots._tx(write=False) as conn:
            try:
                BotStore._own(conn, bot_id, user_id)
            except Missing:
                return NOTHING  # a shared bot of someone else has no setting of this person's
            if not self._state_view(conn, bot_id, user_id, now)["active"]:
                return NOTHING
            rows = conn.execute(
                "SELECT * FROM bot_learned WHERE user_id=? AND scope='person' AND state='active' "
                "AND replaced_by IS NULL ORDER BY seq DESC",
                (user_id,),
            ).fetchall()
            shared = self._shared_rows(conn, user_id, shared_boards, can_read, rows)
            match = fts_query(query)
            found = {}
            if (rows or shared) and match:
                for hit in conn.execute(
                    "SELECT rowid, bm25(bot_learned_fts) AS score FROM bot_learned_fts "
                    "WHERE bot_learned_fts MATCH ?",
                    (match,),
                ):
                    found[hit["rowid"]] = max(-hit["score"], 1e-6)
        return self._choose(rows, found, can_read, now, shared, user_id)

    def _shared_rows(self, conn, user_id, boards, can_read, own) -> list:
        """Shared items of the projects in ``boards`` that the reader can read and whose switches
        (workspace and project) are on. The reader's own shared copy of an item that is already
        a candidate is left out, so one sentence is not told twice."""
        if can_read is None or not boards:
            return []
        live = [b for b in dict.fromkeys(boards) if can_read(b) and self._shared_on(conn, b)]
        if not live:
            return []
        rows = conn.execute(
            "SELECT * FROM bot_learned WHERE scope='shared' AND state='active' "
            f"AND replaced_by IS NULL AND board IN ({','.join('?' * len(live))}) ORDER BY seq DESC",
            live,
        ).fetchall()
        ids = {row["id"] for row in own}
        return [r for r in rows if not (r["user_id"] == user_id and r["shared_from"] in ids)]

    @staticmethod
    def _choose(rows, found, can_read, now, shared=(), reader=None) -> Recall:
        pinned, standing, matches = [], [], []
        for row in rows:
            if row["board"] and (can_read is None or not can_read(row["board"])):
                continue
            age = _age_days(row, now)
            if not row["pinned"] and age >= RETIRE_DAYS:
                continue
            weight = 1.0 if row["pinned"] else 0.5 ** (age / HALF_LIFE_DAYS)
            score = found.get(row["seq"], 0.0) * weight
            if row["pinned"]:
                pinned.append((weight, score, row))
            elif row["kind"] in STANDING_KINDS:
                standing.append((score, weight, row))
            elif score > 0:
                matches.append((score, weight, row))
        pinned.sort(key=lambda entry: (entry[1], entry[0]), reverse=True)
        standing.sort(key=lambda entry: entry[:2], reverse=True)
        # Standing items beyond the first three still count when a word matched.
        matches += [entry for entry in standing[STANDING:] if entry[0] > 0]
        matches.sort(key=lambda entry: entry[:2], reverse=True)
        ordered = [e[2] for e in pinned] + [e[2] for e in standing[:STANDING]]
        ordered += [e[2] for e in matches]
        found_shared = RecallMixin._shared_matches(shared, found, can_read, now)
        teammates = found_shared[:MAX_SHARED]
        room = MAX_ITEMS - len(teammates)  # a relevant shared item is not starved by the rest
        chosen, used = [], 0
        for row in [*ordered[:room], *teammates, *ordered[room:]]:
            item = shown(row, reader)
            if len(chosen) < MAX_ITEMS and used + cost(item) <= MAX_CHARS:
                chosen.append(item)
                used += cost(item)
        return Recall(chosen, len(ordered) + len(found_shared) - len(chosen))

    @staticmethod
    def _shared_matches(shared, found, can_read, now) -> list:
        """Shared items with a word in common, best first, decayed like the person's own."""
        matches = []
        for row in shared:
            age = _age_days(row, now)
            if age >= RETIRE_DAYS or not row["board"] or not can_read(row["board"]):
                continue
            score = found.get(row["seq"], 0.0) * 0.5 ** (age / HALF_LIFE_DAYS)
            if score > 0:
                matches.append((score, row))
        matches.sort(key=lambda entry: entry[0], reverse=True)
        return [row for _, row in matches]

    def visible(self, user_id, ids, can_read=None) -> list[dict]:
        """The items of ``ids`` this person still has (and may still read), for a thread block:
        their own, and shared ones while both switches are on and they can read the project."""
        ids = [i for i in ids if isinstance(i, str)][:MAX_ITEMS]
        if not ids:
            return []
        with self._bots._tx(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM bot_learned WHERE state='active' "
                "AND ((user_id=? AND scope='person') OR scope='shared') "
                f"AND id IN ({','.join('?' * len(ids))})",
                (user_id, *ids),
            ).fetchall()
            by_id = {
                row["id"]: row
                for row in rows
                if row["scope"] == "person" or self._shared_on(conn, row["board"])
            }
        return [
            shown(by_id[i], user_id)
            for i in ids
            if i in by_id
            and (not by_id[i]["board"] or (can_read is not None and can_read(by_id[i]["board"])))
        ]
