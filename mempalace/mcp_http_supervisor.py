"""Storage-free HTTP generation owner with a timer-driven recovery circuit."""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from functools import partial

from . import server_registry
from .worker_lifecycle import ControlPipes, RestartCircuit, process_identity

logger = logging.getLogger(__name__)


class Child:
    def __init__(self, command, env):
        self.control = ControlPipes()
        try:
            self.process = subprocess.Popen(command, **self.control.launch_options(env))
        except BaseException:
            self.control.close()
            raise
        self.pid = self.process.pid
        self.start_identity = process_identity(self.pid)
        self.control.launched()

    @property
    def ready(self):
        return self.control.ready

    @property
    def completed(self):
        return self.control.completed

    @property
    def terminal(self):
        return self.control.terminal

    def poll(self):
        return self.process.poll()

    def wait(self, timeout=None):
        result = self.process.wait(timeout=timeout)
        self.control.close()
        return result

    def drain(self):
        self.control.drain()

    def close(self):
        self.control.close()


class HTTPMonitor:
    """One current child and constant-space facts, never a request history.

    step() is the production state machine and deterministic clock/launcher
    seam. run() only supplies wakeups and the lifetime start-serialization lock.
    """

    def __init__(
        self,
        *,
        launcher,
        palace_path,
        clock=time.monotonic,
        publish_marker=None,
        startup_timeout_s=120.0,
    ):
        self.launcher = launcher
        self.palace_path = palace_path
        self.clock = clock
        self.publish_marker = publish_marker or partial(
            server_registry.write_monitor_marker, palace_path
        )
        self.startup_timeout_s = startup_timeout_s
        self.circuit = RestartCircuit(clock)
        self.child = None
        self.deadline = None
        self.next_check = 0.0
        self.stopping = False
        self.wake = threading.Event()
        self.state = None
        self.startup_failed = False
        self.ever_ready = False
        self.marker_failed = False

    def publish(self, state):
        if state == self.state:
            return True
        try:
            self.publish_marker(state, self.child)
        except OSError:
            if not self.marker_failed:
                logger.warning("HTTP monitor marker unavailable; publication will retry")
            self.marker_failed = True
            return False
        if self.marker_failed:
            logger.info("HTTP monitor marker publication recovered")
        self.marker_failed = False
        if self.state == "cooldown" and state == "restarting":
            logger.info("HTTP monitor recovery probe starting")
        logger.info("HTTP monitor state: %s", state)
        self.state = state
        return True

    def _reap_child(self, child):
        status = child.wait()
        # wait joins the fact reader before the final classification.
        self.ever_ready = self.ever_ready or child.ready
        self.child = None
        self.deadline = None
        if self.stopping:
            return
        if not self.ever_ready and status in (1, 2):
            logger.error(
                "HTTP first generation failed before ready; exiting with status %s", status
            )
            raise SystemExit(status)
        if not self.startup_failed:
            self.circuit.ended(child.terminal, child.completed)

    def step(self):
        now = self.clock()
        child = self.child
        if child is not None:
            self.circuit.healthy(child.completed)
            if child.poll() is not None:
                self._reap_child(child)
            else:
                if self.stopping:
                    child.drain()
                    self.publish("draining")
                elif child.terminal is not None:
                    self.publish("draining")
                elif child.ready:
                    self.ever_ready = True
                    self.deadline = None
                    self.publish("running")
                elif self.deadline is not None and now >= self.deadline:
                    if not self.startup_failed:
                        self.circuit.failed()
                        self.startup_failed = True
                    child.drain()
                    self.publish("draining")
                return
        if self.stopping:
            return
        if now < self.next_check:
            return
        orphan = server_registry.live_child_record(self.palace_path)
        if orphan is not None:
            self.publish("orphan_draining")
            self.next_check = now + 60.0
            return
        if not self.circuit.available():
            self.publish("cooldown")
            return
        if not self.publish("restarting"):
            return
        self.startup_failed = False
        try:
            self.child = self.launcher()
            self.deadline = self.clock() + self.startup_timeout_s
        except Exception:
            self.circuit.failed()
            # Avoid spinning on startup failure before the three-failure latch.
            self.next_check = now + (60.0 if self.circuit.failures >= 3 else 0.1)
            self.publish("cooldown" if self.circuit.failures >= 3 else "starting")
        else:
            # Always republish with the current child's identity.
            self.state = None
            self.ever_ready = self.ever_ready or self.child.ready
            self.publish("running" if self.child.ready else "starting")

    def stop(self):
        self.stopping = True
        if self.child is not None:
            self.child.drain()
        self.wake.set()

    def _signal_stop(self, *_):
        # A signal can interrupt the main thread while any lock is held.
        # Even Event.set() takes a lock: only assign the flag here.
        self.stopping = True

    def run(self):
        from filelock import FileLock, Timeout

        directory = server_registry.server_state_dir(self.palace_path)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = FileLock(str(directory / "monitor.lock"))
        try:
            lease = lock.acquire(timeout=0)
        except Timeout:
            logger.error("A palace HTTP monitor is already running; refusing a second monitor")
            raise SystemExit(2) from None
        with lease:
            previous = {}
            if threading.current_thread() is threading.main_thread():
                for name in ("SIGTERM", "SIGINT", "SIGHUP"):
                    sig = getattr(signal, name, None)
                    if sig is not None:
                        previous[sig] = signal.signal(sig, self._signal_stop)
            try:
                while not self.stopping or self.child is not None:
                    self.step()
                    self.wake.wait(0.1)
                    self.wake.clear()
            finally:
                self.stop()
                if self.child is not None:
                    # Do not force-kill an accepted mutation, including on TERM.
                    self.child.wait()
                    self.child = None
                server_registry.clear_monitor_marker(self.palace_path)
                for sig, handler in previous.items():
                    signal.signal(sig, handler)


def make_monitor(*, palace_path, host, port):
    # Resolve an ephemeral requested endpoint once, rather than per generation.
    if port == 0:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            listener.bind((host, 0))
            port = listener.getsockname()[1]
    argv = list(sys.argv[1:])
    if "--port" in argv:
        index = argv.index("--port")
        argv[index + 1] = str(port)
    else:
        argv += ["--port", str(port)]
    command = [sys.executable, "-m", "mempalace.mcp_server", *argv]
    env = dict(os.environ)
    return HTTPMonitor(
        launcher=lambda: Child(command, env),
        palace_path=palace_path,
        publish_marker=partial(
            server_registry.write_monitor_marker,
            palace_path,
            host=host,
            port=port,
            scheme="https" if "--tls-cert" in argv else "http",
        ),
    )
