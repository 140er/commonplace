"""Disabled-by-default, explicit-key, two-hop Compute SSH adapter.

Public seams: EphemeralKey, ParamikoDriver, BudgetedChannel, ExecutionResult and
SSHTransport.execute. Parent dependency action: add an optional Compute extra
containing Paramiko (lock/validate a maintained supported release).
Paramiko is imported only when the explicitly enabled adapter runs. No SSHClient,
SSH config, known_hosts, agent, filesystem key, password, interactive auth, direct
target socket, shell, PTY, alternate driver or retry path is used.

``authorize(request, phase) is True`` is required before connecting, immediately
after ready (phase ``dispatch``), and before target authentication and execution.
The controller consumes exact approval before connecting and checks current
service authority under its participating local fence. Explicit wire mode also
requires a helper challenge and a durable nonce/channel-bound controller permit;
no local fence is held during network I/O. A callback alone grants no authority.
Provide only service-fetched immutable enrollments and fresh explicit keys; no public HTTP/tool integration is registered here.

Results contain bounded raw remote bytes for an authorized background consumer.
They are NOT safe UI/log output; parent owns redaction and audience fencing before
persistence/publication. Missing exit evidence, deadline, disconnect, failed exec
ack or cancellation after go are uncertain; channel closure never proves kill.
Synthetic driver tests establish ordering, not deployed two-hop SSH acceptance.
"""

from __future__ import annotations

import io
import math
import socket
import threading
import time
from dataclasses import dataclass, field

from .protocol import (
    ADMISSION_MODE,
    AdmissionChallenge,
    EnrolledRoute,
    ExecutionRequest,
    FrameIO,
    GatewayEnrollment,
    Limits,
    ProtocolDenied,
    compile_operation,
    finite,
    identifier,
    positive_integer,
    validate_admission,
)


class EphemeralKey:
    """One-use in-memory Ed25519 PEM, obtained explicitly from trusted custody.

    No file path and no implicit loading. Transport consumes the bytes only after
    pin verification. This hides repr/pickling, not Python memory inspection or
    zeroization. Do not put keys in request/event/result objects.
    """

    __slots__ = ("_material", "_lock", "_credential_id")

    def __init__(self, private_bytes, *, credential_id):
        if type(private_bytes) is not bytes or not 1 <= len(private_bytes) <= 16384:
            raise ProtocolDenied("credential_unavailable")
        self._material = private_bytes
        self._lock = threading.Lock()
        self._credential_id = identifier(credential_id)

    @property
    def credential_id(self):
        return self._credential_id

    @property
    def available(self):
        with self._lock:
            return self._material is not None

    def __repr__(self):
        return "<EphemeralKey>"

    def __reduce__(self):
        raise TypeError("credential_not_serializable")

    def _take_for_transport(self):
        with self._lock:
            if self._material is None:
                raise ProtocolDenied("credential_unavailable")
            material, self._material = self._material, None
            return material

    def discard(self):
        with self._lock:
            self._material = None


