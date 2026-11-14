"""Shared sanitization for per-job launch diagnostics, before any persistence."""

import re

from commonplace.redact import redact_tail

MAX_CHARS = 4000
_TOKEN_LIKE = re.compile(r"[A-Za-z0-9_\-.]{40,}")


def clean(text, secrets=(), *, limit=MAX_CHARS):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return redact_tail(_TOKEN_LIKE.sub("<redacted>", text), limit)


def captured(label, stdout, stderr, secrets):
    """Reserve space for both stream tails, their labels and the exit status."""

    def text(raw):
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")

    header = label + "\nstdout:\n"
    middle = "\nstderr:\n"
    budget = MAX_CHARS - len(header) - len(middle)
    out = clean(text(stdout), secrets, limit=budget // 2)
    err = clean(text(stderr), secrets, limit=budget - len(out))
    return header + out + middle + err
