#!/usr/bin/env python3
"""Fact recall eval: do facts work as an index into the full chats?

A seeded, synthetic corpus of chat threads is stored verbatim in a
``ThreadStore``. Facts that point back at exact message ranges are filed into
a ``KnowledgeGraph`` by a *saver*. Questions are then answered from the fact
index alone, and every answer is checked against the chat it cites.

Savers:

* ``oracle``: the corpus' planted facts, filed through ``save_fact`` with
  their exact message ranges, the way an agent calls ``fact_save`` mid-chat.
  This measures the index (merge, supersede, wings, sensitivity, sources),
  not extraction. Extraction (step 5) plugs in as another saver and adds
  ``extraction_recall``.

Metrics (see :func:`run`):

* ``recall_at_1`` / ``recall_at_5``: the expected value is the first / among
  the first five values the index returns for (subject, predicate[, as_of]).
* ``stale_rate``: a replaced value still came back as current (first, or
  open beside the new one).
* ``source_hit_rate``: the answering fact's source ranges open chat messages
  that contain the answer verbatim.
* ``old_thread_recall``: recall@1 over questions whose answer lives only in
  the oldest thread, behind every later thread.
* ``private_leaks``: facts a restricted reader saw that it may not read.
  Must be 0.
* ``merge_rate``: restated facts that became one fact with every source.

Run: ``python benchmarks/fact_recall_bench.py [--threads N] [--seed S] [--json]``
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

if __name__ == "__main__":  # allow running from a checkout without installing
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commonplace.handoff.models import ThreadMessage
from commonplace.handoff.thread_store import ThreadStore
from mempalace.knowledge_graph import KnowledgeGraph

WORKSPACE = "eval"
OWNER = {"wings": None, "sensitivities": None}
# A reader that sees the shared and project wings, but no private wing and
# no private or secret facts.
SCOPED = {
    "wings": ["shared", "project_kestrel", "project_heron"],
    "sensitivities": ["public", "internal"],
}


@dataclass
class Planted:
    """A fact a thread states, at the message that states it."""

    subject: str
    predicate: str
    object: str
    seq: int
    wing: str = "shared"
    sensitivity: str = "internal"
    valid_from: Optional[str] = None
    single_valued: bool = False


@dataclass
class Thread:
    key: str
    day: str
    messages: list
    facts: list = field(default_factory=list)


@dataclass
class Question:
    id: str
    subject: str
    predicate: str
    answer: Optional[str]  # None: the reader must get nothing
    reader: dict
    as_of: Optional[str] = None
    stale: tuple = ()
    old_thread: bool = False
    hidden: tuple = ()  # values this reader must never see


@dataclass
class Corpus:
    threads: list
    questions: list
    restated: list  # (subject, predicate, object, wing, expected source count)


def _say(seq, role, text, day):
    raw = json.dumps({"role": role, "content": text}, ensure_ascii=False)
    return ThreadMessage(
        seq=seq, role=role, kind="message", text=text, raw=raw, at=f"{day}T09:00:00Z"
    )


def _thread(key, day, lines, facts=()):
    messages = [_say(i, role, text, day) for i, (role, text) in enumerate(lines)]
    return Thread(key=key, day=day, messages=messages, facts=list(facts))


_FILLER_TOPICS = (
    ("Heron", "the flaky upload test", "retry it with a longer timeout"),
    ("Osprey", "the dashboard colours", "keep the dark theme"),
    ("Heron", "a slow migration", "split it into batches"),
    ("Osprey", "the release notes", "list the breaking changes first"),
    ("Wren", "the onboarding doc", "cut the setup section in half"),
)


def build_corpus(threads: int = 40, seed: int = 7) -> Corpus:
    """A deterministic corpus. ``threads`` >= 36 keeps every planted case."""
    if threads < 36:
        raise ValueError("the corpus needs at least 36 threads")
    rng = random.Random(seed)
    days = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(threads)]
    out = []

    # Thread 0, the oldest: the only place the Kestrel deploy port is said.
    out.append(
        _thread(
            "kestrel-setup",
            days[0],
            [
                ("user", "Setting up Kestrel today."),
                ("assistant", "Which port should the deploy use?"),
                ("user", "For Kestrel the deploy port is 8443, don't forget it."),
                ("assistant", "Noted: Kestrel deploys on 8443."),
            ],
            [Planted("Kestrel", "deploy_port", "8443", 2, wing="project_kestrel")],
        )
    )

    planted = {
        # Value changes: npm, then pnpm much later.
        3: (
            [("user", "Jeffrey uses npm for every repo."), ("assistant", "Okay, npm it is.")],
            [
                Planted(
                    "Jeffrey",
                    "uses_package_manager",
                    "npm",
                    0,
                    valid_from=days[3],
                    single_valued=True,
                )
            ],
        ),
        25: (
            [
                ("user", "We switched: Jeffrey uses pnpm now, npm is gone."),
                ("assistant", "pnpm from now on."),
            ],
            [
                Planted(
                    "Jeffrey",
                    "uses_package_manager",
                    "pnpm",
                    0,
                    valid_from=days[25],
                    single_valued=True,
                )
            ],
        ),
        # Employer, with older news filed after the newer value.
        10: (
            [
                ("user", "Jeffrey works at Initech since this week."),
                ("assistant", "Congrats on Initech."),
            ],
            [Planted("Jeffrey", "works_at", "Initech", 0, valid_from=days[10], single_valued=True)],
        ),
        30: (
            [("assistant", "Before that?"), ("user", "Back in 2024 Jeffrey worked at Acme.")],
            [
                Planted(
                    "Jeffrey", "works_at", "Acme", 1, valid_from="2024-03-01", single_valued=True
                )
            ],
        ),
        # The same fact restated in three threads.
        5: (
            [("user", "Kestrel's default branch is main."), ("assistant", "main, got it.")],
            [Planted("Kestrel", "default_branch", "main", 0, wing="project_kestrel")],
        ),
        12: (
            [
                ("assistant", "Which branch for the hotfix?"),
                ("user", "Kestrel default branch is still main."),
            ],
            [Planted("Kestrel", "default_branch", "main", 1, wing="project_kestrel")],
        ),
        33: (
            [("user", "Reminder: the default branch for Kestrel is main.")],
            [Planted("Kestrel", "default_branch", "main", 0, wing="project_kestrel")],
        ),
        # Private: a private wing, and a private fact in a shared wing.
        7: (
            [
                ("user", "Private note: Jeffrey is allergic to penicillin."),
                ("assistant", "I'll keep that private."),
            ],
            [
                Planted(
                    "Jeffrey", "allergic_to", "penicillin", 0, wing="private", sensitivity="private"
                )
            ],
        ),
        18: (
            [
                ("user", "Between us: Heron's budget is 40k."),
                ("assistant", "Understood, kept internal to you."),
            ],
            [Planted("Heron", "budget", "40k", 0, wing="project_heron", sensitivity="private")],
        ),
        # A distractor: another project's port, said far more recently.
        35: (
            [("user", "Heron deploys on port 9000."), ("assistant", "Heron: 9000.")],
            [Planted("Heron", "deploy_port", "9000", 0, wing="project_heron")],
        ),
    }

    for i in range(1, threads):
        if i in planted:
            lines, facts = planted[i]
            # Every planted thread also has unrelated chatter around it.
            name, topic, advice = _FILLER_TOPICS[i % len(_FILLER_TOPICS)]
            lines = [("user", f"Quick one about {name}: {topic}.")] + list(lines)
            facts = [Planted(**{**f.__dict__, "seq": f.seq + 1}) for f in facts]
            out.append(_thread(f"t{i:02d}", days[i], lines, facts))
            continue
        name, topic, advice = rng.choice(_FILLER_TOPICS)
        out.append(
            _thread(
                f"t{i:02d}",
                days[i],
                [
                    ("user", f"About {name}: {topic}."),
                    ("assistant", f"I would {advice}."),
                    ("user", rng.choice(("Sounds good.", "Let's do that.", "Fine by me."))),
                ],
            )
        )

    questions = [
        Question("old-thread-port", "Kestrel", "deploy_port", "8443", OWNER, old_thread=True),
        Question(
            "old-thread-port-scoped", "Kestrel", "deploy_port", "8443", SCOPED, old_thread=True
        ),
        Question("distractor-port", "Heron", "deploy_port", "9000", SCOPED),
        Question("changed-value", "Jeffrey", "uses_package_manager", "pnpm", OWNER, stale=("npm",)),
        Question(
            "changed-value-then",
            "Jeffrey",
            "uses_package_manager",
            "npm",
            OWNER,
            as_of=days[10],
        ),
        Question("employer-now", "Jeffrey", "works_at", "Initech", OWNER, stale=("Acme",)),
        Question("employer-history", "Jeffrey", "works_at", "Acme", OWNER, as_of="2024-06-01"),
        Question("restated", "Kestrel", "default_branch", "main", SCOPED),
        Question("private-owner", "Jeffrey", "allergic_to", "penicillin", OWNER),
        Question("private-scoped", "Jeffrey", "allergic_to", None, SCOPED, hidden=("penicillin",)),
        Question("private-in-shared-scoped", "Heron", "budget", None, SCOPED, hidden=("40k",)),
    ]
    restated = [("Kestrel", "default_branch", "main", "project_kestrel", 3)]
    return Corpus(out, questions, restated)


# ── savers ─────────────────────────────────────────────────────────────────


def oracle_saver(kg: KnowledgeGraph, stored_id: str, thread: Thread) -> int:
    """File each planted fact with its exact message range. Returns facts filed."""
    for fact in thread.facts:
        kg.save_fact(
            fact.subject,
            fact.predicate,
            fact.object,
            valid_from=fact.valid_from,
            source_thread=stored_id,
            source_seq_start=fact.seq,
            source_seq_end=fact.seq,
            wing=fact.wing,
            sensitivity=fact.sensitivity,
            single_valued=fact.single_valued,
        )
    return len(thread.facts)


SAVERS = {"oracle": oracle_saver}


# ── recall ─────────────────────────────────────────────────────────────────


def _start_key(fact) -> str:
    return fact.get("valid_from") or ""


def recall(kg: KnowledgeGraph, question: Question, k: int = 5) -> list:
    """The index's answer: up to ``k`` facts, best first.

    Open, unreplaced facts first (newest start first), then the rest. With
    ``as_of`` only facts that held at that instant.
    """
    facts = kg.query_entity(
        question.subject,
        as_of=question.as_of,
        direction="outgoing",
        wings=question.reader["wings"],
        sensitivities=question.reader["sensitivities"],
    )
    facts = [f for f in facts if f["predicate"] == question.predicate]
    if question.as_of is None:
        facts.sort(key=_start_key, reverse=True)
        facts.sort(key=lambda f: not (f["current"] and f["status"] == "active"))
    else:
        facts.sort(key=_start_key, reverse=True)
    return facts[:k]


def _cites_answer(store: ThreadStore, kg: KnowledgeGraph, fact: dict, answer: str) -> bool:
    """Does any source range of ``fact`` open messages containing ``answer`` verbatim?"""
    for source in kg.sources([fact["id"]])[fact["id"]]:
        thread = source.get("source_thread")
        start = source.get("source_seq_start")
        if thread is None or start is None:
            continue
        end = source.get("source_seq_end", start)
        if any(answer in m.text for m in store.messages(thread, start, end)):
            return True
    return False


def run(
    threads: int = 40,
    seed: int = 7,
    saver: Callable = oracle_saver,
    workdir: Optional[str] = None,
) -> dict:
    """Build the corpus, file facts with ``saver``, answer every question."""
    corpus = build_corpus(threads, seed)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        store = ThreadStore(str(Path(tmp) / "threads.sqlite3"))
        kg = KnowledgeGraph(db_path=str(Path(tmp) / "kg.sqlite3"))
        try:
            ids = {}
            for thread in corpus.threads:
                stored = store.store(WORKSPACE, thread.key, thread.messages, actor="eval")
                ids[thread.key] = stored.id
                saver(kg, stored.id, thread)
            return _score(kg, store, corpus, ids)
        finally:
            kg.close()
            store.close()


def _score(kg, store, corpus, ids) -> dict:
    per_question = []
    hits1 = hits5 = stale = cited = answerable = 0
    old_total = old_hits = leaks = 0
    for q in corpus.questions:
        found = recall(kg, q)
        values = [f["object"] for f in found]
        leaked = [v for v in values if v in q.hidden]
        leaks += len(leaked)
        row = {"id": q.id, "expected": q.answer, "got": values[:5], "leaked": leaked}
        if q.answer is None:
            row["ok"] = not values
        else:
            answerable += 1
            top = values[0] if values else None
            hit1 = top == q.answer
            hits1 += hit1
            hits5 += q.answer in values
            stale += any(
                f["object"] in q.stale and f["current"] and f["status"] == "active"
                for f in found
            )
            cite = bool(found) and hit1 and _cites_answer(store, kg, found[0], q.answer)
            cited += cite
            if q.old_thread:
                old_total += 1
                old_hits += hit1 and cite
            row.update(ok=hit1 and cite, cited=cite)
        per_question.append(row)

    merged = 0
    for subject, predicate, obj, wing, count in corpus.restated:
        facts = [
            f
            for f in kg.query_entity(subject, direction="outgoing", wings=[wing])
            if f["predicate"] == predicate and f["object"] == obj
        ]
        merged += len(facts) == 1 and len(kg.sources([facts[0]["id"]])[facts[0]["id"]]) == count

    return {
        "threads": len(corpus.threads),
        "questions": len(corpus.questions),
        "recall_at_1": hits1 / answerable,
        "recall_at_5": hits5 / answerable,
        "stale_rate": stale / answerable,
        "source_hit_rate": cited / answerable,
        "old_thread_recall": old_hits / old_total,
        "private_leaks": leaks,
        "merge_rate": merged / len(corpus.restated),
        "per_question": per_question,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--threads", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--saver", choices=sorted(SAVERS), default="oracle")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)
    report = run(args.threads, args.seed, SAVERS[args.saver])
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for key in (
            "recall_at_1",
            "recall_at_5",
            "stale_rate",
            "source_hit_rate",
            "old_thread_recall",
            "private_leaks",
            "merge_rate",
        ):
            print(f"{key:18} {report[key]}")
        for row in report["per_question"]:
            if not row["ok"]:
                print(f"MISS {row['id']}: expected {row['expected']!r}, got {row['got']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
