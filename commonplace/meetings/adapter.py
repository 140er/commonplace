"""The meeting provider interface and the in-memory fake.

A provider is one :class:`Adapter`. The engine, the consent rules and the routes never name a
platform; provider names appear only in adapter modules and fixtures. Nothing is registered by
default, so a hub offers no provider until one is added with :func:`register`.

``join``, ``leave``, ``find``, ``events``, ``audio`` and ``post_chat`` carry the meeting lifecycle
(``engine.py``); ``verify`` is for webhook providers and is declared for the first real adapter.
The fake gives every one of them a scripted, in-memory behaviour.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Iterator, Optional

PROVIDER = re.compile(r"[a-z][a-z0-9_-]{1,31}\Z")
# What ``test`` may tell the owner. Anything else an adapter returns is shown as ``failed``, so a
# provider error string (which may echo a credential) never reaches a response or an audit row.
REASONS = frozenset(
    {"bad_credentials", "missing_permission", "no_chat_send", "unreachable", "adapter_error"}
)


@dataclass(frozen=True)
class Caps:
    chat_send: bool = False
    chat_receive: bool = False
    per_speaker_audio: bool = False
    captions: bool = False
    remove_detect: bool = False
    participant_list: bool = False
    lobby: bool = False
    max_text: int = 2000
    transport: str = "webhook"  # or "socket": events arrive over an outbound connection


@dataclass(frozen=True)
class Connection:
    """What an adapter sees of a configured connection. The secrets exist only in memory."""

    id: str
    provider: str
    label: str
    secrets: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # not a pytest class

    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class JoinRef:
    ref: str  # the normalized meeting link; sealed before it is stored
    ext_meeting: str  # opaque provider meeting id, never a passcode


@dataclass(frozen=True)
class Reject:
    reason: str


@dataclass(frozen=True)
class JoinHandle:
    handle: str


@dataclass(frozen=True)
class Lobby:
    handle: str


@dataclass(frozen=True)
class Event:
    kind: str  # joined | admitted | lobby_timeout | participant_joined | participant_left | chat |
    # removed | meeting_ended | tick (an idle heartbeat: lets the engine check its limits)
    ext_event: str = ""
    ext_participant: Optional[str] = None
    host: bool = False
    text: str = ""  # chat text, or the provider's display name for a participant event (untrusted)
    addressed: bool = False


@dataclass(frozen=True)
class Frame:
    pcm: bytes = field(repr=False)
    ext_participant: Optional[str] = None


@dataclass(frozen=True)
class PostResult:
    ok: bool
    ext_post: str = ""


@dataclass(frozen=True)
class Verified:
    connection_id: str
    ext_meeting: str = ""


class Adapter:
    provider: str = ""
    caps: Caps = Caps()

    def parse_link(self, link: str) -> JoinRef | Reject:
        raise NotImplementedError

    def test(self, connection: Connection) -> TestResult:
        raise NotImplementedError

    def join(
        self, connection, join_ref, display_name, idempotency_key
    ) -> JoinHandle | Lobby | Reject:
        raise NotImplementedError

    def leave(self, connection, handle) -> None:
        raise NotImplementedError

    def find(self, connection, idempotency_key) -> Optional[JoinHandle]:
        """The handle of an earlier ``join`` with this key, or None. Recovery uses it to make a
        bot whose worker died leave; it never joins."""
        raise NotImplementedError

    def events(self, connection, handle) -> Iterator[Event]:
        """Lifecycle, chat and participant events. A blocking stream must yield a ``tick`` at
        least every few seconds: the engine checks its lease, switches and limits between events."""
        raise NotImplementedError

    def audio(self, connection, handle) -> Iterator[Frame]:
        raise NotImplementedError

    def post_chat(self, connection, handle, text, idempotency_key) -> PostResult:
        raise NotImplementedError

    def verify(self, connection, headers, body) -> Verified | Reject:
        raise NotImplementedError


class Wait:
    """A script step for the fake: hold the event stream until its audio has been consumed."""


class FakeAdapter(Adapter):
    """Scripted, in-memory, no network. Everything it was asked to do is recorded.

    ``script`` is the event stream (:class:`Event`, a callable run at that point, or :class:`Wait`),
    ``frames`` the audio. ``join_result`` is what ``join`` answers (default: a handle), and
    ``join_errors`` how many calls raise first. ``post_results`` are used one per ``post_chat``
    call (a :class:`PostResult` or an exception to raise), then posts succeed.
    """

    FAKE_HOST = "meet.example.test"
    LINK = (
        rf"https://{re.escape(FAKE_HOST)}/m/([A-Za-z0-9-]{{4,64}})(?:\?pwd=[A-Za-z0-9_-]{{1,64}})?"
    )

    def __init__(self, provider="fake", caps=None, result=None, raises=None):
        self.provider = provider
        self.caps = caps if caps is not None else Caps(chat_send=True, chat_receive=True)
        self.result = result if result is not None else TestResult(True)
        self.raises = raises
        self.tested: list[Connection] = []
        self.script: list = []
        self.frames: list[Frame] = []
        self.join_result = None
        self.join_errors = 0
        self.post_results: list = []
        self.joins: list[tuple] = []  # (display_name, idempotency_key)
        self.leaves: list[str] = []  # handles
        self.posts: list[tuple] = []  # (text, idempotency_key)
        self.handles: dict[str, JoinHandle] = {}
        self.audio_opened_after_posts: Optional[int] = None
        self.audio_done = threading.Event()
        self.log: list[str] = []  # shared order of "join", "post", "audio", "leave"

    def parse_link(self, link):
        match = re.fullmatch(self.LINK, link)
        if not match:
            return Reject("foreign_host")
        return JoinRef(ref=link, ext_meeting=match[1])

    def test(self, connection):
        self.tested.append(connection)
        if self.raises is not None:
            raise self.raises
        return self.result

    def join(self, connection, join_ref, display_name, idempotency_key):
        self.joins.append((display_name, idempotency_key))
        self.log.append("join")
        if self.join_errors > 0:
            self.join_errors -= 1
            raise ConnectionError("provider unavailable")
        result = self.join_result or JoinHandle(f"handle-{idempotency_key}")
        self.handles[idempotency_key] = result
        return result

    def find(self, connection, idempotency_key):
        return self.handles.get(idempotency_key)

    def leave(self, connection, handle):
        self.leaves.append(handle.handle if hasattr(handle, "handle") else str(handle))
        self.log.append("leave")

    def events(self, connection, handle):
        for step in list(self.script):
            if isinstance(step, Wait):
                self.audio_done.wait(2)
            elif callable(step):
                step()
            else:
                yield step

    def audio(self, connection, handle):
        self.audio_opened_after_posts = len(self.posts)
        self.log.append("audio")
        try:
            yield from list(self.frames)
        finally:
            self.audio_done.set()

    def post_chat(self, connection, handle, text, idempotency_key):
        if self.post_results:
            outcome = self.post_results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if not outcome.ok:
                return outcome
        self.log.append("post")
        if all(key != idempotency_key for _, key in self.posts):
            self.posts.append((text, idempotency_key))
        return PostResult(True, f"post-{idempotency_key}")


_ADAPTERS: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    if not PROVIDER.fullmatch(adapter.provider):
        raise ValueError("invalid provider name")
    if adapter.provider in _ADAPTERS and _ADAPTERS[adapter.provider] is not adapter:
        raise ValueError("provider registered twice")
    _ADAPTERS[adapter.provider] = adapter
    return adapter


def unregister(provider: str) -> None:
    _ADAPTERS.pop(provider, None)


def get(provider: str) -> Optional[Adapter]:
    return _ADAPTERS.get(provider)


def providers() -> list[str]:
    return sorted(_ADAPTERS)