class ParamikoDriver:
    """Low-level transport: handshake, compare full pin, then one public-key auth.

    ``module`` is a synthetic test seam, never an automatic alternative driver.
    The only socket creator is connect_gateway, using an enrolled numeric address.
    Target SSH always uses the channel passed to handshake.
    """

    def __init__(self, module=None):
        self._module = module

    def _library(self):
        if self._module is None:
            try:
                import paramiko
            except ImportError:
                raise ProtocolDenied("ssh_dependency_unavailable") from None
            self._module = paramiko
        return self._module

    def connect_gateway(self, gateway, timeout):
        if not isinstance(gateway, GatewayEnrollment):
            raise ProtocolDenied("invalid_execution")
        self._library()  # Missing optional support denies before opening a socket.
        family = socket.AF_INET6 if ":" in gateway.address else socket.AF_INET
        channel = socket.socket(family, socket.SOCK_STREAM)
        try:
            channel.settimeout(timeout)
            channel.connect((gateway.address, gateway.port))
            return channel
        except BaseException:
            channel.close()
            raise

    def handshake(self, channel, pin, hop, timeout):
        library = self._library()
        transport = library.Transport(channel)
        try:
            transport.banner_timeout = timeout
            transport.auth_timeout = timeout
            transport.channel_timeout = timeout
            transport.start_client(timeout=timeout)
            if not transport.is_active():
                raise ProtocolDenied("ssh_handshake_failed")
            pin.verify(transport.get_remote_server_key(), hop)
            return transport
        except BaseException:
            # Teardown must not replace an independently detected pin failure.
            try:
                transport.close()
            except Exception:
                pass
            raise

    def authenticate(self, transport, account, key, timeout):
        transport.auth_timeout = timeout
        material = key._take_for_transport()
        try:
            private_key = self._library().Ed25519Key.from_private_key(
                io.StringIO(material.decode("ascii"))
            )
            # auth_publickey performs no password/agent/keyboard-interactive fallback.
            transport.auth_publickey(account, private_key)
            if not transport.is_authenticated():
                raise ProtocolDenied("ssh_authentication_failed")
        except ProtocolDenied:
            raise
        except Exception:
            raise ProtocolDenied("ssh_authentication_failed") from None
        finally:
            del material

    def helper(self, transport, timeout):
        channel = transport.open_session(timeout=timeout)
        try:
            channel.settimeout(timeout)
            self._request(
                channel, lambda: channel.invoke_subsystem("commonplace-compute-v1"), timeout
            )
            return channel
        except BaseException:
            channel.close()
            raise

    def command(self, transport, command, timeout):
        if type(command) is not str or command != compile_operation("system.info.v1", {}):
            raise ProtocolDenied("unsupported_operation")
        channel = transport.open_session(timeout=timeout)
        try:
            channel.settimeout(timeout)
            self._request(channel, lambda: channel.exec_command(command), timeout)
            channel.shutdown_write()
            return channel
        except BaseException:
            channel.close()
            raise

    def script_command(self, transport, timeout):
        """Fixed bidirectional helper channel; unlike a probe stdin stays open."""
        from .target_helper import TARGET_HELPER_COMMAND

        channel = transport.open_session(timeout=timeout)
        try:
            channel.settimeout(timeout)
            self._request(channel, lambda: channel.exec_command(TARGET_HELPER_COMMAND), timeout)
            return channel
        except BaseException:
            channel.close()
            raise

    @staticmethod
    def _request(channel, request, timeout):
        expired = threading.Event()

        def expire():
            expired.set()
            channel.close()

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            request()
            if expired.is_set():
                raise ProtocolDenied("deadline_exceeded")
        except Exception:
            if expired.is_set():
                raise ProtocolDenied("deadline_exceeded") from None
            raise
        finally:
            timer.cancel()


class BudgetedChannel:
    """Socket-like wrapper accounting nested SSH ciphertext plus control frames.

    Both directions share one byte ceiling and monotonic deadline. Inner SSH may
    shorten, never extend, that deadline. Bytes offered for a send count before
    I/O, including ambiguous sends. Closing requests cancellation, not remote kill.
    """

    def __init__(self, channel, byte_limit, deadline, *, monotonic=time.monotonic):
        positive_integer(byte_limit, 1048576)
        finite(deadline)
        self.channel = channel
        self.byte_limit, self.deadline, self.monotonic = byte_limit, deadline, monotonic
        self.used = 0
        self.timeout = None
        self.closed = False
        self.failure_reason = None
        self._lock = threading.Lock()

    def _prepare(self, offered=0):
        with self._lock:
            remaining = self.deadline - self.monotonic()
            if self.closed:
                raise OSError("tunnel_closed")
            if remaining <= 0:
                self.failure_reason = self.failure_reason or "deadline_exceeded"
                self.close()
                raise ProtocolDenied("deadline_exceeded")
            if self.used + offered > self.byte_limit:
                self.failure_reason = "tunnel_budget_exceeded"
                self.close()
                raise ProtocolDenied("tunnel_budget_exceeded")
            self.used += offered
            timeout = remaining if self.timeout is None else min(remaining, self.timeout)
            self.channel.settimeout(timeout)

    def settimeout(self, timeout):
        if timeout is not None and (
            type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0
        ):
            raise ProtocolDenied("invalid_timeout")
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def send(self, data):
        self._prepare(len(data))
        sent = self.channel.send(data)
        self._prepare()
        return sent

    def sendall(self, data):
        self._prepare(len(data))
        self.channel.sendall(data)
        self._prepare()

    def recv(self, size):
        self._prepare()
        with self._lock:
            count = min(size, max(1, self.byte_limit - self.used + 1))
        data = self.channel.recv(count)
        self._prepare(len(data))
        return data

    def close(self):
        self.closed = True
        self.channel.close()


