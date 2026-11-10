"""Default-off durable passive discovery, driven only by pinned outer SSH.

Parent installation: open_store(..., control_store=the_fenced_compute_store),
configure(gateway, workspace_id, store=..., enabled=True, fence=the_same_fence,
wrapping_key_path=explicit_private_path, provisioning_keys={key_id: public_bytes},
sources=("linux_arp",), passive_admission_mode=ADMISSION_MODE), then service.start().
Trusted WorkspaceExecutionConfig must independently select that admission_mode
and discovery_enabled/discovery_sources. Shutdown calls service.stop()
before stores close. Without this explicit configuration, routes remain inert.
Constructors/configure/install never read keys or open sockets. start preflights
already initialized custody; no automatic key generation or provider fallback.

Trusted provisioning calls _accept_enrollment with GatewayDiscoveryReceipt signed
by an independently pinned verifier. Its purpose binds *outer-only* passive helper
enrollment, not a public verified-state boolean or a target enrollment. The
verifier must check the exact gateway/credential binding, protected helper source
configuration and participation in gateway admission epoch checks. Local
authority uses LOCAL_DOMAINS only; PassiveSSHTransport supplies the separate
authenticated, purpose-bound challenge/permit exchange. A mode flag alone does
not certify remote participation. No controller callback proves instantaneous revocation
of an already-running remote helper. Unknown attempts never auto-recollect.
"""

from __future__ import annotations

import base64
import json
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from commonplace import sessions

from .control import StoreError, _digest
from .credentials import CredentialBinding, CredentialError, CredentialStore
from .discovery import (
    MAX_CACHE_AGE_SECONDS,
    MAX_SOURCE_BYTES,
    MAX_SOURCE_LINES,
    PassiveObservation,
    _in_scope,
    parse_cache,
    validate_sources,
)
from .discovery_store import digest, gateway_document, open_store
from .discovery_transport import PassiveSSHTransport
from .executor import _CustodyKey
from .fence import FenceDenied, _held, _LocalAuthorityFence
from .protocol import (
    ADMISSION_MODE,
    PASSIVE_PURPOSE,
    PassiveAdmissionChallenge,
    PassiveDiscoveryRequest,
    ProtocolDenied,
    canonical,
    finite,
    identifier,
    passive_result,
    positive_integer,
)
from .scopes import DiscoveryLimits
from .transport import ParamikoDriver

_STOP_REASONS = frozenset(
    {
        "cancelled",
        "duration_limit",
        "source_limit",
        "host_limit",
        "result_limit",
        "rate_limit",
        "output_limit",
    }
)


@dataclass(frozen=True)
class DiscoveryPolicy:
    workspace_concurrency: int = 4
    gateway_concurrency: int = 2
    workspace_rate: int = 100
    gateway_rate: int = 100

    def __post_init__(self):
        for name, ceiling in (
            ("workspace_concurrency", 4),
            ("gateway_concurrency", 2),
            ("workspace_rate", 100),
            ("gateway_rate", 100),
        ):
            positive_integer(getattr(self, name), ceiling)


@dataclass(frozen=True)
class GatewayDiscoveryReceipt:
    key_id: str
    workspace: str
    gateway: str
    enrollment: str
    verified_at: float
    expires_at: float
    signature: bytes

    def signed_bytes(self):
        for name in ("key_id", "workspace", "gateway"):
            identifier(getattr(self, name))
        _digest(self.enrollment)
        finite(self.verified_at)
        finite(self.expires_at)
        return canonical(
            {
                "v": 1,
                "purpose": "compute-passive-gateway-enrollment",
                **{k: v for k, v in asdict(self).items() if k != "signature"},
            }
        )


