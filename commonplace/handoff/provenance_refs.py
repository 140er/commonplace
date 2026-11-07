"""Pure, lossless reference recognition for provenance authorization."""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

PROVENANCE_FIELDS = {
    "source_thread",
    "source_url",
    "source_file",
    "source_drawer_id",
    "source_closet",
}
DERIVED_FIELDS = {"origin_id", "origin_link", "approval_link", "source_link"}
NON_PROVENANCE = {
    "subject",
    "predicate",
    "object",
    "old_object",
    "new_object",
    "valid_from",
    "valid_to",
    "ended",
    "at",
    "wing",
    "sensitivity",
    "sensitivities",
    "confidence",
    "single_valued",
    "source_seq_start",
    "source_seq_end",
    "thread_scope",
}
READ_FIELDS = {
    "triple_id",
    "source_thread",
    "source_seq_start",
    "source_seq_end",
    "source_drawer_id",
    "source_file",
    "added_at",
    "origin_kind",
    "origin_id",
    "origin_link",
    "approval_status",
    "approved_by",
    "approval_link",
    "source_closet",
    "source_url",
    "source_link",
    "fact_status",
}
REFUSE = object()
_THREAD = re.compile(r"thr_[0-9a-f]+", re.IGNORECASE)
_PRIVATE = re.compile(r"(?:capture-fork|capture|import-private):[^\s?]+", re.IGNORECASE)
_PERCENT = re.compile(r"%[0-9a-f]{2}", re.IGNORECASE)

# These domains mirror capture.SOURCES and import_sources.SOURCES plus the
# grant-only native-memory route. They constrain new shadows, never legacy ids.
_CAPTURE_SOURCES = frozenset({"claude-code", "codex"})
_IMPORT_SOURCES = _CAPTURE_SOURCES | {
    "cursor",
    "t3",
    "grok-bot",
    "chatgpt",
    "claude-ai",
    "native-memory",
}
_BOUNDARY = r"(?<![^ /?&#=(<\"'])"
_ACTOR = r"(?:: *|(?=[A-Za-z0-9]))(?P<actor>[^\s:/\\?]+): *"
_SHADOW_PRIVATE = re.compile(
    _BOUNDARY
    + r"(?P<namespace>import-private|capture)"
    + _ACTOR
    + r"(?P<source>[^\s:/\\?]+): *(?P<identity>[^\s?]+)",
    re.IGNORECASE,
)
_SHADOW_FORK = re.compile(
    _BOUNDARY + r"(?P<namespace>capture-fork)" + _ACTOR + r"(?P<identity>[^\s?]+)",
    re.IGNORECASE,
)
_FORK_DIGEST = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class References:
    text: str | None
    tokens: tuple[str, ...]


def _controls(text):
    return any(unicodedata.category(char) in {"Cc", "Cf"} for char in text)


def _shadows(text):
    """Return complete authorization candidates and their original text spans."""
    chars, offsets = [], []
    for index, char in enumerate(text):
        # NFKC must not broaden the permitted structural space alphabet.
        normalized = (
            char
            if char.isspace() and char not in {" ", "\u00a0"}
            else unicodedata.normalize("NFKC", char)
        )
        chars.append(normalized)
        offsets.extend([index] * len(normalized))
    shadow = "".join(chars)
    for pattern in (_SHADOW_FORK, _SHADOW_PRIVATE):
        for match in pattern.finditer(shadow):
            namespace = match["namespace"].lower()
            actor, identity = match["actor"], match["identity"]
            if namespace == "capture-fork":
                if not _FORK_DIGEST.fullmatch(identity):
                    continue
                token = f"{namespace}:{actor}:{identity}"
            else:
                source = match["source"].lower()
                domain = _CAPTURE_SOURCES if namespace == "capture" else _IMPORT_SOURCES
                if source not in domain:
                    continue
                token = f"{namespace}:{actor}:{source}:{identity}"
            # Compose compatibility characters across actor/identity boundaries
            # within the token too; per-character normalization above keeps spans.
            token = unicodedata.normalize("NFKC", token)
            yield offsets[match.start()], offsets[match.end() - 1] + 1, token


def _tokens(text):
    shadows = list(_shadows(text))
    tokens = [match[0] for match in _THREAD.finditer(text)]
    for match in _PRIVATE.finditer(text):
        # Structural spacing can split an ordinary scan at an internal colon.
        # The complete shaped reference replaces only that incomplete prefix.
        if any(
            start == match.start()
            and match.end() < end
            and text[match.end()] in {" ", "\u00a0"}
            and match[0].endswith(":")
            for start, end, _ in shadows
        ):
            continue
        tokens.append(match[0])
    for _, _, token in shadows:
        tokens.append(token)
        tokens.extend(match[0] for match in _THREAD.finditer(token))
    return tuple(dict.fromkeys(tokens))


def refs(value):
    """Decode at most four rounds, refusing controls before trimming anything."""
    if value is None:
        return References(None, ())
    if not isinstance(value, str) or _controls(value):
        return REFUSE
    text = value
    try:
        for _ in range(4):
            decoded = unquote(text, errors="strict")
            if _controls(decoded):
                return REFUSE
            if decoded == text:
                break
            text = decoded
    except (UnicodeError, ValueError):
        return REFUSE
    if _controls(text) or _PERCENT.search(text):
        return REFUSE
    text = text.strip()
    # Independent scans also catch thread ids embedded inside private source ids.
    return References(text, _tokens(text))
