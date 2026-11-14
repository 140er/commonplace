"""Bounded runtime choices carried only inside an approved signed receipt."""

import re
from urllib.parse import urlsplit

COMPONENTS = ("host", "claude", "codex")
# Current native subscription releases exceed 256 MiB. This is a release-only
# budget; credential, receipt, journal and response limits remain unchanged.
MAX_ARTIFACT = 384 * 1024 * 1024


def validate(value, topics):
    if not isinstance(value, dict) or set(value) != {"components", "topic_models"}:
        raise ValueError("invalid runtime choices")
    components, mapping = value["components"], value["topic_models"]
    if (
        not isinstance(components, dict)
        or "host" not in components
        or not set(components) <= set(COMPONENTS)
        or not isinstance(mapping, dict)
        or set(mapping) != set(topics)
    ):
        raise ValueError("invalid runtime scope")
    for release in components.values():
        if not isinstance(release, dict) or set(release) != {"version", "sha256", "url", "format"}:
            raise ValueError("invalid runtime release")
        if (
            not isinstance(release["version"], str)
            or not re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", release["version"]
            )
            or not isinstance(release["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", release["sha256"])
            or release["format"] not in ("native", "python-zipapp")
            or not isinstance(release["url"], str)
            or len(release["url"]) > 1024
        ):
            raise ValueError("unpinned runtime release")
        url = urlsplit(release["url"])
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or any(ord(c) < 33 or ord(c) > 126 for c in release["url"])
        ):
            raise ValueError("invalid runtime source")
    for choice in mapping.values():
        if (
            not isinstance(choice, dict)
            or set(choice) != {"provider", "model"}
            or choice["provider"] not in set(components) - {"host"}
            or not isinstance(choice["model"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", choice["model"])
        ):
            raise ValueError("invalid topic mapping")
    if set(components) - {"host"} != {choice["provider"] for choice in mapping.values()}:
        raise ValueError("unselected runtime component")
    return value


def facts_valid(facts, name):
    if not isinstance(facts, dict) or set(facts) != {"availability", "login"}:
        raise ValueError("invalid component status")
    if facts["availability"] not in ("present", "missing", "unsupported") or facts["login"] not in (
        "not-applicable",
        "logged-out",
        "unverified",
        "unknown",
    ):
        raise ValueError("invalid component status")
    if (name == "host" and facts["availability"] == "present") != (
        facts["login"] == "not-applicable"
    ):
        raise ValueError("invalid login status")
    return facts
