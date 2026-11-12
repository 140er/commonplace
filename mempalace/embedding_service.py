"""One local embedding service shared by every MemPalace process on a machine.

Each process that embeds (an MCP worker, ``mempalace mine``, a search)
otherwise loads its own copy of the ONNX model. With the opt-in switch
``embedding_service`` (env ``MEMPALACE_EMBEDDING_SERVICE``) set, the
local ONNX models (``minilm``, ``embeddinggemma``) are served by one
process instead and every caller sends its texts there:

    python -m mempalace.embedding_service --socket /run/mempalace/embedding.sock
    MEMPALACE_EMBEDDING_SERVICE=/run/mempalace/embedding.sock mempalace search "..."

Local only. The transport is a Unix domain socket (mode 0600, same-user
peers), so texts never leave the machine and there is no network listener
to misconfigure. The service runs the same embedding function the caller
would have built in-process, so the vectors are the same bytes.

When the service is down, a caller raises
:class:`EmbeddingServiceUnavailableError`. Nothing falls back to another
model or to a remote provider, and callers do not load the model themselves
(that would bring back the per-process copies this exists to remove).
Search degrades to lexical ranking and says so; writes are refused with the
error until the service is back.

Memory is bounded by construction: one model, a cap on concurrent
inference runs, on open connections, on request bytes and on texts per
request. Requests and texts are never logged or kept.

The switch is off by default, and ``embeddinggemma2`` and ``openai-compat``
are never routed here.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import stat
import struct
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
# The models a service can hold: the local ONNX ones. ``embeddinggemma2``
# takes per-document metadata and media, ``openai-compat`` is already a
# remote call; both stay with the caller.
SERVICE_MODELS = frozenset({"minilm", "embeddinggemma"})
DEFAULT_SOCKET_NAME = "embedding.sock"

DEFAULT_MAX_CONNECTIONS = 8
DEFAULT_MAX_CONCURRENT = 2
DEFAULT_MAX_REQUEST_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_TEXTS = 1024
DEFAULT_IO_TIMEOUT_S = 30.0

# Client side. A request carries at most this many texts and roughly this
# many bytes, both under the service defaults. The text count is a multiple
# of the models' 32-row sub-batch, so MiniLM (fixed-width padding) splits
# into the same sub-batches it would in-process.
CLIENT_MAX_TEXTS = 256
CLIENT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_TIMEOUT_S = 120.0
CONNECT_TIMEOUT_S = 2.0
# After a transport failure, fail fast for this long instead of waiting on
# the socket again: one search embeds more than once.
DOWN_COOLDOWN_S = 3.0

EMBEDDING_SERVICE_UNAVAILABLE_ERROR = "Embedding service unavailable"
EMBEDDING_SERVICE_HINT = (
    "Start it with `python -m mempalace.embedding_service`, or unset "
    "embedding_service / MEMPALACE_EMBEDDING_SERVICE to embed in-process. "
    "Search keeps working with lexical ranking meanwhile."
)

_OFF = frozenset({"", "0", "off", "false", "no", "none"})
_ON = frozenset({"1", "on", "true", "yes"})
_HEADER = struct.Struct(">I")
_MAX_HEADER_BYTES = 64 * 1024


class EmbeddingServiceUnavailableError(RuntimeError):
    """The shared embedding service did not answer, or refused the request.

    Raised instead of embedding with anything else: the caller neither loads
    the model itself nor reaches for another provider.
    """

    def __init__(self, address: str, reason: str):
        self.address = address
        self.reason = reason
        super().__init__(
            f"The embedding service at {address} is unavailable: {reason}\n"
            "MemPalace does not fall back to another embedding model or provider. "
            + EMBEDDING_SERVICE_HINT
        )


def resolve_address(value, config_dir) -> Optional[str]:
    """The socket path for a configured ``embedding_service`` value, or None when off.

    Off: unset, empty, ``0``/``off``/``false``/``no``. ``1``/``on``/``true``/
    ``yes`` is ``embedding.sock`` in the config directory. Anything else is
    the socket path itself and must be absolute, because callers with
    different working directories have to reach the same socket.
    """
    if value is None or value is False:
        return None
    if value is True:
        return str(Path(config_dir) / DEFAULT_SOCKET_NAME)
    text = str(value).strip()
    if text.lower() in _OFF:
        return None
    if text.lower() in _ON:
        return str(Path(config_dir) / DEFAULT_SOCKET_NAME)
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError(
            f"embedding_service must be an absolute socket path or on/off, got {text!r}"
        )
    return str(path)


def configured_address() -> Optional[str]:
    """The configured service socket path, or None when the switch is off."""
    from .config import MempalaceConfig

    return MempalaceConfig().embedding_service


# ── wire format ──────────────────────────────────────────────────────────
# Every message is a 4-byte big-endian length and that many bytes of JSON.
# An ``embed`` reply is followed by ``n * dim`` little-endian float32 values:
# the vectors exactly as the model produced them.


def _recv_exact(sock, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(min(remaining, 1 << 20))
        if not chunk:
            raise ConnectionError("connection closed mid-message")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _send_message(sock, message: dict, payload: bytes = b"") -> None:
    # ensure_ascii keeps a lone surrogate (a stray source byte) intact.
    body = json.dumps(message, separators=(",", ":")).encode("ascii")
    sock.sendall(_HEADER.pack(len(body)) + body + payload)


def _recv_message(sock, max_bytes: int) -> dict:
    (size,) = _HEADER.unpack(_recv_exact(sock, _HEADER.size))
    if size > max_bytes:
        raise _TooLarge(size)
    message = json.loads(_recv_exact(sock, size))
    if not isinstance(message, dict):
        raise ValueError("message is not an object")
    return message


class _TooLarge(ValueError):
    def __init__(self, size: int):
        self.size = size
        super().__init__(f"message of {size} bytes is over the limit")


# ── client ───────────────────────────────────────────────────────────────


class EmbeddingServiceClient:
    """Sends texts to the service and returns its vectors. Thread-safe.

    One connection per request: a Unix-socket connect is tens of
    microseconds, and no pooled connection can go stale across a service
    restart.
    """

    def __init__(self, address: str, model: str, timeout: Optional[float] = None):
        self.address = address
        self.model = model
        self.timeout = _client_timeout() if timeout is None else timeout
        self._down_until = 0.0
        self._down_reason = ""

    def _unavailable(self, reason: str, *, transport: bool) -> EmbeddingServiceUnavailableError:
        if transport:
            self._down_reason = reason
            self._down_until = time.monotonic() + DOWN_COOLDOWN_S
        return EmbeddingServiceUnavailableError(self.address, reason)

    def _request(self, message: dict):
        """Return ``(reply, payload)`` for one request, or raise unavailable."""
        if time.monotonic() < self._down_until:
            raise EmbeddingServiceUnavailableError(self.address, self._down_reason)
        if not hasattr(socket, "AF_UNIX"):
            raise self._unavailable(
                "this platform has no Unix domain sockets, which the service needs",
                transport=False,
            )
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(min(CONNECT_TIMEOUT_S, self.timeout))
                sock.connect(self.address)
                sock.settimeout(self.timeout)
                _send_message(sock, message)
                reply = _recv_message(sock, _MAX_HEADER_BYTES)
                payload = b""
                if reply.get("ok") and reply.get("payload_bytes"):
                    payload = _recv_exact(sock, int(reply["payload_bytes"]))
        except FileNotFoundError as exc:
            reason = "no service is listening (socket not found)"
            raise self._unavailable(reason, transport=True) from exc
        except ConnectionRefusedError as exc:
            reason = "no service is listening (connection refused)"
            raise self._unavailable(reason, transport=True) from exc
        except socket.timeout as exc:
            reason = f"no answer within {self.timeout:g}s"
            raise self._unavailable(reason, transport=True) from exc
        except (OSError, ValueError) as exc:
            raise self._unavailable(f"{type(exc).__name__}: {exc}", transport=True) from exc
        if not reply.get("ok"):
            raise self._unavailable(
                f"{reply.get('error', 'error')}: {reply.get('message', 'request refused')}",
                transport=False,
            )
        return reply, payload

    def ping(self) -> dict:
        """The service's status: model, limits and counters."""
        reply, _ = self._request({"v": PROTOCOL_VERSION, "op": "ping"})
        return reply

    def embed(self, texts: list):
        """Vectors for ``texts``, in order: a float32 ``(n, dim)`` array."""
        import numpy as np

        parts = [self._embed_chunk(chunk, np) for chunk in _chunks(texts)]
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    def _embed_chunk(self, texts: list, np):
        reply, payload = self._request(
            {"v": PROTOCOL_VERSION, "op": "embed", "model": self.model, "texts": texts}
        )
        n, dim = reply.get("n"), reply.get("dim")
        if reply.get("model") != self.model or n != len(texts) or len(payload) != n * dim * 4:
            raise self._unavailable("the reply does not match the request", transport=False)
        # A copy, so callers get a writable array like the in-process one.
        return np.frombuffer(payload, dtype="<f4").reshape(n, dim).astype(np.float32)


