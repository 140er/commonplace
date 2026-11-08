"""Generation facts and response completion, without retirement policy.

The parent owns recovery; a child owns admission until the last response has
been written. Private pipes carry liveness and at most three bounded facts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid

CHILD_ENV = "MEMPALACE_HTTP_CHILD"
GENERATION_ENV = "MEMPALACE_HTTP_GENERATION"
LIVENESS_ENV = "MEMPALACE_HTTP_LIVENESS"
FACTS_ENV = "MEMPALACE_HTTP_FACTS"
GENERATION_HEADER = "X-Mempalace-Generation"
STATE_HEADER = "X-Mempalace-State"
ADMITTED_HEADER = "X-Mempalace-Admitted"
DISPATCH_HEADER = "X-Mempalace-Dispatch"
REASON_HEADER = "X-Mempalace-Reason"


class RestartCircuit:
    """Constant-space circuit retained by the restart owner across children."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.failures = 0
        self.until = 0.0

    def failed(self):
        self.failures += 1
        if self.failures >= 3:
            self.until = self.clock() + 60.0

    def healthy(self, completed):
        if completed is not None and completed >= 16:
            self.failures = 0
            self.until = 0.0

    def available(self):
        return self.clock() >= self.until

    def ended(self, terminal, completed):
        self.healthy(completed)
        if terminal is None:
            self.failed()
        elif terminal["reason"] == "rss" and terminal["admitted"] <= 1:
            self.failed()


