"""The one interface a chat platform implements; platform vocabulary stays inside an adapter.

An adapter names its platform and the credential fields an install stores, tests credentials,
verifies and normalises an inbound request, and posts. Everything a platform says is reduced
here to the few fields the engine needs (:class:`Inbound`); everything the engine says is plain
text with optional buttons (:class:`Outbound`) that the adapter renders and escapes for its own
platform and splits to ``caps.max_text``. No platform name, markup or id format is known outside
an adapter.
"""

from dataclasses import dataclass, field

TEST_CODES = ("ok", "credentials", "team_mismatch", "missing_permission", "unreachable")

# One fixed sentence per code, so a platform's own error text never reaches a person.
TEST_TEXT = {
    "ok": "The connection works.",
    "credentials": "The platform did not accept the credentials.",
    "team_mismatch": "The credentials belong to another team than the one entered.",
    "missing_permission": "The app is missing a permission it needs.",
    "unreachable": "The platform could not be reached. Try again.",
    "failed": "The test could not be completed.",
}


@dataclass(frozen=True)
class Caps:
    threads: bool = True
    buttons: bool = True
    private_reply: bool = True
    edit: bool = False
    find_posted: bool = False
    max_text: int = 4000
    transport: str = "http"  # 'http' | 'socket'


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # not a pytest class

    ok: bool
    code: str = "ok"


@dataclass(frozen=True)
class Verified:
    """A request whose signature and freshness checked out: the team it names and its payload."""

    team: str
    payload: dict = field(repr=False)


@dataclass(frozen=True)
class Challenge:
    """A platform handshake the adapter answers itself (no event, no journal row)."""

    body: dict
    status: int = 200


@dataclass(frozen=True)
class Reject:
    """A forged, stale or malformed request. The reason is a fixed code for logs, never echoed."""

    reason: str = "rejected"


@dataclass(frozen=True)
class Inbound:
    """What the engine needs of a message the platform delivered, and nothing else. ``text`` has
    the app mention already stripped. ``kind`` is ``dm``, ``mention`` or ``other``."""

    ext_event: str
    ext_user: str
    ext_channel: str
    ext_thread: str
    ext_message: str
    text: str = field(repr=False)
    kind: str = "other"
    from_bot: bool = False
    shared_channel: bool = False


@dataclass(frozen=True)
class Interaction:
    """A button tap or command (handled from the confirmation slice on)."""

    ext_event: str
    ext_user: str
    ext_channel: str
    payload: str = field(repr=False)


@dataclass(frozen=True)
class Outbound:
    """Plain text and optional ``(label, payload)`` buttons; the adapter renders both."""

    text: str
    buttons: tuple = ()


@dataclass(frozen=True)
class Target:
    """Where a post goes: a channel and, for a reply, the thread of the triggering message."""

    ext_channel: str
    ext_thread: str = ""


@dataclass(frozen=True)
class PostResult:
    """``ok`` with the platform's post id; else ``retry`` says the failure was definite and
    nothing was posted (rate limit, server error), with ``retry_after`` seconds when known."""

    ok: bool
    ext_post: str = ""
    retry: bool = False
    retry_after: int = 0


@dataclass(frozen=True)
class InstallRef:
    """What an adapter is given about an install. Its repr never shows the credentials."""

    id: str
    platform: str
    ext_team: str
    secrets: dict = field(repr=False)


class Adapter:
    platform: str = ""
    secret_fields: tuple = ()  # the credential names an install stores, all required
    caps: Caps = Caps()

    def test(self, install: InstallRef) -> TestResult:
        """Check the credentials and that they belong to ``install.ext_team``."""
        raise NotImplementedError

    def verify(self, install: InstallRef, headers: dict, body: bytes):
        """:class:`Verified`, :class:`Challenge` or :class:`Reject`. Checks the signature (a
        constant-time compare against the sealed secrets) and a five-minute freshness window
        before parsing anything. ``headers`` have lower-case names."""
        raise NotImplementedError

    def normalize(self, install: InstallRef, verified: Verified):
        """:class:`Inbound`, :class:`Interaction`, or None for what is not a message to act on
        (edits, deletions, joins, files)."""
        raise NotImplementedError

    def post(self, install: InstallRef, target: Target, outbound: Outbound, key: str):
        """Post into a thread or channel as the app; ``key`` is the idempotency key. Returns a
        :class:`PostResult`."""
        raise NotImplementedError

    def post_private(self, install: InstallRef, ext_user: str, ext_channel: str, outbound):
        raise NotImplementedError

    def find_posted(self, install: InstallRef, ext_channel: str, key: str):
        """The platform id of an earlier post made with ``key``, or None (``caps.find_posted``)."""
        raise NotImplementedError


ADAPTERS: dict = {}


def register(adapter: Adapter) -> Adapter:
    if not adapter.platform or adapter.platform in ADAPTERS:
        raise ValueError("adapter platform is empty or already registered")
    ADAPTERS[adapter.platform] = adapter
    return adapter


def get(platform):
    return ADAPTERS.get(platform) if isinstance(platform, str) else None


def run_test(adapter: Adapter, install: InstallRef) -> TestResult:
    """The adapter's test, reduced to a known code; any fault is plain ``failed``."""
    try:
        result = adapter.test(install)
    except Exception:  # noqa: BLE001 - a platform's error text must not travel further
        return TestResult(False, "failed")
    if not isinstance(result, TestResult) or result.code not in TEST_CODES:
        return TestResult(False, "failed")
    ok = result.ok and result.code == "ok"
    return TestResult(ok, "ok" if ok else result.code if result.code != "ok" else "failed")