def _chunks(texts: list):
    chunk, size = [], 0
    for text in texts:
        text_size = len(text) * 4 if isinstance(text, str) else 0
        if chunk and (len(chunk) >= CLIENT_MAX_TEXTS or size + text_size > CLIENT_MAX_BYTES):
            yield chunk
            chunk, size = [], 0
        chunk.append(text)
        size += text_size
    if chunk:
        yield chunk


def _client_timeout() -> float:
    raw = os.environ.get("MEMPALACE_EMBEDDING_SERVICE_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def service_embedding_function(local_class, address: str, model: str, **init):
    """An embedding function of ``local_class`` that embeds through the service.

    A subclass, not a stand-in: its name, config and everything else ChromaDB
    reads or persists are the in-process class's own, so a palace cannot
    tell which one wrote it and the switch can be turned off again. Only
    ``__call__`` differs, and the model is never loaded in this process.
    """

    class ServiceEmbeddingFunction(local_class):
        service_client = EmbeddingServiceClient(address, model)

        def __call__(self, input):  # noqa: A002 — ChromaDB EF protocol
            from .embedding import as_text_batch

            texts = as_text_batch(input)
            if not texts:
                return []
            vectors = self.service_client.embed(texts)
            # Each model's in-process return type: arrays for MiniLM (ChromaDB
            # normalizes them), lists of floats for embeddinggemma.
            return vectors if model == "minilm" else vectors.tolist()

    return ServiceEmbeddingFunction(**init)


# ── service ──────────────────────────────────────────────────────────────


def _rss_mib() -> Optional[int]:
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


class EmbeddingService:
    """Holds one embedding model and serves it on a Unix domain socket."""

    def __init__(
        self,
        socket_path,
        *,
        model: Optional[str] = None,
        device: Optional[str] = None,
        embedder=None,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        max_texts: int = DEFAULT_MAX_TEXTS,
        io_timeout: float = DEFAULT_IO_TIMEOUT_S,
    ):
        from .embedding import current_model_name

        for name, value in (
            ("max_connections", max_connections),
            ("max_concurrent", max_concurrent),
            ("max_request_bytes", max_request_bytes),
            ("max_texts", max_texts),
        ):
            if value < 1:
                raise ValueError(f"{name} must be >= 1, got {value}")
        self.socket_path = str(socket_path)
        self.model = current_model_name(model)
        if self.model not in SERVICE_MODELS:
            raise ValueError(
                f"The embedding service serves {', '.join(sorted(SERVICE_MODELS))}; "
                f"embedding_model is {self.model!r}, which stays in-process."
            )
        self.device = device
        self.max_connections = max_connections
        self.max_concurrent = max_concurrent
        self.max_request_bytes = max_request_bytes
        self.max_texts = max_texts
        self.io_timeout = io_timeout
        self._embedder = embedder
        self._dimension = 0
        self._listener = None
        self._accept_thread = None
        self._closed = threading.Event()
        self._stop_requested = threading.Event()
        self._connections = threading.BoundedSemaphore(max_connections)
        self._inference = threading.BoundedSemaphore(max_concurrent)
        self._stats_lock = threading.Lock()
        self._stats = {"model_loads": 0, "requests": 0, "texts": 0, "errors": 0}
        self._started = time.time()

    # lifecycle

    def start(self) -> "EmbeddingService":
        """Load the model, then listen. Returns once requests can be served."""
        if self._listener is not None:
            return self
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("The embedding service needs Unix domain sockets.")
        self._load()
        self._listener = self._bind()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="embedding-service-accept", daemon=True
        )
        self._accept_thread.start()
        logger.info(
            "Embedding service ready (model=%s dim=%d socket=%s max_concurrent=%d "
            "max_connections=%d max_request_bytes=%d max_texts=%d)",
            self.model,
            self._dimension,
            self.socket_path,
            self.max_concurrent,
            self.max_connections,
            self.max_request_bytes,
            self.max_texts,
        )
        return self

    def _load(self) -> None:
        if self._embedder is None:
            from .embedding import get_embedding_function

            # local=True: this process is the service, whatever the switch says.
            self._embedder = get_embedding_function(
                device=self.device, model=self.model, local=True
            )
        # A probe loads the model now, so "ready" means ready and a broken
        # model stops the start instead of the first caller.
        self._dimension = len(self._embedder(input=["probe"])[0])
        self._stats["model_loads"] += 1

    def _bind(self):
        path = Path(self.socket_path)
        if len(os.fsencode(self.socket_path)) > 100:
            raise ValueError(f"socket path is too long for a Unix socket: {self.socket_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise RuntimeError(f"{path} exists and is not a socket; refusing to replace it")
            try:
                EmbeddingServiceClient(self.socket_path, self.model, timeout=2.0).ping()
            except EmbeddingServiceUnavailableError as exc:
                if "no service is listening" not in exc.reason:
                    raise RuntimeError(f"{path} is in use: {exc.reason}") from exc
                path.unlink()  # left behind by a service that is gone
            else:
                raise RuntimeError(f"An embedding service is already listening on {path}")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Owner-only from the moment it exists.
            previous = os.umask(0o177)
            try:
                listener.bind(self.socket_path)
            finally:
                os.umask(previous)
            listener.listen(64)
            # A blocking accept is not reliably woken by close(); poll instead.
            listener.settimeout(0.2)
        except BaseException:
            listener.close()
            raise
        return listener

    def serve_forever(self) -> None:
        """Block until :meth:`request_stop` or :meth:`close`."""
        self._stop_requested.wait()

    def request_stop(self) -> None:
        """Wake :meth:`serve_forever`. Safe to call from a signal handler."""
        self._stop_requested.set()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._stop_requested.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
            with contextlib.suppress(OSError):
                os.unlink(self.socket_path)
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=5)

    def __enter__(self) -> "EmbeddingService":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)

    # serving

    def _accept_loop(self) -> None:
        listener = self._listener
        while not self._closed.is_set():
            # Take a slot before accepting: past max_connections, callers
            # wait in the kernel's backlog instead of in threads and buffers.
            if not self._connections.acquire(timeout=0.2):
                continue
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                self._connections.release()
                continue
            except OSError:
                self._connections.release()
                if self._closed.is_set():
                    return
                logger.warning("Embedding service accept failed", exc_info=True)
                time.sleep(0.1)
                continue
            threading.Thread(
                target=self._serve_connection,
                args=(conn,),
                name="embedding-service-request",
                daemon=True,
            ).start()

    def _serve_connection(self, conn) -> None:
        try:
            with conn:
                conn.settimeout(self.io_timeout)
                if not _same_user(conn):
                    return
                try:
                    request = _recv_message(conn, self.max_request_bytes)
                except _TooLarge as exc:
                    self._refuse(
                        conn,
                        "too_large",
                        f"request of {exc.size} bytes is over the service limit of "
                        f"{self.max_request_bytes}",
                    )
                    return
                reply, payload = self._handle(request)
                _send_message(conn, reply, payload)
        except (OSError, ValueError):
            # A caller that went away or sent garbage; nothing to answer.
            self._count("errors")
        finally:
            self._connections.release()

    def _refuse(self, conn, error: str, message: str) -> None:
        self._count("errors")
        with contextlib.suppress(OSError):
            _send_message(conn, {"ok": False, "error": error, "message": message})

    def _count(self, key: str, by: int = 1) -> None:
        with self._stats_lock:
            self._stats[key] += by

    def _handle(self, request: dict):
        self._count("requests")
        if request.get("v") != PROTOCOL_VERSION:
            return self._error("protocol", f"this service speaks protocol {PROTOCOL_VERSION}")
        op = request.get("op")
        if op == "ping":
            return {
                "ok": True,
                "v": PROTOCOL_VERSION,
                "model": self.model,
                "dim": self._dimension,
                "pid": os.getpid(),
                "rss_mib": _rss_mib(),
                "uptime_s": int(time.time() - self._started),
                "limits": {
                    "max_connections": self.max_connections,
                    "max_concurrent": self.max_concurrent,
                    "max_request_bytes": self.max_request_bytes,
                    "max_texts": self.max_texts,
                },
                "stats": self.stats(),
            }, b""
        if op != "embed":
            return self._error("protocol", f"unknown op {op!r}")
        if request.get("model") != self.model:
            # Never embed with a model the caller did not ask for.
            return self._error(
                "model_not_served",
                f"this service serves {self.model!r}, not {request.get('model')!r}",
            )
        texts = request.get("texts")
        if not isinstance(texts, list) or not all(isinstance(text, str) for text in texts):
            return self._error("protocol", "texts must be a list of strings")
        if len(texts) > self.max_texts:
            return self._error(
                "too_large", f"{len(texts)} texts is over the service limit of {self.max_texts}"
            )
        if not texts:
            return {"ok": True, "model": self.model, "n": 0, "dim": self._dimension}, b""
        try:
            payload, dim = self._embed(texts)
        except Exception as exc:
            # The exception type only: a message could quote the caller's text.
            logger.error("Embedding failed in the service: %s", type(exc).__name__)
            return self._error("embed_failed", f"the model raised {type(exc).__name__}")
        self._count("texts", len(texts))
        return {
            "ok": True,
            "model": self.model,
            "n": len(texts),
            "dim": dim,
            "payload_bytes": len(payload),
        }, payload

    def _error(self, error: str, message: str):
        self._count("errors")
        return {"ok": False, "error": error, "message": message}, b""

    def _embed(self, texts: list):
        import numpy as np

        with self._inference:
            vectors = self._embedder(input=texts)
        array = np.asarray(vectors, dtype="<f4")
        if array.ndim != 2 or array.shape[0] != len(texts):
            raise RuntimeError("the model returned a malformed batch")
        return array.tobytes(), int(array.shape[1])


