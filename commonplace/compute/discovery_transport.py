"""One pinned outer SSH exchange; no inner target or controller-cache fallback.

The trusted service supplies current authority and a lazy custody context manager.
Authentication unwraps only after ParamikoDriver has compared the full outer pin.
In explicit permit mode, helper readiness precedes durable collect intent; the
issuer commits one exact passive permit before releasing locks and sending it.
A lost/partial go, permit or result remains uncertain and is never retried. The
legacy inert component path still records intent before sending its request.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .credentials import CredentialError
from .protocol import (
    ADMISSION_MODE,
    FrameIO,
    GatewayEnrollment,
    PassiveAdmissionChallenge,
    PassiveDiscoveryRequest,
    ProtocolDenied,
    passive_result,
)
from .transport import BudgetedChannel, EphemeralKey, ParamikoDriver

_REASONS = frozenset(
    {
        "gateway_hostkey_mismatch",
        "ssh_dependency_unavailable",
        "ssh_handshake_failed",
        "ssh_authentication_failed",
        "credential_unavailable",
        "authority_denied",
        "authority_unavailable",
        "deadline_exceeded",
        "cancellation_requested",
        "helper_response_missing",
    }
)


@dataclass(frozen=True)
class DiscoveryOutcome:
    """Only a validated final helper response can release an uncertain slot."""

    response: dict | None = None
    dispatched: bool = False
    reason: str = "ssh_transport_failed"


class PassiveSSHTransport:
    def __init__(
        self,
        *,
        authorize,
        credential,
        driver=None,
        monotonic=time.monotonic,
        wall=time.time,
        passive_admission_mode=None,
        gateway_admission=None,
    ):
        if (
            passive_admission_mode not in (None, ADMISSION_MODE)
            or (passive_admission_mode is None and gateway_admission is not None)
            or (passive_admission_mode is not None and not callable(gateway_admission))
        ):
            raise ProtocolDenied("unsupported_admission_mode")
        self.passive_admission_mode, self.gateway_admission = (
            passive_admission_mode,
            gateway_admission,
        )
        self.authorize, self.credential = authorize, credential
        self.driver = driver if driver is not None else ParamikoDriver()
        self.monotonic, self.wall = monotonic, wall

    def collect(self, request, gateway, *, seconds, cancelled=lambda: False):
        if (
            type(request) is not PassiveDiscoveryRequest
            or request.passive_admission_mode != self.passive_admission_mode
            or type(gateway) is not GatewayEnrollment
            or (request.context.workspace, request.context.gateway, request.gateway_epoch)
            != (gateway.workspace, gateway.gateway, gateway.epoch)
            or type(seconds) not in (int, float)
            or not 0 < seconds <= request.limits.duration_seconds
        ):
            raise ProtocolDenied("invalid_discovery_contract")
        resources, dispatched = [], False
        deadline = self.monotonic() + seconds

        def close():
            for resource in reversed(tuple(resources)):
                try:
                    resource.close()
                except Exception:
                    pass

        def remaining():
            if cancelled():
                raise ProtocolDenied("cancellation_requested")
            left = deadline - self.monotonic()
            if left <= 0:
                raise ProtocolDenied("deadline_exceeded")
            return min(10, left)

        def check(phase):
            remaining()
            if self.authorize(request, phase) is not True:
                raise ProtocolDenied("authority_denied")
            remaining()

        timer = threading.Timer(seconds, close)
        timer.daemon = True
        timer.start()
        try:
            check("connect")
            raw = self.driver.connect_gateway(gateway, remaining())
            resources.append(raw)
            outer = self.driver.handshake(raw, gateway.pin, "gateway", remaining())
            resources.append(outer)
            check("auth")
            with self.credential(request, gateway) as key:
                if (
                    not isinstance(key, EphemeralKey)
                    or not key.available
                    or key.credential_id != gateway.credential_id
                ):
                    raise ProtocolDenied("credential_unavailable")
                try:
                    self.driver.authenticate(outer, gateway.account, key, remaining())
                finally:
                    key.discard()
            helper = self.driver.helper(outer, remaining())
            # All six frames, including headers, share a finite fixed meter.
            channel = BudgetedChannel(helper, 65536, deadline, monotonic=self.monotonic)
            resources.append(channel)
            io = FrameIO(channel, deadline, monotonic=self.monotonic)
            if self.passive_admission_mode is None:
                check("collect")
                dispatched = True
            io.send(request.message())
            challenge = None
            if self.passive_admission_mode == ADMISSION_MODE:
                ready = io.receive()
                ready_challenge = PassiveAdmissionChallenge.from_message(
                    ready, request, kind="passive_ready"
                )
                ready_challenge.validate_time(self.wall())
                # Fresh local guarded intent is committed after helper readiness.
                check("collect")
                dispatched = True  # Lost/partial go is uncertain, never retried.
                io.deadline = min(deadline, self.monotonic() + ready["start_by"] - self.wall())
                io.send(ready_challenge.message("passive_go"))
                challenge = PassiveAdmissionChallenge.from_message(
                    io.receive(), request, ready=ready
                )
                challenge.validate_time(self.wall())
                remaining()
                permit = self.gateway_admission(request, challenge)
                PassiveAdmissionChallenge.from_message(permit, request, kind="passive_permit")
                if permit != challenge.message("passive_permit"):
                    raise ProtocolDenied("admission_mismatch")
                challenge.validate_time(self.wall())
                remaining()
                io.send(permit)  # No authority/DB lock is retained by the issuer.
                io.deadline = deadline
            response = io.receive()
            remaining()
            if challenge is not None:
                passive_result(response, challenge)
            return DiscoveryOutcome(response, True, "response_received")
        except ProtocolDenied as exc:
            reason = "helper_response_missing" if str(exc) == "control_disconnected" else str(exc)
            return DiscoveryOutcome(
                None, dispatched, reason if reason in _REASONS else "invalid_helper_response"
            )
        except CredentialError:
            return DiscoveryOutcome(None, dispatched, "credential_unavailable")
        except Exception:
            reason = "deadline_exceeded" if self.monotonic() >= deadline else "ssh_transport_failed"
            return DiscoveryOutcome(None, dispatched, reason)
        finally:
            timer.cancel()
            close()
