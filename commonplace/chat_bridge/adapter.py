"""The one interface a chat platform implements; platform vocabulary stays inside an adapter.

This slice needs only what an install needs: the platform name, the credential fields it
stores, its capabilities and a credential test. Later slices add verifying, normalising and
posting to the same class. An adapter never receives or returns text of a conversation here.
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
