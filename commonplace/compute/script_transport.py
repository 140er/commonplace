"""Strict script exchange on the existing independently pinned target SSH.

No new network route or shell is introduced. This adapter is invoked only after
the durable controller/helper permit and both pinned SSH authentications. Raw
artifact data is temporary transport memory; only bounded redacted metadata is
eligible for controller persistence.
"""

import socket
import time

from .protocol import ProtocolDenied
from .scripts import OPERATION, ScriptRequest
from .target_helper import ScriptWire, receive_result, send_bundle


def validate_payload(outer, request, script):
    if outer.operation == OPERATION:
        validate_envelope(outer, request, script)
    elif request is not None or script is not None:
        raise ProtocolDenied("invalid_execution")


def validate_envelope(outer, request, script):
    if type(request) is not ScriptRequest or outer.operation != OPERATION:
        raise ProtocolDenied("invalid_execution")
    binding = request.manifest.binding
    if (
        binding.authority_epoch is not None
        or request.authority_epoch != outer.authority_epoch
        or request.approval_digest != outer.approval_digest
        or request.expires_at != outer.start_by
        or (request.execution, request.attempt) != (outer.execution, outer.attempt)
        or any(
            getattr(binding, name) != getattr(outer, name)
            for name in (
                "workspace",
                "project",
                "user",
                "agent",
                "task",
                "gateway",
                "node",
                "gateway_epoch",
                "node_epoch",
                "gateway_credential_version",
                "target_credential_version",
                "enrollment",
            )
        )
        or dict(outer.arguments)
        != {
            "bundle_id": request.manifest.bundle_id,
            "manifest_digest": request.manifest.digest,
            "script_digest": request.digest,
        }
        or request.manifest.limits.wall_seconds > outer.limits.runtime_seconds
        or request.manifest.limits.output_bytes > outer.limits.output_bytes
        or request.manifest.input_paths
    ):
        raise ProtocolDenied("admission_mismatch")
    request.manifest.verify(script)


class _CancellableChannel:
    """One I/O owner sends bounded cancel intent while awaiting exact evidence.

    Short socket timeouts make cancellation responsive; the enclosing absolute
    deadline still bounds every receive. Sending a cancel never asserts a kill.
    No second reader/writer, authority lock or background polling job is used.
    """

    def __init__(self, channel, deadline, cancelled):
        self.channel, self.deadline, self.cancelled = channel, deadline, cancelled
        self.committed = False
        self.cancel = None
        self.sent = False

    def settimeout(self, value):
        self.channel.settimeout(min(value, 0.1))

    def sendall(self, data):
        self.channel.sendall(data)

    def recv(self, count):
        while time.monotonic() < self.deadline:
            if (
                self.committed
                and not self.sent
                and self.cancelled is not None
                and self.cancelled.is_set()
            ):
                self.sent = True
                self.cancel()
            try:
                return self.channel.recv(count)
            except (socket.timeout, TimeoutError):
                continue
        raise ProtocolDenied("deadline_exceeded")


def exchange(channel, outer, request, script, deadline, cancelled):
    validate_envelope(outer, request, script)
    transport = _CancellableChannel(channel, deadline, cancelled)
    wire = ScriptWire(transport, deadline=deadline)
    transport.cancel = lambda: wire.send("script_cancel", digest=request.digest)
    send_bundle(wire, request, script)
    transport.committed = True
    return receive_result(wire, request)
