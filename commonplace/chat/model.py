"""Bounded, explicit model transport without retries or provider error text."""

import http.client
import json
import os
import time
from urllib.parse import urlsplit

from commonplace.chat.types import (
    ANTHROPIC_VERSION,
    KEY_HOSTS_ENV,
    MAX_INPUT_CHARS,
    MAX_OUTPUT_TOKENS,
    MAX_RESPONSE_BYTES,
    MODEL_TIMEOUT_S,
    ChatDenied,
    FrontSettings,
    ModelError,
)

ANTHROPIC_ORIGIN = "https://api.anthropic.com"


def complete(
    settings: FrontSettings,
    key: str | None,
    system: str,
    turns: list[dict],
    *,
    timeout: float = MODEL_TIMEOUT_S,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> str:
    if len(system) + sum(len(turn["content"]) for turn in turns) > MAX_INPUT_CHARS:
        raise ValueError("model input too long")
    anthropic = settings.provider == "anthropic"
    if not anthropic and settings.provider != "openai_compatible":
        raise ValueError("model provider unavailable")
    origin = urlsplit(ANTHROPIC_ORIGIN if anthropic else settings.base_url or "")
    if origin.scheme not in ("http", "https") or not origin.hostname:
        raise ValueError("invalid model origin")
    if key is not None and not anthropic:
        hosts = {host.strip().lower() for host in os.environ.get(KEY_HOSTS_ENV, "").split(",")}
        if origin.hostname.lower() not in hosts:
            raise ChatDenied("model key host not allowed")
    headers = {"Content-Type": "application/json"}
    body = {
        "model": settings.model,
        "max_tokens": min(max_tokens, MAX_OUTPUT_TOKENS),
        "temperature": 0,
    }
    if anthropic:
        headers.update({"anthropic-version": ANTHROPIC_VERSION})
        if key is not None:
            headers["x-api-key"] = key
        body.update(system=system, messages=turns)
    else:
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        body.update(stream=False, messages=[{"role": "system", "content": system}, *turns])
    payload = json.dumps(body).encode()
    deadline = time.monotonic() + timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise ModelError("timeout")
        return seconds

    cls = http.client.HTTPSConnection if origin.scheme == "https" else http.client.HTTPConnection
    conn = cls(origin.hostname, origin.port, timeout=remaining())
    response = None
    try:
        conn.connect()
        sock = conn.sock
        sock.settimeout(remaining())
        path = origin.path.rstrip("/") + ("/v1/messages" if anthropic else "/v1/chat/completions")
        conn.request("POST", path, payload, headers)
        sock.settimeout(remaining())
        response = conn.getresponse()
        if response.status != 200:
            raise ModelError("http_error")
        chunks = bytearray()
        while not response.isclosed():
            # Keep the socket reference: HTTP/1.0 responses detach it from conn.
            sock.settimeout(remaining())
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - len(chunks)))
            remaining()
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > MAX_RESPONSE_BYTES:
                raise ModelError("bad_response")
        try:
            data = json.loads(chunks)
            if anthropic:
                first = data["content"][0]
                if first["type"] != "text":
                    raise ValueError
                answer = first["text"]
            else:
                answer = data["choices"][0]["message"]["content"]
            if not isinstance(answer, str):
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("bad_response") from None
        return answer
    except TimeoutError:
        raise ModelError("timeout") from None
    except (OSError, http.client.HTTPException):
        raise ModelError("http_error") from None
    finally:
        if response is not None:
            response.close()
        conn.close()
