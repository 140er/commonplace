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
* ``stale_rate``: a replaced value was left without an end date or returned
  by an as-of query after it ended, regardless of its status label.
* ``source_hit_rate``: every source of the answering fact matches its exact
  planted thread and message range and contains the answer as whole words.
* ``old_thread_recall``: recall@1 over questions whose answer lives only in
  the oldest thread, behind every later thread.
* ``private_leaks``: facts a restricted reader saw that it may not read.
  Must be 0.
* ``merge_rate``: restated facts that became one fact with every source.
* ``extraction_recall``: planted occurrences present with the right identity,
  wing and exact source (the oracle's filing coverage, until step 5).
* ``temporal_precision``: as-of answers contain only the value valid then.
* ``denied_ok``: denied questions return no facts at all.

Run: ``python benchmarks/fact_recall_bench.py [--threads N] [--seed S] [--json]``
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
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
BARS = {
    "recall_at_1": 1.0,
    "recall_at_5": 1.0,
    "stale_rate": 0.0,
    "source_hit_rate": 1.0,
    "old_thread_recall": 1.0,
    "private_leaks": 0,
    "merge_rate": 1.0,
    "extraction_recall": 1.0,
    "temporal_precision": 1.0,
    "denied_ok": 1.0,
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
    seq_end: Optional[int] = None

    @property
    def end(self):
        return self.seq if self.seq_end is None else self.seq_end


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
    for fact in facts:
        if fact.valid_from is None:
            fact.valid_from = day
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
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(threads)]
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
                ("user", "We switched: Jeffrey uses pnpm now."),
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
        # Restating a private fact publicly must not lower its sensitivity.
        19: (
            [("user", "Heron's budget is still 40k.")],
            [Planted("Heron", "budget", "40k", 0, wing="project_heron", sensitivity="public")],
        ),
        # A newer near-miss must not displace the oldest thread's deploy port.
        26: (
            [("user", "Kestrel's staging deploy port is 8444.")],
            [Planted("Kestrel", "staging_deploy_port", "8444", 0, wing="project_kestrel")],
        ),
        # Same lookup key, newer, but outside either old-thread reader's wings.
        27: (
            [("user", "Private Kestrel instance: its deploy port is 9443.")],
            [Planted("Kestrel", "deploy_port", "9443", 0, wing="private")],
        ),
        # Same value in another wing must stay a separate fact.
        32: (
            [("user", "In the Heron wing, Kestrel's default branch is main too.")],
            [Planted("Kestrel", "default_branch", "main", 0, wing="project_heron")],
        ),
        # Attribution needs both messages, even though only one has the value.
        34: (
            [
                ("user", "For Kestrel, record the backup contact."),
                ("user", "Ada Lovelace is the person."),
            ],
            [
                Planted(
                    "Kestrel",
                    "backup_contact",
                    "Ada Lovelace",
                    0,
                    seq_end=1,
                    wing="project_kestrel",
                )
            ],
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
            facts = [
                Planted(**{**f.__dict__, "seq": f.seq + 1, "seq_end": f.end + 1}) for f in facts
            ]
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
        Question(
            "old-thread-port",
            "Kestrel",
            "deploy_port",
            "8443",
            {"wings": ["project_kestrel"], "sensitivities": None},
            old_thread=True,
            hidden=("9443",),
        ),
        Question(
            "old-thread-port-scoped",
            "Kestrel",
            "deploy_port",
            "8443",
            SCOPED,
            old_thread=True,
            hidden=("9443",),
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
        Question(
            "changed-value-after",
            "Jeffrey",
            "uses_package_manager",
            "pnpm",
            OWNER,
            as_of=days[30],
            stale=("npm",),
        ),
        Question(
            "changed-value-boundary",
            "Jeffrey",
            "uses_package_manager",
            "pnpm",
            OWNER,
            as_of=days[25],
            stale=("npm",),
        ),
        Question("employer-now", "Jeffrey", "works_at", "Initech", OWNER, stale=("Acme",)),
        Question("employer-history", "Jeffrey", "works_at", "Acme", OWNER, as_of="2024-06-01"),
        Question(
            "restated",
            "Kestrel",
            "default_branch",
            "main",
            {"wings": ["project_kestrel"], "sensitivities": ["public", "internal"]},
        ),
        Question(
            "same-value-other-wing",
            "Kestrel",
            "default_branch",
            "main",
            {"wings": ["project_heron"], "sensitivities": ["public", "internal"]},
        ),
        Question("multi-message", "Kestrel", "backup_contact", "Ada Lovelace", SCOPED),
        Question("private-owner", "Jeffrey", "allergic_to", "penicillin", OWNER),
        Question("private-in-shared-owner", "Heron", "budget", "40k", OWNER),
        Question("private-scoped", "Jeffrey", "allergic_to", None, SCOPED, hidden=("penicillin",)),
        Question("private-in-shared-scoped", "Heron", "budget", None, SCOPED, hidden=("40k",)),
    ]
    restated = [
        ("Kestrel", "default_branch", "main", "project_kestrel"),
        ("Heron", "budget", "40k", "project_heron"),
    ]
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
            source_seq_end=fact.end,
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


def _identity(fact):
    return tuple(fact[key] for key in ("subject", "predicate", "object", "wing"))


def _ground_truth(corpus, ids):
    expected = {}
    for thread in corpus.threads:
        for fact in thread.facts:
            key = _identity(fact.__dict__)
            expected.setdefault(key, set()).add((ids[thread.key], fact.seq, fact.end))
    return expected


def _range(source):
    start = source.get("source_seq_start")
    return source.get("source_thread"), start, source.get("source_seq_end", start)


def _contains_value(text, value):
    # Literal, case-sensitive words: npm is not pnpm; main is not remain.
    return re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text) is not None


def _valid_source(store, source, value, expected):
    if source not in expected:
        return False
    thread, start, end = source
    messages = store.messages(thread, start, end)
    return [m.seq for m in messages] == list(range(start, end + 1)) and _contains_value(
        "\n".join(m.text for m in messages), value
    )


def _cites_answer(store, kg, fact, truth):
    """Every source must match the exact planted set and open its verbatim value."""
    expected = truth.get(_identity(fact), set())
    sources = [_range(s) for s in kg.sources([fact["id"]])[fact["id"]]]
    return (
        bool(expected)
        and len(sources) == len(expected)
        and set(sources) == expected
        and all(_valid_source(store, s, fact["object"], expected) for s in sources)
    )


def run(
    threads: int = 40,
    seed: int = 7,
    saver: Callable = oracle_saver,
    workdir: Optional[str] = None,
    retriever: Callable = recall,
) -> dict:
    """Build the corpus, file facts with ``saver``, answer using ``retriever``.

    The oracle deliberately receives planted facts. A step-5 extraction saver
    must use only messages; its input boundary belongs in that implementation.
    """
    corpus = build_corpus(threads, seed)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        store = ThreadStore(str(Path(tmp) / "threads.sqlite3"))
        kg = KnowledgeGraph(db_path=str(Path(tmp) / "kg.sqlite3"))
        try:
            ids = {}
            for thread in corpus.threads:
                stored = store.store(WORKSPACE, thread.key, thread.messages, actor="eval")
                ids[thread.key] = stored.id
            # Freeze the expected pointers before a saver can touch its inputs.
            truth = _ground_truth(corpus, ids)
            for thread in corpus.threads:
                saver(kg, ids[thread.key], thread)
            return _score(kg, store, corpus, truth, retriever)
        finally:
            kg.close()
            store.close()


def _question_score(q, found, store, kg, truth):
    values = [f["object"] for f in found]
    wings, sensitivities = q.reader["wings"], q.reader["sensitivities"]
    leaked = [
        f["object"]
        for f in found
        if f["object"] in q.hidden
        or (wings is not None and f["wing"] not in wings)
        or (sensitivities is not None and f["sensitivity"] not in sensitivities)
    ]
    row = {"id": q.id, "expected": q.answer, "got": values, "leaked": leaked}
    if q.answer is None:
        return {**row, "ok": not values}
    hit1 = bool(values) and values[0] == q.answer
    stale = [
        f["object"]
        for f in found
        if f["object"] in q.stale and (q.as_of is not None or f["valid_to"] is None)
    ]
    temporal_ok = q.as_of is None or (bool(values) and all(v == q.answer for v in values))
    cited = hit1 and _cites_answer(store, kg, found[0], truth)
    return {
        **row,
        "hit1": hit1,
        "hit5": q.answer in values,
        "cited": cited,
        "stale": stale,
        "temporal_ok": temporal_ok,
        "ok": hit1 and cited and not stale and not leaked and temporal_ok,
    }


def _filing_scores(kg, store, corpus, truth):
    facts = [
        fact
        for subject in sorted({key[0] for key in truth})
        for fact in kg.query_entity(subject, direction="outgoing")
    ]
    filed = {}
    spans = []
    for fact in facts:
        key = _identity(fact)
        for raw in kg.sources([fact["id"]])[fact["id"]]:
            source = _range(raw)
            if _valid_source(store, source, fact["object"], truth.get(key, set())):
                filed.setdefault(key, set()).add(source)
            _, start, end = source
            if start is not None and end is not None:
                spans.append(end - start + 1)
    covered = sum(len(expected & filed.get(key, set())) for key, expected in truth.items())
    merged = sum(
        len(matches := [f for f in facts if _identity(f) == key]) == 1
        and _cites_answer(store, kg, matches[0], truth)
        for key in corpus.restated
    )
    return {
        "merge_rate": merged / len(corpus.restated),
        "extraction_recall": covered / sum(len(s) for s in truth.values()),
        "mean_source_messages": sum(spans) / len(spans) if spans else 0,
        "max_source_messages": max(spans, default=0),
    }


def passes(report):
    return all(report[key] == bar for key, bar in BARS.items()) and all(
        row["ok"] for row in report["per_question"]
    )


def _score(kg, store, corpus, truth, retriever) -> dict:
    rows = [_question_score(q, retriever(kg, q), store, kg, truth) for q in corpus.questions]
    answerable = [r for r in rows if r["expected"] is not None]
    denied = [r for r in rows if r["expected"] is None]
    old = [r for q, r in zip(corpus.questions, rows) if q.old_thread]
    temporal = [r for q, r in zip(corpus.questions, rows) if q.as_of is not None]
    report = {
        "threads": len(corpus.threads),
        "questions": len(corpus.questions),
        "recall_at_1": sum(r["hit1"] for r in answerable) / len(answerable),
        "recall_at_5": sum(r["hit5"] for r in answerable) / len(answerable),
        "stale_rate": sum(bool(r["stale"]) for r in answerable) / len(answerable),
        "source_hit_rate": sum(r["cited"] for r in answerable) / len(answerable),
        "old_thread_recall": sum(r["ok"] for r in old) / len(old),
        "private_leaks": sum(len(r["leaked"]) for r in rows),
        "denied_ok": sum(r["ok"] for r in denied) / len(denied),
        "temporal_precision": sum(r["temporal_ok"] for r in temporal) / len(temporal),
        **_filing_scores(kg, store, corpus, truth),
        "per_question": rows,
    }
    report["passed"] = passes(report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--threads", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--saver", choices=sorted(SAVERS), default="oracle")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)
    if args.threads < 36:
        parser.error("the corpus needs at least 36 threads")
    report = run(args.threads, args.seed, SAVERS[args.saver])
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for key in (*BARS, "mean_source_messages", "max_source_messages"):
            print(f"{key:20} {report[key]}")
        for row in report["per_question"]:
            if not row["ok"]:
                print(f"MISS {row['id']}: expected {row['expected']!r}, got {row['got']!r}")
        for key, bar in BARS.items():
            if report[key] != bar:
                print(f"FAIL {key}: expected {bar}, got {report[key]}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
