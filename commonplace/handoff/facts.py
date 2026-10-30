"""Deterministic facts from user messages, with exact source ranges.

No model and no I/O in ``extract``. The source chat is authoritative; these
triples are only an index; transcript roles do not verify their speakers. Rules
are deliberately narrow: unsupported prose stays in the chat and its mirror.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

from commonplace.privacy import scan
from mempalace.config import MAX_NAME_LENGTH

from .models import MIRROR_WING
from .writer import _is_user_message

VERSION = 1
# Background parsing gives the GIL back this often, then rests as long: a large
# backlog must not slow the gateway's request threads (thread reads stay < 100 ms).
PACE_SECONDS = 0.002
_NAME = r"(?P<subject>[^\W\d_][\w .’'-]{0,79}?)"
_VALUE = r"(?P<object>[^\n,;!?]{1,180}?)"
_PREFIX = re.compile(r"^(?:[A-Za-z]+(?: [A-Za-z]+){0,2}:|FYI,)\s*", re.I)
_DATED = re.compile(r"^(?:On|As of) (\d{4}-\d{2}-\d{2})[, :]\s*", re.I)
_UNCERTAIN = re.compile(
    r"\b(?:if|suppose|hypothetically|example|imagine|maybe|might|could|would|should|"
    r"not|never|no longer|used to|anymore)\b|\be\.g\.",
    re.I,
)
_WRAPPER = re.compile(
    r"<[/!A-Za-z]|(?im:^\s*#*\s*AGENTS\.md instructions)|"
    r"Base directory for this skill:|(?im:^\s*(?:Orchestrator|Chief|Agent|Assistant|Steer from Chief)\s*:)",
)
_ABBREVIATION = re.compile(r"(?:\b(?:st|dr|mr|mrs|ms|prof|sr|jr|e\.g|i\.e)|\b[A-Z])\.$", re.I)
_VALUE_SCOPE = re.compile(
    r"\b(?:and|but|or|since|from|for|until|in|on|during|except|unless|anymore)\b", re.I
)
_PROPERTIES = {
    "deploy port": "deploy_port",
    "staging deploy port": "staging_deploy_port",
    "default branch": "default_branch",
    "package manager": "uses_package_manager",
    "model": "uses_model",
    "budget": "budget",
    "backup contact": "backup_contact",
}
_PROPERTY_WORDS = "|".join(_PROPERTIES)
_RULES = [
    (
        "property",
        rf"(?:For )?{_NAME}(?:['’]s)? (?:the )?(?P<property>{_PROPERTY_WORDS}) is (?:still )?{_VALUE}\.?",
    ),
    ("default_branch", rf"the default branch for {_NAME} is {_VALUE}\.?"),
    ("backup_contact", rf"the backup contact for {_NAME} is {_VALUE}\.?"),
    ("deploy_port", rf"{_NAME} deploys on port (?P<object>\d{{1,5}})\.?"),
    ("works_at", rf"{_NAME} works at {_VALUE}\.?"),
    ("allergic_to", rf"{_NAME} is allergic to {_VALUE}\.?"),
    ("uses_package_manager", rf"{_NAME} uses (?P<object>npm|pnpm|yarn|bun)(?: now)?\.?"),
]
_RULES = [(predicate, re.compile(pattern, re.I)) for predicate, pattern in _RULES]
_CONTACT = re.compile(rf"For {_NAME}, record the backup contact[.!]?\Z", re.I)
_CONTACT_VALUE = re.compile(r"(?P<object>[^\n.!?]{1,180}?) is the person[.!]?\Z", re.I)
_NOT_NAMES = {
    "i",
    "we",
    "you",
    "he",
    "she",
    "they",
    "it",
    "this",
    "that",
    "the",
    "a",
    "an",
    "our",
    "my",
    "your",
    "their",
    "nobody",
    "somebody",
    "someone",
    "everybody",
    "everyone",
    "anyone",
    "all",
    "none",
    "no",
    "these",
    "those",
    "shared",
    "default",
}


def _proper_name(value):
    words = value.split()
    return (
        bool(words)
        and len(words) <= 5
        and words[0].lower() not in _NOT_NAMES
        and all(re.fullmatch(r"[^\W\d_][\w’'-]*", word) and word[0].isupper() for word in words)
    )


def _literal_value(predicate, value):
    if _VALUE_SCOPE.search(value) or re.search(r"\b(?:St|Dr|Mr|Mrs|Ms|Prof)\.|\b[A-Z]\.", value):
        return False
    if predicate.endswith("deploy_port"):
        return value.isdigit() and 1 <= int(value) <= 65535
    if predicate in ("works_at", "backup_contact"):
        return _proper_name(value)
    if predicate == "default_branch":
        return re.fullmatch(r"[\w][\w./-]*", value) is not None
    if predicate == "uses_model":
        return re.fullmatch(r"[\w][\w.:/-]*", value) is not None
    if predicate == "uses_package_manager":
        return value.lower() in {"npm", "pnpm", "yarn", "bun"}
    if predicate == "budget":
        return re.fullmatch(r"[$€£]?\d+(?:\.\d+)?(?:[kKmMbB]| (?:EUR|USD|GBP))?", value) is not None
    return re.fullmatch(r"[\w-]+(?: [\w-]+){0,2}", value) is not None


@dataclass(frozen=True)
class Fact:
    subject: str
    predicate: str
    object: str
    seq_start: int
    seq_end: int
    valid_from: str
    single_valued: bool = False

    def arguments(self, stored):
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "theme": MIRROR_WING,
            "sensitivity": "private",
            "valid_from": self.valid_from,
            "single_valued": self.single_valued,
            "source": {"thread": stored.id, "seq_start": self.seq_start, "seq_end": self.seq_end},
        }


def _instant(value, fallback):
    for candidate in (value, fallback):
        try:
            stamp = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            # KG temporal values deliberately use whole-second canonical UTC.
            return (
                stamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            )
        except (ValueError, AttributeError, OverflowError):
            continue
    raise ValueError("a fact needs a source timestamp")


def _sentences(text):
    fenced = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or line.startswith((">", "<", '"', "'")):
            continue
        start = 0
        for boundary in re.finditer(r"[.!?](?:\s+|$)", line):
            candidate = line[start : boundary.start() + 1]
            if _ABBREVIATION.search(candidate):
                continue
            yield candidate
            start = boundary.end()
        if line[start:]:
            yield line[start:]


def _safe(message):
    if not _is_user_message(message) or "\x00" in message.text or _WRAPPER.search(message.text):
        return False
    try:
        message.text.encode("utf-8")
        raw = json.loads(message.raw)
    except UnicodeEncodeError:
        return False
    except (ValueError, TypeError, RecursionError):
        raw = {}
    if isinstance(raw, dict) and any(
        isinstance(record, dict) and record.get("isMeta")
        for record in (raw, raw.get("message"), raw.get("payload"))
    ):
        return False
    return not any(flag.startswith(("secret:", "injection:")) for flag in scan(message.text))


def _structured(sentence, message, at):
    if sentence.endswith("?") or _UNCERTAIN.search(sentence):
        return None
    text = _PREFIX.sub("", sentence)
    dated = _DATED.match(text)
    if dated:
        try:
            at = date.fromisoformat(dated[1]).isoformat()
        except ValueError:
            return None
        text = text[dated.end() :]
    for predicate, pattern in _RULES:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        subject = match["subject"].strip().removesuffix(" now")
        obj = match["object"].strip()
        if predicate == "property":
            predicate = _PROPERTIES[match["property"].lower()]
        if (
            not _proper_name(subject)
            or len(obj) > MAX_NAME_LENGTH
            or not _literal_value(predicate, obj)
        ):
            return None
        return Fact(
            subject,
            predicate,
            obj,
            message.seq,
            message.seq,
            at,
            single_valued=predicate != "allergic_to",
        )
    return None


def extract(messages, *, user: str = "", fallback_at: str) -> list[Fact]:
    """Index complete named assertions, never infer a deciding speaker.

    ``user`` is retained for caller compatibility, but cannot establish who
    authored a transcript. First-person preferences/decisions, past-tense
    employers, ambiguous locations and qualified values stay in the chat.
    """
    out = []
    previous = None
    for message in messages:
        if not _safe(message):
            previous = None
            continue
        at = _instant(message.at, fallback_at)
        contact = _CONTACT.fullmatch(previous.text.strip()) if previous else None
        value = _CONTACT_VALUE.fullmatch(message.text.strip())
        if (
            contact
            and value
            and previous.seq + 1 == message.seq
            and not _UNCERTAIN.search(message.text)
            and not _UNCERTAIN.search(previous.text)
            and _proper_name(contact["subject"])
            and _proper_name(value["object"])
            and len(value["object"]) <= MAX_NAME_LENGTH
        ):
            out.append(
                Fact(
                    contact["subject"],
                    "backup_contact",
                    value["object"],
                    previous.seq,
                    message.seq,
                    at,
                    single_valued=True,
                )
            )
        for sentence in _sentences(message.text):
            fact = _structured(sentence, message, at)
            if fact:
                out.append(fact)
        previous = message
    return list(dict.fromkeys(out))


class _Paced(list):
    """The batch as a list; iterating it rests after every ``PACE_SECONDS``."""

    def __iter__(self):
        started = time.perf_counter()
        for message in super().__iter__():
            if time.perf_counter() - started >= PACE_SECONDS:
                time.sleep(PACE_SECONDS)
                started = time.perf_counter()
            yield message


def file_pending(store, stored, save_fact, *, user="") -> int:
    """Resume bounded batches from a durable cursor; failed messages are retried.

    ``save_fact(arguments)`` must raise on a policy or worker failure. A crash
    between save and checkpoint may replay a save; fact_save dedupes its row
    and source. One-message lookbehind preserves cross-message facts on retry.
    """
    cursor = store.fact_cursor(stored.id, VERSION)
    filed = 0
    while True:
        messages = store.messages(stored.id, max(0, cursor - 1), cursor + 127)
        pending = [m for m in messages if m.seq >= cursor]
        if not pending:
            return filed
        facts = extract(_Paced(messages), user=user, fallback_at=stored.created_at)
        by_end = {}
        for fact in facts:
            if fact.seq_end >= cursor:
                by_end.setdefault(fact.seq_end, []).append(fact)
        completed = cursor
        try:
            for message in pending:
                for fact in by_end.get(message.seq, []):
                    save_fact(fact.arguments(stored))
                    filed += 1
                completed = message.seq + 1
        finally:
            if completed > cursor:
                store.advance_fact_cursor(stored.id, VERSION, completed)
        cursor = completed
