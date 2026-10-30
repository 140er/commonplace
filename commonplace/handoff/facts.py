"""Deterministic facts from user messages, with exact source ranges.

No model and no I/O in ``extract``. The source chat is authoritative; these
triples and verbatim decision/preference sentences are only an index. Rules
are deliberately narrow: unsupported prose stays in the chat and its mirror.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from commonplace.privacy import scan
from mempalace.config import MAX_NAME_LENGTH
from mempalace.general_extractor import DECISION_MARKERS, PREFERENCE_MARKERS

from .models import MIRROR_WING
from .writer import _is_user_message

VERSION = 1
_NAME = r"(?P<subject>[^\W\d_][\w .-]{0,79}?)"
_VALUE = r"(?P<object>[^\n,;.!?]{1,180}?)"
_END = r"(?=\s+(?:since|from|now|for|today|this week)\b|[,;.!?]|$)"
_PREFIX = re.compile(r"^(?:Reminder:|Private note:|Between us:|We switched:)\s*", re.I)
_DATED = re.compile(r"^(?:On|As of) (\d{4}-\d{2}-\d{2})[, :]\s*", re.I)
_UNCERTAIN = re.compile(
    r"\b(?:if|suppose|hypothetically|example|maybe|might|could|would|should|"
    r"not|never|no longer|used to)\b",
    re.I,
)
_HYPOTHETICAL = re.compile(
    r"\b(?:if|suppose|hypothetically|example|maybe|might|could|would|should)\b", re.I
)
_PREFERENCE = re.compile("|".join(PREFERENCE_MARKERS), re.I)
_DECISION = re.compile("|".join(DECISION_MARKERS), re.I)
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
        rf"(?:For )?{_NAME}(?:['’]s)? (?:the )?(?P<property>{_PROPERTY_WORDS}) is (?:still )?{_VALUE}{_END}",
    ),
    ("default_branch", rf"the default branch for {_NAME} is {_VALUE}{_END}"),
    ("deploy_port", rf"{_NAME} deploys on port (?P<object>\d{{1,5}})\b"),
    ("works_at", rf"{_NAME} works at {_VALUE}{_END}"),
    ("works_at", rf"{_NAME} worked at {_VALUE}{_END}"),
    ("lives_in", rf"{_NAME} lives in {_VALUE}{_END}"),
    ("allergic_to", rf"{_NAME} is allergic to {_VALUE}{_END}"),
    ("uses_package_manager", rf"{_NAME} uses (?P<object>npm|pnpm|yarn|bun)\b"),
]
_RULES = [(predicate, re.compile(pattern, re.I)) for predicate, pattern in _RULES]
_CONTACT = re.compile(rf"For {_NAME}, record the backup contact[.!]?\Z", re.I)
_CONTACT_VALUE = re.compile(r"(?P<object>[^\n.!?]{1,180}?) is the person[.!]?\Z", re.I)
_PRONOUNS = {"i", "we", "you", "he", "she", "they", "it", "this", "that"}


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
            return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        yield from re.split(r"(?<=[.!?])\s+", line)


def _safe(message):
    if not _is_user_message(message) or "\x00" in message.text:
        return False
    try:
        message.text.encode("utf-8")
    except UnicodeEncodeError:
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
        match = pattern.match(text)
        if match is None:
            continue
        if predicate == "works_at" and " worked at " in text.lower() and not dated:
            return None  # A past employer without a date must not replace today's one.
        subject, obj = match["subject"].strip(), match["object"].strip()
        if subject.lower() in _PRONOUNS or not obj or len(obj) > MAX_NAME_LENGTH:
            return None
        if predicate == "property":
            predicate = _PROPERTIES[match["property"].lower()]
        if predicate.endswith("deploy_port") and (not obj.isdigit() or not 1 <= int(obj) <= 65535):
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
    """Extract from messages and trusted user/time metadata, never eval answers.

    Structured rules require a named subject. Verbatim decision/preference
    sentences use the authenticated user's identity when one is supplied.
    """
    out = []
    previous = None
    for message in messages:
        if not _safe(message):
            previous = None
            continue
        at = _instant(message.at, fallback_at)
        # The only cross-message rule requires adjacent user messages and an
        # explicit named subject; no pronoun or topic guessing.
        contact = _CONTACT.fullmatch(previous.text.strip()) if previous else None
        value = _CONTACT_VALUE.fullmatch(message.text.strip())
        if (
            contact
            and value
            and previous.seq + 1 == message.seq
            and not _UNCERTAIN.search(message.text)
            and not _UNCERTAIN.search(previous.text)
            and contact["subject"].lower() not in _PRONOUNS
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
            elif (
                user
                and 1 <= len(sentence) <= MAX_NAME_LENGTH
                and not sentence.endswith("?")
                and not _HYPOTHETICAL.search(sentence)
            ):
                kind = (
                    "preference"
                    if _PREFERENCE.search(sentence)
                    else ("decision" if _DECISION.search(sentence) else None)
                )
                if kind:
                    out.append(Fact(user, kind, sentence, message.seq, message.seq, at))
        previous = message
    return list(dict.fromkeys(out))


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
        facts = extract(messages, user=user, fallback_at=stored.created_at)
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