@dataclass(frozen=True)
class ExecutionResult:
    state: str
    reason: str
    exit_code: int | None = None
    stdout: bytes = field(default=b"", repr=False)
    stderr: bytes = field(default=b"", repr=False)
    cancellation_requested: bool = False
    tunnel_bytes: int = 0
    script_result: object = field(default=None, repr=False)


class SSHTransport:
    """Execute a single authorized fixed probe through the enrolled helper.

    Explicit enablement is for parent-controlled integration, not a service grant.
    Reusing an execution is never retried automatically. ``cancelled`` is a local
    threading.Event-like object; it does not assert remote termination.
    """

    def __init__(
        self,
        authorize,
        *,
        enabled=False,
        limits=None,
        driver=None,
        wall=time.time,
        monotonic=time.monotonic,
        admission_mode=None,
        gateway_admission=None,
    ):
        limits = Limits() if limits is None else limits
        if not callable(authorize) or not isinstance(limits, Limits):
            raise ProtocolDenied("invalid_transport_configuration")
        if (
            admission_mode not in (None, ADMISSION_MODE)
            or ((admission_mode is None) != (gateway_admission is None))
            or (gateway_admission is not None and not callable(gateway_admission))
        ):
            raise ProtocolDenied("invalid_admission_configuration")
        self.admission_mode, self.gateway_admission = admission_mode, gateway_admission
        self.authorize, self.enabled, self.limits = authorize, enabled is True, limits
        self.driver = driver if driver is not None else ParamikoDriver()
        self.wall, self.monotonic = wall, monotonic

    def _authorize(self, request, phase, cancelled):
        if cancelled is not None and cancelled.is_set():
            raise ProtocolDenied("cancellation_requested")
        try:
            allowed = self.authorize(request, phase)
        except Exception:
            raise ProtocolDenied("authority_unavailable") from None
        if allowed is not True:
            raise ProtocolDenied("authority_denied")

    def execute(
        self,
        request,
        gateway,
        route,
        gateway_key,
        target_key,
        *,
        cancelled=None,
        script_request=None,
        script=None,
    ):
        if not self.enabled:
            raise ProtocolDenied("transport_disabled")
        if (
            not isinstance(request, ExecutionRequest)
            or not isinstance(gateway, GatewayEnrollment)
            or not isinstance(route, EnrolledRoute)
        ):
            raise ProtocolDenied("invalid_execution")
        if (
            not isinstance(gateway_key, EphemeralKey)
            or not isinstance(target_key, EphemeralKey)
            or gateway_key is target_key
            or gateway_key.credential_id != gateway.credential_id
            or target_key.credential_id != route.credential_id
            or not gateway_key.available
            or not target_key.available
        ):
            raise ProtocolDenied("credential_unavailable")
        if request.admission_mode != self.admission_mode:
            raise ProtocolDenied("unsupported_admission_mode")
        request.validate_binding(gateway, route, self.limits, self.wall())
        command = compile_operation(request.operation, dict(request.arguments))
        from .script_transport import validate_payload

        validate_payload(request, script_request, script)
        self._authorize(request, "connect", cancelled)
        resources = []
        routed, tunnel = False, None
        runtime = min(request.limits.runtime_seconds, request.run_until - self.wall())
        deadline = self.monotonic() + runtime

        def close_resources():
            # Paramiko request acknowledgements are not all governed by socket
            # timeouts. Closing the owning transports also bounds these waits.
            for resource in reversed(tuple(resources)):
                try:
                    resource.close()
                except Exception:
                    pass

        timer = threading.Timer(runtime, close_resources)
        timer.daemon = True
        timer.start()

        def remaining():
            value = deadline - self.monotonic()
            if value <= 0 or self.wall() >= request.run_until:
                raise ProtocolDenied("deadline_exceeded")
            self._authorize_cancel(cancelled)
            return min(request.limits.connect_seconds, value)

        try:
            raw = self.driver.connect_gateway(gateway, remaining())
            resources.append(raw)
            outer = self.driver.handshake(raw, gateway.pin, "gateway", remaining())
            resources.append(outer)
            self.driver.authenticate(outer, gateway.account, gateway_key, remaining())
            helper = self.driver.helper(outer, remaining())
            # Full nested SSH handshake, auth, protocol overhead and output share
            # this meter; plaintext output has its own smaller bound below.
            tunnel = BudgetedChannel(
                helper, request.limits.tunnel_bytes, deadline, monotonic=self.monotonic
            )
            resources.append(tunnel)
            control = FrameIO(
                tunnel,
                min(deadline, self.monotonic() + request.limits.connect_seconds),
                monotonic=self.monotonic,
            )
            control.send(request.message())
            ready = control.receive()
            validate_admission(ready, "ready", request)
            now = self.wall()
            if (
                not now
                < ready["start_by"]
                <= min(request.start_by, now + request.limits.admission_seconds)
            ):
                raise ProtocolDenied("admission_expired")
            # Parent callback must recheck AND atomically consume service approval.
            self._authorize(request, "dispatch", cancelled)
            request.validate_binding(gateway, route, self.limits, self.wall())
            if self.wall() >= ready["start_by"]:
                raise ProtocolDenied("admission_expired")
            control.deadline = min(
                control.deadline, self.monotonic() + ready["start_by"] - self.wall()
            )
            routed = True  # A lost/partial go send is ambiguous, never retry it.
            control.send({**ready, "kind": "go"})
            if self.admission_mode == ADMISSION_MODE:
                challenge = AdmissionChallenge.from_message(control.receive(), request, ready)
                challenge.validate_time(self.wall())
                self._authorize_cancel(cancelled)
                try:
                    permit = self.gateway_admission(request, challenge)
                except ProtocolDenied:
                    raise
                except Exception:
                    raise ProtocolDenied("authority_unavailable") from None
                # Validate the exact committed response before any send. A lost
                # or partial permit send is uncertain and never issued again.
                validate_admission(permit, "admission_permit", request)
                if permit != challenge.message("admission_permit"):
                    raise ProtocolDenied("admission_mismatch")
                challenge.validate_time(self.wall())
                self._authorize_cancel(cancelled)
                control.send(permit)
            opened = control.receive()
            validate_admission(opened, "opened", request)
            if opened != {**ready, "kind": "opened"}:
                raise ProtocolDenied("admission_mismatch")
            if self.wall() >= ready["start_by"]:
                raise ProtocolDenied("admission_expired")
            inner = self.driver.handshake(tunnel, route.pin, "target", remaining())
            resources.append(inner)
            self._authorize(request, "target_auth", cancelled)
            self.driver.authenticate(inner, route.account, target_key, remaining())
            self._authorize(request, "execute", cancelled)
            # Setting routed before the request accounts for a lost exec ack.
            if request.operation == "workspace.script.v1":
                from .script_transport import exchange

                process = self.driver.script_command(inner, remaining())
                resources.append(process)
                outcome = exchange(process, request, script_request, script, deadline, cancelled)
                observed = outcome.state == "observed_exit" and outcome.termination_confirmed
                return ExecutionResult(
                    "completed" if observed else "uncertain",
                    "exit_observed" if observed else "exit_evidence_missing",
                    outcome.exit_code if observed else None,
                    cancellation_requested=outcome.cancel_requested,
                    tunnel_bytes=tunnel.used,
                    script_result=outcome,
                )
            process = self.driver.command(inner, command, remaining())
            resources.append(process)
            return self._collect(process, tunnel, deadline, request, cancelled)
        except ProtocolDenied as exc:
            reason = str(exc)
            return ExecutionResult(
                "uncertain" if routed else "not_started",
                reason,
                cancellation_requested=reason == "cancellation_requested",
                tunnel_bytes=tunnel.used if tunnel else 0,
            )
        except Exception:
            # Driver exception strings may contain remote text or credentials.
            reason = (
                tunnel.failure_reason
                if tunnel and tunnel.failure_reason
                else "ssh_transport_failed"
            )
            if self.monotonic() >= deadline or self.wall() >= request.run_until:
                reason = "deadline_exceeded"
            return ExecutionResult(
                "uncertain" if routed else "not_started",
                reason,
                tunnel_bytes=tunnel.used if tunnel else 0,
            )
        finally:
            timer.cancel()
            gateway_key.discard()
            target_key.discard()
            for resource in reversed(resources):
                try:
                    resource.close()
                except Exception:
                    pass

    @staticmethod
    def _authorize_cancel(cancelled):
        if cancelled is not None and cancelled.is_set():
            raise ProtocolDenied("cancellation_requested")

    def _collect(self, process, tunnel, deadline, request, cancelled):
        stdout, stderr = bytearray(), bytearray()
        while True:
            reason = tunnel.failure_reason
            if cancelled is not None and cancelled.is_set():
                reason = "cancellation_requested"
            elif self.monotonic() >= deadline or self.wall() >= request.run_until:
                reason = "deadline_exceeded"
            if reason:
                return ExecutionResult(
                    "uncertain",
                    reason,
                    stdout=bytes(stdout),
                    stderr=bytes(stderr),
                    cancellation_requested=reason == "cancellation_requested",
                    tunnel_bytes=tunnel.used,
                )
            progressed = False
            for ready, receive, output in (
                (process.recv_ready, process.recv, stdout),
                (process.recv_stderr_ready, process.recv_stderr, stderr),
            ):
                if ready():
                    room = request.limits.output_bytes - len(stdout) - len(stderr)
                    data = receive(min(4096, room + 1))
                    output.extend(data[:room])
                    progressed = progressed or bool(data)
                    if len(data) > room:
                        return ExecutionResult(
                            "uncertain",
                            "output_budget_exceeded",
                            stdout=bytes(stdout),
                            stderr=bytes(stderr),
                            tunnel_bytes=tunnel.used,
                        )
            if (
                process.exit_status_ready()
                and (process.eof_received or process.closed)
                and not process.recv_ready()
                and not process.recv_stderr_ready()
            ):
                code = process.recv_exit_status()
                if type(code) is int and 0 <= code <= 255:
                    return ExecutionResult(
                        "completed",
                        "exit_observed",
                        code,
                        bytes(stdout),
                        bytes(stderr),
                        tunnel_bytes=tunnel.used,
                    )
                return ExecutionResult(
                    "uncertain",
                    "exit_evidence_missing",
                    stdout=bytes(stdout),
                    stderr=bytes(stderr),
                    tunnel_bytes=tunnel.used,
                )
            if process.closed:
                return ExecutionResult(
                    "uncertain",
                    "exit_evidence_missing",
                    stdout=bytes(stdout),
                    stderr=bytes(stderr),
                    tunnel_bytes=tunnel.used,
                )
            if not progressed:
                # Status/close signals this event; a short wait also bounds
                # output/cancellation latency without an auxiliary connection.
                process.status_event.wait(min(0.05, max(0, deadline - self.monotonic())))