class DiscoveryService:
    def __init__(
        self,
        store,
        *,
        enabled=False,
        fence=None,
        wrapping_key_path=None,
        provisioning_keys=None,
        sources=(),
        driver=None,
        policy=None,
        job_limits=None,
        passive_admission_mode=None,
    ):
        if passive_admission_mode not in (None, ADMISSION_MODE):
            raise ProtocolDenied("unsupported_admission_mode")
        self.passive_admission_mode = passive_admission_mode
        self.store, self.control = store, store.control
        self.enabled, self.fence = enabled is True, fence
        self.wrapping_key_path = wrapping_key_path
        self._keys = dict(provisioning_keys or {})
        self.sources = tuple(sources)
        self.driver = driver
        self.policy = policy if policy is not None else DiscoveryPolicy()
        self.job_limits = (
            job_limits if job_limits is not None else DiscoveryLimits(32, 1, 32, 10, 32)
        )
        self._worker = uuid.uuid4().hex
        self._stop, self._wake = threading.Event(), threading.Event()
        self._lifecycle = threading.Lock()
        self._threads = []
        self._ready = False
        self.last_reason = "disabled" if not self.enabled else "not_started"
        self._transport = PassiveSSHTransport(
            authorize=self._authorize,
            credential=self._credential,
            driver=driver,
            passive_admission_mode=passive_admission_mode,
            gateway_admission=self._wire_admission if passive_admission_mode is not None else None,
            wall=self.control._now,
        )
        store.service = self

    @property
    def running(self):
        return self._ready and not self._stop.is_set()

    def _guard(self):
        if (
            not self.enabled
            or self.fence is None
            or self.control._authority_fence is not self.fence
        ):
            raise FenceDenied("authority_fence_unavailable")
        self._configured_mode()
        return _held(
            self.fence,
            self.store.workspace_id,
            local=self.passive_admission_mode == ADMISSION_MODE,
        )

    def _configured_mode(self):
        from . import ExecutionConfig, WorkspaceExecutionConfig

        config = getattr(self.control.gateway.db, "_compute_config", None)
        workspace = next(
            (
                w
                for w in getattr(config, "workspaces", ())
                if w.workspace == self.store.workspace_id
            ),
            None,
        )
        if self.passive_admission_mode is None:
            if workspace is not None and getattr(workspace, "admission_mode", None) is not None:
                raise FenceDenied("gateway_admission_unintegrated")
            return
        if (
            type(config) is not ExecutionConfig
            or type(workspace) is not WorkspaceExecutionConfig
            or workspace.discovery_enabled is not True
            or workspace.admission_mode != ADMISSION_MODE
            or tuple(workspace.discovery_sources) != self.sources
            or Path(workspace.wrapping_key_path) != Path(self.wrapping_key_path)
            or workspace.provisioning_keys != self._keys
            or Path(workspace.directory)
            != Path(self.control.gateway.workspace_dir(workspace.workspace))
            or getattr(self.control.gateway, "_compute_fences", {}).get(workspace.workspace)
            is not self.fence
            or type(self._transport) is not PassiveSSHTransport
            or self._transport.passive_admission_mode != ADMISSION_MODE
        ):
            raise FenceDenied("gateway_admission_unintegrated")

    def _unchanged(self, lease):
        if (
            isinstance(self.fence, _LocalAuthorityFence)
            and self.fence._coordinator.epoch() != lease.epoch
        ):
            raise StoreError("authority_changed", 409)

    def available(self):
        """Current process availability; no key/custody/network work on HTTP."""
        if not self.running:
            return False
        try:
            with self._guard():
                return True
        except Exception:
            return False

    def _policy(self, conn):
        if type(self.policy) is not DiscoveryPolicy or type(self.job_limits) is not DiscoveryLimits:
            raise StoreError("discovery_unavailable", 503)
        self.policy.__post_init__()
        self.job_limits.__post_init__()
        # Durable policy prevents another process/config restart widening capacity.
        document = canonical(asdict(self.policy)).decode()
        conn.execute("INSERT OR IGNORE INTO discovery_policy VALUES (1,?)", (document,))
        if (
            conn.execute("SELECT document FROM discovery_policy WHERE singleton=1").fetchone()[0]
            != document
        ):
            raise StoreError("discovery_policy_conflict", 409)

    def _custody_store(self, authorize):
        if self.wrapping_key_path is None:
            raise CredentialError("credential_unavailable")
        path = Path(self.control._path() if callable(self.control._path) else self.control._path)
        return CredentialStore(
            path.parent / "compute-credentials.sqlite3",
            self.wrapping_key_path,
            path.parent,
            authorize,
            clock=self.control.clock,
        )

    def start(self, *, background=True):
        """Start two bounded workers; background=False enables an explicit pump.

        The explicit pump is useful for embedding/testing via _run_once; ordinary
        installation uses background=True. Only this lifecycle call preflights keys.
        """
        if not self.enabled:
            return False
        with self._lifecycle:
            if self.running:
                return True
            try:
                validate_sources(self.sources)
                if self.driver is None:
                    self.driver = ParamikoDriver()
                    self.driver._library()
                if not 1 <= len(self._keys) <= 16:
                    raise ValueError
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

                for name, key in self._keys.items():
                    identifier(name)
                    if type(key) is not bytes:
                        raise ValueError
                    Ed25519PublicKey.from_public_bytes(key)
                with self._guard():
                    with self.control._journal():
                        with self.store._journal() as conn:
                            self._policy(conn)
                            self._recover(conn)
                # No broad authority/database lock spans custody opening.
                with self._custody_store(lambda request, supplied: False):
                    pass
            except Exception:
                self.last_reason = "discovery_unavailable"
                return False
            self._stop.clear()
            self._ready, self.last_reason = True, "available"
            if background:
                self._threads = [
                    threading.Thread(target=self._work, name="compute-discovery", daemon=True)
                    for _ in range(2)
                ]
                for thread in self._threads:
                    thread.start()
            return True

    def wake(self):
        self._wake.set()

    def stop(self, timeout=1.0):
        if type(timeout) not in (int, float) or not 0 <= timeout <= 30:
            raise ValueError("invalid_stop_timeout")
        self._stop.set()
        self._wake.set()
        self._ready = False
        with self._lifecycle:
            threads = tuple(self._threads)
        for thread in threads:
            thread.join(timeout / max(1, len(threads)))
        return not any(thread.is_alive() for thread in threads)

    def _work(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                worked = self._run_once()
            except Exception:
                self.last_reason = "discovery_unavailable"
                worked = False
            if not worked:
                self._wake.wait(0.5)

    def _receipt(self, conn, gateway):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        try:
            stored = conn.execute(
                "SELECT document FROM discovery_receipts WHERE gateway_id=?", (gateway.gateway,)
            ).fetchone()
            if stored is None:
                raise ValueError
            raw = json.loads(stored[0])
            receipt = GatewayDiscoveryReceipt(
                **{**raw, "signature": base64.b64decode(raw["signature"], validate=True)}
            )
            key = self._keys[receipt.key_id]
            Ed25519PublicKey.from_public_bytes(key).verify(
                receipt.signature, receipt.signed_bytes()
            )
            now = self.control._now()
            if (
                (receipt.workspace, receipt.gateway, receipt.enrollment)
                != (gateway.workspace, gateway.gateway, digest(gateway_document(gateway)))
                or not receipt.verified_at <= now < receipt.expires_at
                or not receipt.verified_at < receipt.expires_at <= receipt.verified_at + 3600
            ):
                raise ValueError
            return receipt
        except Exception:
            raise StoreError("enrollment_unverified", 409) from None

    def _accept_enrollment(self, principal, gateway_id, *, session_hash, receipt):
        """Trusted signed provisioning only; never registered as an HTTP action."""
        if type(receipt) is not GatewayDiscoveryReceipt:
            raise StoreError("enrollment_unverified", 409)
        with self._guard():
            with self.control._journal() as control:
                current = self.control._owner(principal, session_hash)
                row, metadata = self.control._gateway(control, current, gateway_id)
                outer = self.control._enrollment(row, metadata)
                try:
                    document = {
                        **asdict(receipt),
                        "signature": base64.b64encode(receipt.signature).decode("ascii"),
                    }
                    with self.store._journal() as conn:
                        conn.execute(
                            "INSERT INTO discovery_receipts VALUES (?,?) ON CONFLICT(gateway_id) DO UPDATE SET document=excluded.document",
                            (gateway_id, canonical(document).decode()),
                        )
                        self._receipt(conn, outer)
                except Exception:
                    raise StoreError("enrollment_unverified", 409) from None

    def _binding(self, conn, scope, gateway, authority, sources):
        if (
            scope.revoked
            or self.control._now() >= scope.expires_at
            or scope.methods != frozenset({"passive"})
            or not set(sources) <= set(self.sources)
        ):
            raise StoreError("scope_unavailable", 409)
        proof = self._receipt(conn, gateway)
        # Request construction checks that every existing context field is wire-safe.
        PassiveDiscoveryRequest(
            "check", "check", gateway.epoch, 1, scope.context, sources, scope.limits
        )
        raw_scope = {**asdict(scope), "methods": sorted(scope.methods)}
        return {
            "passive_admission_mode": self.passive_admission_mode,
            "scope": raw_scope,
            "gateway": gateway_document(gateway),
            "authority": authority,
            "receipt": digest(
                {
                    "signed": proof.signed_bytes().decode(),
                    "signature": base64.b64encode(proof.signature).decode(),
                }
            ),
        }

    def _fresh(self, control, conn, row, lease, *, publishing=False):
        principal = sessions.person_principal(
            self.control.gateway.db, row["user_id"], self.store.workspace_id
        )
        current = self.control._owner(principal, row["session_hash"])
        _, scope, _, outer, authority = self.store._scope(control, current, row["scope_id"])
        sources = tuple(json.loads(row["intake"])["sources"])
        binding = self._binding(conn, scope, outer, authority, sources)
        if canonical(binding).decode() != row["binding"] or lease.epoch != row["authority_epoch"]:
            raise StoreError("authority_changed", 409)
        if not publishing and (
            row["cancel_requested"]
            or self._stop.is_set()
            or (row["deadline"] is not None and self.control._now() >= row["deadline"])
        ):
            raise StoreError("authority_changed", 409)
        return scope, outer

    def _recover(self, conn):
        # Never reclaim, rerun or release a running attempt just because it expired.
        conn.execute(
            "UPDATE discoveries SET state='uncertain',reason='interrupted',updated_at=? WHERE state IN ('claimed','dispatching') AND deadline<=?",
            (self.control._now(), self.control._now()),
        )

    def _claim(self):
        if not self.running:
            return None
        with self._guard() as lease:
            if not self.running:
                return None
            with self.control._journal() as control:
                with self.store._journal() as conn:
                    self._policy(conn)
                    self._recover(conn)
                    for row in conn.execute(
                        "SELECT * FROM discoveries WHERE state='queued' ORDER BY created_at,id"
                    ).fetchall():
                        try:
                            scope, gateway = self._fresh(control, conn, row, lease)
                        except Exception:
                            conn.execute(
                                "UPDATE discoveries SET state='denied',reason='authority_changed',updated_at=? WHERE id=?",
                                (self.control._now(), row["id"]),
                            )
                            continue
                        now = self.control._now()
                        active = conn.execute(
                            "SELECT gateway_id,scope_id,slot,rate FROM discoveries WHERE slot=1 OR ended_at>?",
                            (now - 1,),
                        ).fetchall()
                        gateway_active = [x for x in active if x["gateway_id"] == row["gateway_id"]]
                        scope_active = [x for x in active if x["scope_id"] == row["scope_id"]]
                        if (
                            sum(x["slot"] for x in active) >= self.policy.workspace_concurrency
                            or sum(x["slot"] for x in gateway_active)
                            >= self.policy.gateway_concurrency
                            or sum(x["slot"] for x in scope_active) >= row["scope_concurrency"]
                            or sum(x["rate"] for x in active) + row["rate"]
                            > self.policy.workspace_rate
                            or sum(x["rate"] for x in gateway_active) + row["rate"]
                            > self.policy.gateway_rate
                            or sum(x["rate"] for x in scope_active) + row["rate"]
                            > scope.limits.rate_per_second
                        ):
                            continue
                        deadline = min(
                            now + row["seconds"],
                            scope.expires_at,
                            self._receipt(conn, gateway).expires_at,
                        )
                        if deadline <= now:
                            continue
                        request = PassiveDiscoveryRequest(
                            row["id"],
                            row["scope_id"],
                            gateway.epoch,
                            lease.epoch,
                            scope.context,
                            tuple(json.loads(row["intake"])["sources"]),
                            DiscoveryLimits(
                                row["hosts"], 1, row["rate"], row["seconds"], row["results"]
                            ),
                            passive_admission_mode=self.passive_admission_mode,
                            start_by=min(deadline, now + 5)
                            if self.passive_admission_mode
                            else None,
                            run_until=deadline if self.passive_admission_mode else None,
                        )
                        conn.execute(
                            "INSERT INTO discovery_attempts VALUES (?,?,?,?)",
                            (row["id"], self._worker, canonical(request.message()).decode(), now),
                        )
                        self._unchanged(lease)
                        conn.execute(
                            "UPDATE discoveries SET state='claimed',slot=1,worker=?,deadline=?,updated_at=? WHERE id=? AND state='queued'",
                            (self._worker, deadline, now, row["id"]),
                        )
                        return request, gateway, deadline
        return None

    def _check(self, control, conn, lease, request):
        row = self.store._row(conn, request.discovery)
        if row["worker"] != self._worker or row["state"] not in ("claimed", "dispatching"):
            raise StoreError("authority_changed", 409)
        scope, gateway = self._fresh(control, conn, row, lease)
        attempt = conn.execute(
            "SELECT * FROM discovery_attempts WHERE discovery_id=?", (request.discovery,)
        ).fetchone()
        if (
            attempt is None
            or attempt["worker"] != self._worker
            or attempt["request"] != canonical(request.message()).decode()
            or request.passive_admission_mode != self.passive_admission_mode
            or request.gateway_epoch != gateway.epoch
            or request.authority_epoch != lease.epoch
            or (request.run_until is not None and request.run_until != row["deadline"])
        ):
            raise StoreError("authority_changed", 409)
        return row, scope, gateway

    def _authorize(self, request, phase):
        try:
            with self._guard() as lease:
                with self.control._journal() as control:
                    with self.store._journal() as conn:
                        row, _, _ = self._check(control, conn, lease, request)
                        if phase not in ("connect", "auth", "collect") or row["state"] != "claimed":
                            return False
                        if phase == "collect":
                            self._unchanged(lease)
                            conn.execute(
                                "UPDATE discoveries SET state='dispatching',updated_at=? WHERE id=?",
                                (self.control._now(), request.discovery),
                            )
            return True
        except Exception:
            return False

    @staticmethod
    def _credential_binding(request, gateway):
        return CredentialBinding(
            request.context.workspace,
            request.context.user,
            gateway.gateway,
            "",
            gateway.account,
            "gateway",
            gateway.credential_version,
        )

    def _admission_credential(self, request, gateway):
        context = object()
        binding = self._credential_binding(request, gateway)

        def authority(credential_request, supplied):
            return (
                supplied is context
                and credential_request.action == "acquire"
                and (
                    credential_request.enrollment.credential_id,
                    credential_request.enrollment.binding,
                )
                == (gateway.credential_id, binding)
            )

        with self._custody_store(authority) as custody:
            custody.acquire(gateway.credential_id, binding, context).close()

    def _wire_admission(self, request, challenge):
        if (
            self.passive_admission_mode != ADMISSION_MODE
            or type(challenge) is not PassiveAdmissionChallenge
            or challenge.request != request
        ):
            raise ProtocolDenied("authority_denied")
        try:
            with self._guard() as lease:
                with self.control._journal() as control:
                    with self.store._journal() as conn:
                        row, _, gateway = self._check(control, conn, lease, request)
                        if row["state"] != "dispatching" or row["slot"] != 1:
                            raise StoreError("authority_changed", 409)
                        challenge.validate_time(self.control._now())
                        self._admission_credential(request, gateway)
                        self._unchanged(lease)
                        permit = challenge.message("passive_permit")
                        conn.execute(
                            "INSERT INTO discovery_permits VALUES (?,?,?,?,?,?,?,?)",
                            (
                                request.discovery,
                                PASSIVE_PURPOSE,
                                request.digest,
                                lease.epoch,
                                challenge.channel,
                                challenge.nonce,
                                canonical(permit).decode(),
                                self.control._now(),
                            ),
                        )
                        challenge.validate_time(self.control._now())
            # Commit AND authority release precede all channel sends/reads.
            return permit
        except Exception:
            raise ProtocolDenied("authority_denied") from None

    @contextmanager
    def _credential(self, request, gateway):
        context = object()
        binding = self._credential_binding(request, gateway)

        def authority(credential_request, supplied):
            if (
                supplied is not context
                or credential_request.action != "acquire"
                or (
                    credential_request.enrollment.credential_id,
                    credential_request.enrollment.binding,
                )
                != (gateway.credential_id, binding)
            ):
                return False
            try:
                with self._guard() as lease:
                    with self.control._journal() as control:
                        with self.store._journal() as conn:
                            self._check(control, conn, lease, request)
                return True
            except Exception:
                return False

        if not self._authorize(request, "auth"):
            raise CredentialError("credential_unavailable")
        with self._custody_store(authority) as custody:
            key = _CustodyKey(
                custody.acquire(gateway.credential_id, binding, context), gateway.credential_id
            )
            try:
                yield key
            finally:
                key.discard()

    def _cancelled(self, discovery_id):
        if self._stop.is_set():
            return True
        try:
            with self.store._journal() as conn:
                row = self.store._row(conn, discovery_id)
                return bool(row["cancel_requested"]) or row["state"] not in (
                    "claimed",
                    "dispatching",
                )
        except Exception:
            return True

    def _validate_response(self, response, request, scope):
        fields = {
            "v",
            "kind",
            "discovery",
            "observations",
            "truncated",
            "reason",
            "source_bytes",
            "source_lines",
        }
        if (
            type(response) is not dict
            or set(response) != fields
            or len(canonical(response)) > request.output_bytes
        ):
            raise ValueError
        if (
            type(response["v"]) is not int
            or response["v"] != 1
            or response["kind"] != "passive_observations"
            or response["discovery"] != request.discovery
        ):
            raise ValueError
        reason = response["reason"]
        if (
            (reason is not None and (type(reason) is not str or reason not in _STOP_REASONS))
            or type(response["truncated"]) is not bool
            or response["truncated"] != (reason is not None)
        ):
            raise ValueError
        for name, maximum in (
            ("source_bytes", MAX_SOURCE_BYTES),
            ("source_lines", MAX_SOURCE_LINES),
        ):
            if type(response[name]) is not int or not 0 <= response[name] <= maximum:
                raise ValueError
        observations = response["observations"]
        if type(observations) is not list or len(observations) > min(
            request.limits.results,
            response["source_lines"],
        ):
            raise ValueError
        if response["source_bytes"] < response["source_lines"]:
            raise ValueError
        now, hosts, seen, times, result = self.control._now(), set(), set(), {}, []
        minimum_bytes, source_counts = 0, {}
        for item in observations:
            if type(item) is not dict or set(item) != set(PassiveObservation.__dataclass_fields__):
                raise ValueError
            for name in ("gateway", "interface", "source", "address", "hardware_address", "state"):
                if type(item[name]) is not str or len(item[name]) > 128:
                    raise ValueError
            if item["gateway"] != request.context.gateway or item["source"] not in request.sources:
                raise ValueError
            stamp = finite(item["observed_at"])
            if (
                not now - MAX_CACHE_AGE_SECONDS <= stamp <= now
                or times.setdefault(item["source"], stamp) != stamp
            ):
                raise ValueError
            if item["source"] == "linux_arp":
                if item["state"] != "complete":
                    raise ValueError
                line = f"{item['address']} 0x1 0x2 {item['hardware_address']} * {item['interface']}"
            else:
                line = f"{item['address']} dev {item['interface']} lladdr {item['hardware_address']} {item['state']}"
            minimum_bytes += len(line.encode("ascii"))
            source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1
            parsed = parse_cache(item["source"], line.encode("ascii"))
            if len(parsed) != 1 or asdict(parsed[0]) != {
                k: item[k] for k in ("address", "interface", "hardware_address", "state")
            }:
                raise ValueError
            observation = PassiveObservation(**item)
            if observation in seen or not _in_scope(scope, request.context, observation, now):
                raise ValueError
            seen.add(observation)
            hosts.add((observation.interface, observation.address))
            if len(hosts) > request.limits.hosts:
                raise ValueError
            result.append(observation.document())
        minimum_bytes += sum(max(0, count - 1) for count in source_counts.values())
        if response["source_bytes"] < minimum_bytes:
            raise ValueError
        return result, {name: response[name] for name in ("source_bytes", "source_lines")}, reason

    def _finish(self, request, outcome):
        # If the fence is unavailable, leave the durable slot for recovery.
        with self._guard() as lease:
            with self.control._journal() as control:
                with self.store._journal() as conn:
                    row = self.store._row(conn, request.discovery)
                    if row["worker"] != self._worker or row["state"] not in (
                        "claimed",
                        "dispatching",
                        "uncertain",
                    ):
                        return
                    known, observations, measured, partial = False, [], {}, None
                    reason = outcome.reason
                    try:
                        # Validate even after cancellation. A valid final exchange
                        # proves that helper invocation ended, not host liveness.
                        scope, _ = self._fresh(control, conn, row, lease, publishing=True)
                        if outcome.response is not None:
                            response = outcome.response
                            if request.passive_admission_mode is not None:
                                record = conn.execute(
                                    "SELECT permit FROM discovery_permits WHERE discovery_id=?",
                                    (request.discovery,),
                                ).fetchone()
                                if record is None:
                                    raise ValueError
                                challenge = PassiveAdmissionChallenge.from_message(
                                    json.loads(record["permit"]), request, kind="passive_permit"
                                )
                                response = passive_result(response, challenge)
                            observations, measured, partial = self._validate_response(
                                response, request, scope
                            )
                            known = True
                    except Exception:
                        reason = (
                            "invalid_helper_response"
                            if outcome.response is not None
                            else "authority_changed"
                        )
                    if not known:
                        observations = []
                    cancelled = row["cancel_requested"] or self._stop.is_set()
                    if known and cancelled:
                        state, reason, observations = "cancelled", "cancellation_requested", []
                    elif known:
                        state, reason = (
                            ("partial", partial)
                            if partial
                            else ("completed", "collection_complete")
                        )
                    elif (
                        outcome.dispatched
                        or row["state"] in ("dispatching", "uncertain")
                        or conn.execute(
                            "SELECT 1 FROM discovery_permits WHERE discovery_id=?",
                            (request.discovery,),
                        ).fetchone()
                        is not None
                    ):
                        state = "uncertain"
                    else:
                        state = "failed"
                    slot = int(state == "uncertain")
                    usage = {**json.loads(row["usage"]), **measured}
                    conn.execute(
                        "UPDATE discoveries SET state=?,reason=?,slot=?,observations=?,usage=?,updated_at=?,ended_at=? WHERE id=?",
                        (
                            state,
                            reason,
                            slot,
                            canonical(observations).decode(),
                            canonical(usage).decode(),
                            self.control._now(),
                            None if slot else self.control._now(),
                            request.discovery,
                        ),
                    )

    def _run_once(self):
        claimed = self._claim()
        if claimed is None:
            return False
        request, gateway, deadline = claimed
        transport = self._transport
        transport.driver = self.driver
        seconds = deadline - self.control._now()
        if seconds <= 0:
            from .discovery_transport import DiscoveryOutcome

            outcome = DiscoveryOutcome(reason="deadline_exceeded")
        else:
            outcome = transport.collect(
                request,
                gateway,
                seconds=seconds,
                cancelled=lambda: self._cancelled(request.discovery),
            )
        self._finish(request, outcome)
        return True


def configure(gateway, workspace_id, *, store=None, **settings):
    """Explicit trusted installation registry; never read environment/config files.

    Parent supplies existing ControlStore via open_store, then starts/stops the
    returned service. Routes look up this workspace only and never auto-start it.
    Reconfiguration while workers are alive is refused; stop the previous service.
    """
    registry = getattr(gateway, "_compute_discovery_services", None)
    if registry is None:
        registry = gateway._compute_discovery_services = {}
    previous = registry.get(workspace_id)
    if previous is not None and (previous.running or any(t.is_alive() for t in previous._threads)):
        raise ValueError("discovery_already_configured")
    store = store if store is not None else open_store(gateway, workspace_id)
    if store.gateway is not gateway or store.workspace_id != workspace_id:
        raise ValueError("discovery_store_mismatch")
    service = DiscoveryService(store, **settings)
    registry[workspace_id] = service
    return service


def configured_service(gateway, workspace_id):
    return getattr(gateway, "_compute_discovery_services", {}).get(workspace_id)


def install_lifecycle(gateway):
    """Install only explicitly enabled discovery using the existing workspace fence.

    The exact trusted admission_mode selects the concrete passive SSH adapter;
    a local lease remains local. Missing mode/fence/custody stays unavailable.
    Shutdown shares the central signal-all, bounded-join lifecycle.
    """
    config = getattr(gateway.db, "_compute_config", None)
    if config is None:
        return
    from .control import open_store as open_control_store

    gateway._compute_discovery_reasons = {}
    for workspace in config.workspaces:
        if getattr(workspace, "discovery_enabled", False) is not True:
            gateway._compute_discovery_reasons[workspace.workspace] = "disabled"
            continue
        try:
            fence = getattr(gateway, "_compute_fences", {}).get(workspace.workspace)
            if fence is None:
                raise FenceDenied("authority_fence_unavailable")
            control = open_control_store(gateway, workspace.workspace)
            if control._authority_fence is not fence:
                raise FenceDenied("authority_fence_unavailable")
            store = open_store(gateway, workspace.workspace, control_store=control)
            service = configure(
                gateway,
                workspace.workspace,
                store=store,
                enabled=True,
                fence=fence,
                wrapping_key_path=workspace.wrapping_key_path,
                provisioning_keys=workspace.provisioning_keys,
                sources=workspace.discovery_sources,
                passive_admission_mode=getattr(workspace, "admission_mode", None),
            )
            gateway.db._compute_services.append(service)
            service.start()
            gateway._compute_discovery_reasons[workspace.workspace] = service.last_reason
        except Exception:
            gateway._compute_discovery_reasons[workspace.workspace] = "discovery_unavailable"