def process_identity(pid):
    """Kernel start identity on supported platforms; never substitute a PID."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            from ._proc import kernel32, pid_alive

            if not pid_alive(pid):
                return None
            kernel = kernel32()
            kernel.GetProcessTimes.argtypes = (
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
            )
            kernel.GetProcessTimes.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                creation, end, system, user = (wintypes.FILETIME() for _ in range(4))
                if not kernel.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(end),
                    ctypes.byref(system),
                    ctypes.byref(user),
                ):
                    return None
                return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            finally:
                kernel.CloseHandle(handle)
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/stat", "rb") as stream:
                raw = stream.read(4096)
            fields = raw[raw.rfind(b")") + 2 :].split()
            return None if fields[0] in (b"Z", b"X") else int(fields[19])
        value = (
            subprocess.check_output(["ps", "-o", "lstart=", "-p", str(pid)], timeout=1)
            .decode()
            .strip()
        )
        return value or None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def identity_alive(pid, identity):
    return identity is not None and process_identity(pid) == identity


def valid_generation(value):
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


class Coordinator:
    """Hold admission through response flush; EOF never cuts an accepted call."""

    def __init__(
        self, *, generation=None, fact=lambda record: None, cleanup=lambda: None, exit=lambda: None
    ):
        self.generation = generation or str(uuid.uuid4())
        self.fact = fact
        self.cleanup = cleanup
        self.exit = exit
        self.lock = threading.RLock()
        self.active = 0
        self.admitted = 0
        self.completed = 0
        self.reason = None
        self.finished = False
        self.progress_sent = False

    def acquire(self):
        with self.lock:
            if self.reason is not None:
                return False
            self.active += 1
            return True

    def admit(self):
        with self.lock:
            if self.reason is not None:
                return False
            self.admitted += 1
            return True

    def headers(self):
        with self.lock:
            return {
                GENERATION_HEADER: self.generation,
                STATE_HEADER: "DRAINING" if self.reason else "RUNNING",
                ADMITTED_HEADER: str(self.admitted),
            }

    def _emit(self, kind, **fields):
        self.fact({"v": 1, "kind": kind, "generation": self.generation, **fields})

    def ready(self):
        self._emit("ready")

    def release(self, *, storage=False, completed=False):
        with self.lock:
            self.active -= 1
            if storage and completed:
                self.completed += 1
                if self.completed >= 16 and not self.progress_sent:
                    self.progress_sent = True
                    self._emit("completed_16")
            finish = self.reason is not None and self.active == 0
        if finish:
            self._finish()

    def drain(self, reason="parent_lost"):
        with self.lock:
            if self.reason is None:
                self.reason = reason
            finish = self.active == 0
        if finish:
            self._finish()

    def _finish(self):
        with self.lock:
            if self.finished:
                return
            self.finished = True
            self._emit(
                "retiring",
                admitted=self.admitted,
                completed=self.completed,
                reason=self.reason,
                rss_source="unknown",
            )
        self.cleanup()
        self.exit()


class ControlPipes:
    """Only child read/write ends are explicitly inherited (also on Windows)."""

    def __init__(self, generation=None):
        self.generation = generation or str(uuid.uuid4())
        self.live_read, self.live_write = os.pipe()
        self.fact_read, self.fact_write = os.pipe()
        self.ready = False
        self.completed = None
        self.terminal = None
        self.lock = threading.Lock()
        self.reader = None
        self._closed = False

    def launch_options(self, env):
        env = dict(env)
        env.update({CHILD_ENV: "1", GENERATION_ENV: self.generation})
        if os.name == "nt":
            import msvcrt

            handles = [msvcrt.get_osfhandle(fd) for fd in (self.live_read, self.fact_write)]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.lpAttributeList = {"handle_list": handles}
            env.update({LIVENESS_ENV: str(handles[0]), FACTS_ENV: str(handles[1])})
            return {"env": env, "startupinfo": startupinfo, "close_fds": True}
        env.update({LIVENESS_ENV: str(self.live_read), FACTS_ENV: str(self.fact_write)})
        return {"env": env, "pass_fds": (self.live_read, self.fact_write)}

    def launched(self):
        for name in ("live_read", "fact_write"):
            os.close(getattr(self, name))
            setattr(self, name, None)
        self.reader = threading.Thread(target=self._read, name="worker-facts", daemon=True)
        self.reader.start()

    def _read(self):
        # Never retain a log/history, and never allocate an unbounded line.
        with os.fdopen(self.fact_read, "rb", buffering=0) as stream:
            while True:
                raw = stream.readline(513)
                if not raw:
                    return
                if len(raw) > 512 or not raw.endswith(b"\n"):
                    return
                try:
                    record = json.loads(raw)
                except (ValueError, UnicodeError):
                    return
                if not self.accept(record):
                    return

    def accept(self, record):
        if not isinstance(record, dict) or record.get("v") != 1:
            return False
        if record.get("generation") != self.generation:
            return False
        kind = record.get("kind")
        base = {"v", "kind", "generation"}
        with self.lock:
            if kind in {"ready", "completed_16"} and set(record) == base:
                if kind == "ready":
                    self.ready = True
                else:
                    self.completed = max(self.completed or 0, 16)
                return True
            if kind != "retiring" or set(record) != base | {
                "admitted",
                "completed",
                "reason",
                "rss_source",
            }:
                return False
            if any(
                type(record[k]) is not int or not 0 <= record[k] < 2**63
                for k in ("admitted", "completed")
            ):
                return False
            if record["completed"] > record["admitted"]:
                return False
            if record["reason"] not in {"parent_lost", "shutdown", "count", "rss", "idle"}:
                return False
            if record["rss_source"] not in {"unknown", "proc", "psutil", "unsupported"}:
                return False
            self.terminal = record
            self.completed = record["completed"]
            return True

    def drain(self):
        with self.lock:
            if self.live_write is not None:
                os.close(self.live_write)
                self.live_write = None

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.drain()
        if self.reader is not None:
            self.reader.join(5)
        else:
            os.close(self.fact_read)
        for name in ("live_read", "fact_write"):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)


def child_coordinator(*, cleanup, exit):
    """Adopt explicit pipe ends; no other inherited end can hide parent EOF."""
    generation = os.environ.get(GENERATION_ENV)
    if not valid_generation(generation):
        generation = None
    live_fd = os.environ.pop(LIVENESS_ENV, None)
    fact_fd = os.environ.pop(FACTS_ENV, None)
    if os.name == "nt":
        import msvcrt

        if live_fd is not None:
            live_fd = msvcrt.open_osfhandle(int(live_fd), os.O_RDONLY | os.O_BINARY)
        if fact_fd is not None:
            fact_fd = msvcrt.open_osfhandle(int(fact_fd), os.O_WRONLY | os.O_BINARY)
    else:
        live_fd = int(live_fd) if live_fd is not None else None
        fact_fd = int(fact_fd) if fact_fd is not None else None
    if fact_fd is not None:
        try:
            os.set_blocking(fact_fd, False)
        except OSError:
            os.close(fact_fd)
            fact_fd = None

    def fact(record):
        if fact_fd is None:
            return
        raw = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        if len(raw) <= 512:
            try:
                os.write(fact_fd, raw)
            except OSError:
                pass

    coordinator = Coordinator(generation=generation, fact=fact, cleanup=cleanup, exit=exit)
    if live_fd is not None:

        def watch():
            try:
                with os.fdopen(live_fd, "rb", buffering=0) as stream:
                    while stream.read(1):
                        pass
            finally:
                coordinator.drain("parent_lost")

        threading.Thread(target=watch, name="monitor-liveness", daemon=True).start()
    return coordinator


def proven_refusal(status, getheader, payload, generation):
    """An arbitrary HTTP 503 or a partial body never proves no dispatch."""
    if status != 503 or not valid_generation(generation):
        return None
    reason = getheader(REASON_HEADER)
    if (
        getheader(GENERATION_HEADER) != generation
        or getheader(DISPATCH_HEADER) != "not-started"
        or reason not in {"recycling", "busy"}
    ):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict) or error.get("code") != -32071:
        return None
    data = error.get("data")
    if (
        not isinstance(data, dict)
        or data.get("dispatch_started") is not False
        or data != {"dispatch_started": False, "generation": generation, "reason": reason}
    ):
        return None
    return reason