def _same_user(conn) -> bool:
    """Whether the peer runs as this user (where the platform can tell)."""
    peercred = getattr(socket, "SO_PEERCRED", None)
    if peercred is None:
        return True  # the 0600 socket file is the gate
    try:
        creds = conn.getsockopt(socket.SOL_SOCKET, peercred, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
    except OSError:
        return False
    return uid == os.geteuid()


def main(argv=None) -> int:
    import argparse
    import signal

    parser = argparse.ArgumentParser(
        prog="python -m mempalace.embedding_service",
        description="Serve the configured local embedding model to every MemPalace "
        "process on this machine over a Unix domain socket.",
    )
    parser.add_argument(
        "--socket",
        help="socket path (default: embedding_service from the config, "
        f"else {DEFAULT_SOCKET_NAME} in the config directory)",
    )
    parser.add_argument("--model", help="minilm or embeddinggemma (default: embedding_model)")
    parser.add_argument("--device", help="embedding device (default: embedding_device)")
    parser.add_argument("--max-concurrent", type=int, default=DEFAULT_MAX_CONCURRENT)
    parser.add_argument("--max-connections", type=int, default=DEFAULT_MAX_CONNECTIONS)
    parser.add_argument("--max-request-bytes", type=int, default=DEFAULT_MAX_REQUEST_BYTES)
    parser.add_argument("--max-texts", type=int, default=DEFAULT_MAX_TEXTS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        from .config import MempalaceConfig

        socket_path = args.socket or configured_address()
        if socket_path is None:
            socket_path = str(Path(MempalaceConfig().config_dir) / DEFAULT_SOCKET_NAME)
        service = EmbeddingService(
            socket_path,
            model=args.model,
            device=args.device,
            max_connections=args.max_connections,
            max_concurrent=args.max_concurrent,
            max_request_bytes=args.max_request_bytes,
            max_texts=args.max_texts,
        )
        service.start()
    except Exception as exc:
        logger.error("Embedding service did not start: %s", exc)
        return 1

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: service.request_stop())
    try:
        service.serve_forever()
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
