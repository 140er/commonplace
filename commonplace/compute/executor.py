"""Durable, default-off background execution of exact approved Compute probes.

Host integration is explicit: pass the same participating AuthorityFence to Store
and ExecutionService, an externally provisioned wrapping-key path, and pinned
Ed25519 provisioning public keys. Local writer participation is implemented in
fence.py; its lease deliberately excludes remote admission. Explicit
controller-ssh-permit-v1 configuration wires a separate challenge/permit round
trip over the existing authenticated SSH helper channel. A local-only service
cannot start transport.

start/stop manage bounded background threads; there is no public dispatch or
verification setter. Trusted provisioning calls _accept_enrollment with a signed
receipt from an independent verifier, never a request-body boolean or a probe.
Constructing/installing the service performs no I/O and initializes no secrets.

Each approved execution has at most one execution_runs row. Claim commits before
custody or network work. A crash, unknown acknowledgement or stale running row
never queues a second attempt. Uncertain reservations require external evidence;
no automatic reconciliation or restore rollback safety is claimed. Backups must
preserve this journal; restoring an old journal requires disabling execution.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from commonplace import redact, sessions
from commonplace.control_db import ControlError

from .control import StoreError, _digest
from .credentials import (
    CredentialBinding,
    CredentialError,
    CredentialStore,
    internal_transport_private_bytes,
)
from .fence import FenceDenied, _held, _LocalAuthorityFence
from .protocol import (
    ADMISSION_MODE,
    AdmissionChallenge,
    ExecutionRequest,
    Limits,
    ProtocolDenied,
    canonical,
    enrollment_digest,
    finite,
    identifier,
)
from .transport import SCRIPT_NONRUN_CODES, EphemeralKey, ExecutionResult, SSHTransport

MAX_RESULT_BYTES = 8192
MAX_VERIFICATION_SECONDS = 3600
_PHASES = {"connect": 1, "dispatch": 2, "target_auth": 3, "execute": 4}
_REASONS = frozenset(
    {
        "exit_observed",
        "exit_evidence_missing",
        "cancellation_requested",
        "deadline_exceeded",
        "output_budget_exceeded",
        "tunnel_budget_exceeded",
        "ssh_transport_failed",
        "ssh_handshake_failed",
        "ssh_authentication_failed",
        "gateway_hostkey_mismatch",
        "target_hostkey_mismatch",
        "authority_denied",
        "authority_unavailable",
        "admission_expired",
        "admission_mismatch",
        "credential_unavailable",
        "ssh_dependency_unavailable",
        "cancelled_before_start",
        *SCRIPT_NONRUN_CODES,
    }
)


@dataclass(frozen=True)
class EnrollmentReceipt:
    """Public, detached proof from a separately trusted provisioning verifier.

    The signer must independently verify the exact two-hop enrollment/credential
    bindings and protected helper configuration, without unapproved probes. Merely
    owning a browser session cannot mint this signature. Public keys are pinned
    in service configuration, never taken from the submitted receipt.
    """

    key_id: str
    workspace: str
    gateway: str
    node: str
    enrollment: str
    verified_at: float
    expires_at: float
    signature: bytes

    def signed_bytes(self):
        for value in (self.key_id, self.workspace, self.gateway, self.node):
            identifier(value)
        _digest(self.enrollment)
        finite(self.verified_at)
        finite(self.expires_at)
        return canonical(
            {
                "v": 1,
                "purpose": "compute-enrollment",
                **{
                    name: getattr(self, name)
                    for name in self.__dataclass_fields__
                    if name != "signature"
                },
            }
        )


class _CustodyKey(EphemeralKey):
    """Transport-compatible key that unwraps only after the transport pin check.

    Unlike an eager EphemeralKey this holds an actual opaque custody handle,
    never private bytes. Custody rechecks current authority, version, expiry and
    revocation on the final internal conversion as well as handle acquisition.
    """

    __slots__ = ("_handle",)

    def __init__(self, handle, credential_id):
        self._handle = handle
        self._credential_id = credential_id
        self._lock = threading.Lock()

    @property
    def available(self):
        with self._lock:
            return self._handle is not None

    def _take_for_transport(self):
        with self._lock:
            if self._handle is None:
                raise ProtocolDenied("credential_unavailable")
            handle, self._handle = self._handle, None
            try:
                return internal_transport_private_bytes(handle)
            finally:
                handle.close()

    def discard(self):
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None


class _Cancellation:
    def __init__(self, service, request):
        self.service, self.request = service, request

    def is_set(self):
        if self.service._stop.is_set():
            return True
        try:
            with self.service.store._journal() as conn:
                run = self.service._run(conn, self.request)
                return run["cancel_requested_at"] is not None or run["phase"] == "terminal"
        except Exception:
            return True


class ExecutionService:
    """Lazy opt-in service. The parent owns installation and shutdown ordering.

    ``wrapping_key_path`` is an explicit service-private path, not key material.
    Only the already-initialized workspace compute-credentials.sqlite3 is opened.
    start() returns False when disabled or missing the required configuration.
    stop(timeout) requests local cancellation and reports whether threads exited;
    it does not prove remote termination or force-stop Python/network threads.
    """

    def __init__(
        self,
        store,
        *,
        enabled=False,
        fence=None,
        wrapping_key_path=None,
        provisioning_keys=None,
        driver=None,
        admission_mode=None,
    ):
        if admission_mode not in (None, ADMISSION_MODE):
            raise ProtocolDenied("unsupported_admission_mode")
        self.admission_mode = admission_mode
        self.store, self.enabled = store, enabled is True
        self.fence, self.wrapping_key_path = fence, wrapping_key_path
        self._provisioning_keys = dict(provisioning_keys or {})
        self._driver = driver
        self._worker_id = uuid.uuid4().hex
        self._stop = threading.Event()
        self._lifecycle = threading.Lock()
        self._threads = []
        self.last_reason = "disabled" if not self.enabled else "not_started"

    def _guard(self):
        if self.fence is None or self.store._authority_fence is not self.fence:
            raise FenceDenied("authority_fence_unavailable")
        return _held(self.fence, self.store.workspace_id, local=True)

    def _unchanged(self, lease):
        if (
            isinstance(self.fence, _LocalAuthorityFence)
            and self.fence._coordinator.epoch() != lease.epoch
        ):
            raise StoreError("authority_changed", 409)

    @property
    def running(self):
        """Current automatic worker availability, not a target readiness guarantee."""
        return (
            self.enabled
            and not self._stop.is_set()
            and any(thread.is_alive() for thread in self._threads)
        )

    def start(self):
        if not self.enabled:
            return False
        if isinstance(self.fence, _LocalAuthorityFence) and self.admission_mode is None:
            self.last_reason = "gateway_admission_unintegrated"
            return False
        try:
            with self._guard():
                if self.wrapping_key_path is None:
                    raise CredentialError("credential_unavailable")
        except Exception:
            self.last_reason = "execution_unavailable"
            return False
        with self._lifecycle:
            if any(thread.is_alive() for thread in self._threads):
                return True
            self._stop.clear()
            self._threads = [
                threading.Thread(target=self._work, name="compute-executor", daemon=True)
                for _ in range(2)
            ]
            for thread in self._threads:
                thread.start()
        return True

    def stop(self, timeout=1.0):
        finite(timeout)
        if not 0 <= timeout <= 30:
            raise ValueError("invalid_stop_timeout")
        self._stop.set()
        # Do not hold lifecycle/control/fence/custody locks while joining.
        with self._lifecycle:
            threads = tuple(self._threads)
        for thread in threads:
            thread.join(timeout / max(1, len(threads)))
        return not any(thread.is_alive() for thread in threads)

    def _work(self):
        while not self._stop.is_set():
            worked = self._run_once()
            if not worked:
                self._stop.wait(0.5)

    def _accept_enrollment(self, principal, node_id, *, session_hash, receipt):
        """Trusted offline provisioning seam, deliberately absent from Store/REST.

        No caller verified flag, supplied signing key, custody access or network
        work. A valid receipt is still checked against current enrollment inside
        the shared fence. Pending proposals become stale when state changes.
        """
        if not self.enabled or type(receipt) is not EnrollmentReceipt:
            raise StoreError("verification_unavailable", 409)
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            public = self._provisioning_keys[receipt.key_id]
            if type(public) is not bytes or type(receipt.signature) is not bytes:
                raise ValueError
            raw = receipt.signed_bytes()
            Ed25519PublicKey.from_public_bytes(public).verify(receipt.signature, raw)
            with self._guard():
                with self.store._journal() as conn:
                    current = self.store._owner(principal, session_hash)
                    node, nm, gateway, gm = self.store._node(conn, current, node_id)
                    outer = self.store._enrollment(gateway, gm)
                    inner = self.store._route(node, nm, gateway["id"])
                    now = self.store._now()
                    if (
                        (receipt.workspace, receipt.gateway, receipt.node, receipt.enrollment)
                        != (
                            self.store.workspace_id,
                            gateway["id"],
                            node_id,
                            enrollment_digest(outer, inner),
                        )
                        or not receipt.verified_at <= now < receipt.expires_at
                        or not receipt.verified_at
                        < receipt.expires_at
                        <= receipt.verified_at + MAX_VERIFICATION_SECONDS
                    ):
                        raise ValueError
                    for table, row, metadata in (("gateways", gateway, gm), ("nodes", node, nm)):
                        metadata["state"] = "verified"
                        conn.execute(
                            f"UPDATE {table} SET metadata=? WHERE id=?",
                            (canonical(metadata).decode(), row["id"]),
                        )
                    digest = hashlib.sha256(raw + receipt.signature).hexdigest()
                    conn.execute(
                        "INSERT INTO enrollment_verifications(node_id,gateway_id,enrollment,"
                        "receipt_digest,verified_at,expires_at) VALUES (?,?,?,?,?,?) "
                        "ON CONFLICT(node_id) DO UPDATE SET gateway_id=excluded.gateway_id,"
                        "enrollment=excluded.enrollment,receipt_digest=excluded.receipt_digest,"
                        "verified_at=excluded.verified_at,expires_at=excluded.expires_at,"
                        "script_runtime=NULL",
                        (
                            node_id,
                            gateway["id"],
                            receipt.enrollment,
                            digest,
                            receipt.verified_at,
                            receipt.expires_at,
                        ),
                    )
                    self.store._audit(conn, current, node_id, "enrollment.verified")
        except Exception:
            raise StoreError("verification_unavailable", 409) from None

    def _accept_script_runtime(self, principal, node_id, *, session_hash, receipt):
        """Trusted, independently signed runtime receipt; absent from REST."""
        from .script_control import accept_runtime

        return accept_runtime(self, principal, node_id, session_hash=session_hash, receipt=receipt)

    def _person(self, user_id, session_hash):
        principal = sessions.person_principal(
            self.store.gateway.db, user_id, self.store.workspace_id
        )
        return self.store._owner(principal, session_hash)

    def _fresh(self, conn, execution_id):
        row = self.store._row(conn, "executions", execution_id)
        now = self.store._now()
        if (
            row["state"] != "approved"
            or row["approval_expires_at"] is None
            or now >= min(row["approval_expires_at"], row["proposal_expires_at"])
        ):
            raise StoreError("approval_unavailable", 409)
        origin = self._person(row["user_id"], row["session_hash"])
        approver = self._person(row["approved_by"], row["approval_session_hash"])
        stored = json.loads(row["binding"])
        self.store._execution_task(origin, stored["task_number"])
        fresh = self.store._binding(
            conn,
            origin,
            row["session_hash"],
            row["node_id"],
            stored["task_number"],
            stored["operation"],
            stored["arguments"],
            row["proposal_expires_at"],
            row["id"],
            row["request_id"],
        )
        self.store._authority(approver, fresh["authority"]["board"], fresh["task_number"])
        if canonical(fresh) != canonical(stored) or not hmac.compare_digest(
            hashlib.sha256(canonical(fresh)).hexdigest(), row["digest"]
        ):
            raise StoreError("authority_changed", 409)
        node, nm, gateway, gm = self.store._node(conn, origin, row["node_id"])
        outer = self.store._enrollment(gateway, gm)
        inner = self.store._route(node, nm, gateway["id"])
        proof = conn.execute(
            "SELECT * FROM enrollment_verifications WHERE node_id=?", (node["id"],)
        ).fetchone()
        if (
            gm["state"] != "verified"
            or nm["state"] != "verified"
            or proof is None
            or proof["gateway_id"] != gateway["id"]
            or proof["enrollment"] != enrollment_digest(outer, inner)
            or not proof["verified_at"] <= now < proof["expires_at"]
        ):
            raise StoreError("enrollment_unverified", 409)
        return row, fresh, outer, inner, origin

    def _recover(self, conn):
        # Stage zero precedes any socket and is a confirmed non-run on recovery.
        conn.execute(
            "UPDATE execution_runs SET phase='terminal',reason='interrupted_before_start',"
            "certainty='known',reserved=0,updated_at=? "
            "WHERE phase='admitted' AND stage=0 AND run_until<=?",
            (self.store._now(), self.store._now()),
        )
        # An elapsed deadline after dispatch is no proof of remote death.
        conn.execute(
            "UPDATE execution_runs SET phase='uncertain',reason='interrupted',updated_at=? "
            "WHERE phase IN ('admitted','dispatching','running') AND run_until<=?",
            (self.store._now(), self.store._now()),
        )

    @staticmethod
    def _consent_digest(row):
        return hashlib.sha256(
            canonical(
                {
                    name: row[name]
                    for name in (
                        "id",
                        "digest",
                        "session_hash",
                        "approved_by",
                        "approval_session_hash",
                        "approval_expires_at",
                    )
                }
            )
        ).hexdigest()

    def _claim(self):
        with self._guard() as lease:
            with self.store._journal() as conn:
                self._recover(conn)
                if (
                    conn.execute("SELECT count(*) FROM execution_runs WHERE reserved=1").fetchone()[
                        0
                    ]
                    >= 2
                ):
                    return None
                rows = conn.execute(
                    "SELECT e.id FROM executions e LEFT JOIN execution_runs r ON r.execution_id=e.id "
                    "WHERE e.state='approved' AND r.execution_id IS NULL ORDER BY e.created_at,e.id"
                ).fetchall()
                for candidate in rows:
                    try:
                        row, binding, gateway, route, origin = self._fresh(conn, candidate["id"])
                    except (StoreError, ControlError, ProtocolDenied):
                        conn.execute(
                            "UPDATE executions SET reason='admission_denied' WHERE id=?",
                            (candidate["id"],),
                        )
                        continue
                    if binding.get("admission_mode") != self.admission_mode:
                        continue
                    limits = Limits.from_dict(binding["limits"])
                    count = conn.execute(
                        "SELECT count(*) FROM execution_runs WHERE gateway_id=? AND reserved=1",
                        (gateway.gateway,),
                    ).fetchone()[0]
                    if count >= limits.tunnels:
                        continue
                    self._unchanged(lease)
                    now = self.store._now()
                    attempt = uuid.uuid4().hex
                    start_by = min(
                        now + limits.admission_seconds,
                        row["approval_expires_at"],
                        row["proposal_expires_at"],
                    )
                    arguments = binding["arguments"]
                    if binding["operation"] == "workspace.script.v1":
                        from .scripts import ScriptManifest, ScriptRequest

                        script_request = ScriptRequest(
                            row["id"],
                            attempt,
                            ScriptManifest.from_dict(binding["script"]["manifest"]),
                            start_by,
                            authority_epoch=lease.epoch,
                            approval_digest=self._consent_digest(row),
                        )
                        arguments = {
                            "bundle_id": script_request.manifest.bundle_id,
                            "manifest_digest": script_request.manifest.digest,
                            "script_digest": script_request.digest,
                        }
                    request = ExecutionRequest(
                        workspace=self.store.workspace_id,
                        project=binding["authority"]["board"],
                        user=origin.user_id,
                        agent=origin.actor,
                        task=str(binding["task_number"]),
                        execution=row["id"],
                        attempt=attempt,
                        gateway=gateway.gateway,
                        node=route.node,
                        gateway_epoch=gateway.epoch,
                        node_epoch=route.epoch,
                        gateway_credential_version=gateway.credential_version,
                        target_credential_version=route.credential_version,
                        authority_epoch=lease.epoch,
                        enrollment=binding["enrollment"],
                        start_by=start_by,
                        run_until=now + limits.runtime_seconds,
                        limits=limits,
                        operation=binding["operation"],
                        arguments=arguments,
                        admission_mode=self.admission_mode,
                        approval_digest=(
                            self._consent_digest(row) if self.admission_mode is not None else None
                        ),
                    )
                    if isinstance(self.fence, _LocalAuthorityFence):
                        try:
                            self._admission_credentials(request, gateway, route)
                            self._unchanged(lease)
                        except CredentialError:
                            conn.execute(
                                "UPDATE executions SET reason='admission_denied' WHERE id=?",
                                (row["id"],),
                            )
                            continue
                    conn.execute(
                        "INSERT INTO execution_runs(execution_id,attempt_id,worker_id,gateway_id,"
                        "request,approval_digest,phase,reason,certainty,reserved,created_at,updated_at,run_until) "
                        "VALUES (?,?,?,?,?,?,'admitted','approval_consumed','unknown',1,?,?,?)",
                        (
                            row["id"],
                            request.attempt,
                            self._worker_id,
                            gateway.gateway,
                            canonical(request.document()).decode(),
                            self._consent_digest(row),
                            now,
                            now,
                            request.run_until,
                        ),
                    )
                    self.store._audit(conn, origin, row["id"], "execution.admitted")
                    return request, gateway, route
        return None

    def _run(self, conn, request):
        run = conn.execute(
            "SELECT * FROM execution_runs WHERE execution_id=?", (request.execution,)
        ).fetchone()
        if (
            run is None
            or run["attempt_id"] != request.attempt
            or run["worker_id"] != self._worker_id
            or run["request"] != canonical(request.document()).decode()
        ):
            raise StoreError("attempt_unavailable", 409)
        return run

    def _script_payload(self, request, conn, binding, principal):
        from .script_control import read_bundle
        from .script_transport import validate_envelope
        from .scripts import ScriptRequest

        _, manifest, script = read_bundle(
            self.store, conn, principal, binding["arguments"]["bundle_id"]
        )
        script_request = ScriptRequest(
            request.execution,
            request.attempt,
            manifest,
            request.start_by,
            authority_epoch=request.authority_epoch,
            approval_digest=request.approval_digest,
        )
        validate_envelope(request, script_request, script)
        return script_request, script

    def _check(self, conn, lease, request):
        run = self._run(conn, request)
        if (
            run["phase"] in ("terminal", "uncertain")
            or run["cancel_requested_at"] is not None
            or self._stop.is_set()
            or lease.epoch != request.authority_epoch
        ):
            raise StoreError("authority_changed", 409)
        row, binding, _, _, origin = self._fresh(conn, request.execution)
        if request.operation != binding["operation"]:
            raise StoreError("authority_changed", 409)
        if request.operation == "workspace.script.v1":
            self._script_payload(request, conn, binding, origin)
        if request.admission_mode != self.admission_mode or (
            request.admission_mode is not None
            and (
                binding.get("admission_mode") != request.admission_mode
                or request.approval_digest != self._consent_digest(row)
            )
        ):
            raise StoreError("authority_changed", 409)
        if not hmac.compare_digest(run["approval_digest"], self._consent_digest(row)):
            raise StoreError("authority_changed", 409)
        self._unchanged(lease)
        return run

    def _authorize(self, request, phase):
        try:
            stage = _PHASES[phase]
            with self._guard() as lease:
                with self.store._journal() as conn:
                    run = self._check(conn, lease, request)
                    if stage != run["stage"] + 1 or self.store._now() >= request.start_by:
                        return False
                    if stage >= 3 and self.admission_mode == ADMISSION_MODE:
                        permit = conn.execute(
                            "SELECT wire_permit FROM gateway_admissions WHERE execution_id=? "
                            "AND attempt_id=? AND authority_epoch=?",
                            (request.execution, request.attempt, request.authority_epoch),
                        ).fetchone()
                        if permit is None or not permit["wire_permit"]:
                            return False
                    conn.execute(
                        "UPDATE execution_runs SET stage=?,phase=?,updated_at=? WHERE execution_id=?",
                        (
                            stage,
                            "running" if stage == 4 else "dispatching",
                            self.store._now(),
                            request.execution,
                        ),
                    )
            return True
        except Exception:
            return False

    def _gateway_admission(self, request, phase, *, challenge=None):
        """Consume one exact permit under fresh local authority, then return.

        Explicit wire mode requires the immutable challenge read by SSHTransport
        on the already pinned/authenticated helper channel. The durable identity
        includes that nonce/channel/deadline and the exact approved attempt. The
        caller sends only after this method releases its fence/transaction.
        Legacy callback-only fixtures remain inert without explicit wire mode.
        Revocation committed before issuance wins; an issued permit is in flight.
        """
        if (
            not self.enabled
            or type(request) is not ExecutionRequest
            or phase not in ("ready", "admit")
        ):
            return False
        if self.admission_mode is not None:
            return self._issue_wire_permit(request, phase, challenge)
        if request.admission_mode is not None or challenge is not None:
            return False
        try:
            with self._guard() as lease:
                with self.store._journal() as conn:
                    run = self._check(conn, lease, request)
                    if self.store._now() >= request.start_by or run["stage"] < (
                        1 if phase == "ready" else 2
                    ):
                        return False
                    if phase == "admit":
                        # Exact attempt is already immutable in execution_runs.
                        # A lost callback acknowledgement never mints a second
                        # permit; the helper's nonce journal prevents replay too.
                        conn.execute(
                            "INSERT INTO gateway_admissions(execution_id,attempt_id,authority_epoch,issued_at) "
                            "VALUES (?,?,?,?)",
                            (request.execution, request.attempt, lease.epoch, self.store._now()),
                        )
            return True
        except Exception:
            return False

    @staticmethod
    def _wire_schema(conn):
        # Additive migration under the existing local participating fence and
        # FULL-synchronous Store transaction. Existing append-only/guard triggers
        # remain intact. Old local-only permits cannot become wire permits.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(gateway_admissions)")}
        for name in ("helper_channel", "helper_nonce", "wire_permit"):
            if name not in columns:
                conn.execute(f"ALTER TABLE gateway_admissions ADD COLUMN {name} TEXT")
        for name in ("helper_channel", "helper_nonce"):
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS gateway_admissions_{name} "
                f"ON gateway_admissions({name})"
            )

    def _issue_wire_permit(self, request, phase, challenge):
        if (
            phase != "admit"
            or self.admission_mode != ADMISSION_MODE
            or type(challenge) is not AdmissionChallenge
            or challenge.request != request
        ):
            return False
        try:
            with self._guard() as lease:
                with self.store._journal() as conn:
                    run = self._check(conn, lease, request)
                    if run["stage"] != 2:
                        return False
                    challenge.validate_time(self.store._now())
                    _, _, gateway, route, _ = self._fresh(conn, request.execution)
                    request.validate_binding(gateway, route, request.limits, self.store._now())
                    self._admission_credentials(request, gateway, route)
                    self._unchanged(lease)
                    self._wire_schema(conn)
                    permit = challenge.message("admission_permit")
                    conn.execute(
                        "INSERT INTO gateway_admissions(execution_id,attempt_id,authority_epoch,"
                        "issued_at,helper_channel,helper_nonce,wire_permit) VALUES (?,?,?,?,?,?,?)",
                        (
                            request.execution,
                            request.attempt,
                            lease.epoch,
                            self.store._now(),
                            challenge.channel,
                            challenge.nonce,
                            canonical(permit).decode(),
                        ),
                    )
                    # Time spent in current reads/migration must not extend the
                    # helper's short deadline. Failure rolls back the insertion.
                    challenge.validate_time(self.store._now())
            # The commit/fence have both finished before the caller may send.
            return permit
        except Exception:
            return False

    def _wire_admission(self, request, challenge):
        permit = self._gateway_admission(request, "admit", challenge=challenge)
        if type(permit) is not dict:
            raise ProtocolDenied("authority_denied")
        return permit

    @staticmethod
    def _credential_bindings(request, gateway, route):
        return (
            (
                gateway.credential_id,
                CredentialBinding(
                    request.workspace,
                    request.user,
                    gateway.gateway,
                    "",
                    gateway.account,
                    "gateway",
                    gateway.credential_version,
                ),
            ),
            (
                route.credential_id,
                CredentialBinding(
                    request.workspace,
                    request.user,
                    gateway.gateway,
                    route.node,
                    route.account,
                    "target",
                    route.credential_version,
                ),
            ),
        )

    def _custody(self, request, gateway, route):
        expected = self._credential_bindings(request, gateway, route)
        context = object()

        def authorize(credential_request, supplied):
            if (
                supplied is not context
                or credential_request.action != "acquire"
                or (
                    credential_request.enrollment.credential_id,
                    credential_request.enrollment.binding,
                )
                not in expected
                or not self._script_credential(request, credential_request)
            ):
                return False
            with self._guard() as lease:
                with self.store._journal() as conn:
                    self._check(conn, lease, request)
                    if self.store._now() >= request.start_by:
                        return False
            return True

        # Fresh authority before opening any existing custody/wrapping key.
        with self._guard() as lease:
            with self.store._journal() as conn:
                self._check(conn, lease, request)
        if self.wrapping_key_path is None:
            raise CredentialError("credential_unavailable")
        path = Path(self.store._path() if callable(self.store._path) else self.store._path)
        custody = CredentialStore(
            path.parent / "compute-credentials.sqlite3",
            self.wrapping_key_path,
            path.parent,
            authorize,
            clock=self.store.clock,
        )
        keys = []
        try:
            for credential_id, binding in expected:
                keys.append(
                    _CustodyKey(custody.acquire(credential_id, binding, context), credential_id)
                )
            return custody, keys
        except BaseException:
            for key in keys:
                key.discard()
            custody.close()
            raise

    def _admission_credentials(self, request, gateway, route):
        """Read authenticated active custody state before local permit commit.

        Already inside the authority fence and after exact current consent/task
        checks. Handle acquisition authenticates binding, version, expiry and
        revocation without converting a private key or performing network work.
        A pre-existing revocation therefore wins *before* durable admission.
        """
        if self.wrapping_key_path is None:
            raise CredentialError("credential_unavailable")
        expected = self._credential_bindings(request, gateway, route)
        context = object()

        def authorize(credential_request, supplied):
            return (
                supplied is context
                and credential_request.action == "acquire"
                and (
                    credential_request.enrollment.credential_id,
                    credential_request.enrollment.binding,
                )
                in expected
                and self._script_credential(request, credential_request)
            )

        path = Path(self.store._path() if callable(self.store._path) else self.store._path)
        with CredentialStore(
            path.parent / "compute-credentials.sqlite3",
            self.wrapping_key_path,
            path.parent,
            authorize,
            clock=self.store.clock,
        ) as custody:
            for credential_id, binding in expected:
                custody.acquire(credential_id, binding, context).close()

    def _script_credential(self, request, credential_request):
        if (
            request.operation != "workspace.script.v1"
            or credential_request.enrollment.binding.purpose != "target"
        ):
            return True
        from .script_control import _configured

        runtime = _configured(self.store, request.node)
        try:
            public = credential_request.enrollment.public_key.split()
            return (
                len(public) == 2
                and hashlib.sha256(base64.b64decode(public[1], validate=True)).hexdigest()
                == runtime.key_principal
            )
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _evidence(result, limit):
        if type(result.stdout) is not bytes or type(result.stderr) is not bytes:
            return {}
        size = len(result.stdout) + len(result.stderr)
        # Never truncate a token/key block before redaction. Oversize or binary
        # output is omitted wholesale. The adapter already bounds raw collection.
        output, kinds = "[omitted:too_long]", ["too_long"]
        if size <= min(limit, 4096):
            try:
                text = (result.stdout + result.stderr).decode("utf-8", errors="strict")
                text = "".join(c for c in text if c in "\n\t" or c.isprintable())
                output, kinds = redact.redact(text)
            except UnicodeError:
                output, kinds = "[omitted:binary]", []
        evidence = {
            "output": output,
            "redactions": kinds,
            "stdout_bytes": min(len(result.stdout), limit),
            "stderr_bytes": min(len(result.stderr), limit),
        }
        return (
            evidence
            if len(canonical(evidence)) <= MAX_RESULT_BYTES
            else {"output": "[omitted:too_long]"}
        )

    def _finish(self, request, result):
        # A late exact-attempt observed exit can settle a reservation. If current
        # authority is lost, retain only outcome metadata, never remote text.
        try:
            with self._guard() as lease:
                with self.store._journal() as conn:
                    self._check(conn, lease, request)
                    self._record_result(conn, request, result, publish=True)
                    return
        except Exception:
            pass
        with self.store._journal() as conn:
            self._record_result(conn, request, result, publish=False)

    def _record_result(self, conn, request, result, *, publish):
        run = self._run(conn, request)
        if run["phase"] == "terminal":
            return
        valid = type(result) is ExecutionResult
        known_exit = (
            valid
            and result.state == "completed"
            and run["stage"] == 4
            and type(result.exit_code) is int
            and (
                0 <= result.exit_code <= 255
                or (request.operation == "workspace.script.v1" and -255 <= result.exit_code < 0)
            )
        )
        issued = conn.execute(
            "SELECT 1 FROM gateway_admissions WHERE execution_id=?", (request.execution,)
        ).fetchone()
        # Before target authentication there can be no target operation. The
        # absence of a durable permit independently proves a non-run.
        not_started = run["stage"] < 3 or not issued
        known = known_exit or not_started
        reason = result.reason if valid and result.reason in _REASONS else "transport_unavailable"
        if not_started and run["cancel_requested_at"] is not None:
            reason = "cancelled_before_start"
        evidence = self._evidence(result, request.limits.output_bytes) if valid and publish else {}
        if request.operation == "workspace.script.v1":
            from .script_sandbox import ScriptResult
            from .scripts import project_result

            script_result = result.script_result if valid else None
            known_exit = (
                known_exit
                and type(script_result) is ScriptResult
                and (
                    script_result.state == "observed_exit"
                    and script_result.termination_confirmed
                    and script_result.exit_code == result.exit_code
                )
            )
            confirmed_nonrun = (
                run["stage"] == 4
                and valid
                and (
                    (result.state == "not_run" and result.reason in SCRIPT_NONRUN_CODES)
                    or (
                        type(script_result) is ScriptResult
                        and script_result.state in ("denied", "cancelled")
                        and script_result.termination_confirmed
                    )
                )
            )
            known = known_exit or not_started or confirmed_nonrun
            evidence = (
                project_result(script_result)
                if publish and type(script_result) is ScriptResult
                else {}
            )
            if len(canonical(evidence)) > MAX_RESULT_BYTES:
                evidence = {"output": "[omitted:too_long]"}
        conn.execute(
            "UPDATE execution_runs SET phase=?,reason=?,certainty=?,reserved=?,updated_at=?,"
            "exit_code=?,result=? WHERE execution_id=?",
            (
                "terminal" if known else "uncertain",
                reason,
                "known" if known else "unknown",
                0 if known else 1,
                self.store._now(),
                result.exit_code if known_exit else None,
                canonical(evidence).decode(),
                request.execution,
            ),
        )

    def settle_uncertain(self, execution_id, note):
        """Trusted operator reconciliation; deliberately absent from HTTP routes.

        Independent external evidence is the operator's responsibility. The
        original attempt identity remains a tombstone and cannot be retried.
        This does not grant permission to publish withheld remote output.
        """
        identifier(execution_id)
        if type(note) is not str or not note.strip() or len(note) > 1000:
            raise StoreError("invalid_operator_note", 400)
        with self._guard():
            with self.store._journal() as conn:
                row = conn.execute(
                    "SELECT phase FROM execution_runs WHERE execution_id=?", (execution_id,)
                ).fetchone()
                if row is None or row["phase"] != "uncertain":
                    raise StoreError("uncertain_execution_required", 409)
                conn.execute(
                    "UPDATE execution_runs SET phase='terminal',reason='operator_settled',"
                    "certainty='known',reserved=0,operator_note=?,updated_at=? WHERE execution_id=?",
                    (note, self.store._now(), execution_id),
                )
                conn.execute(
                    "INSERT INTO audit(object_id,action,user_id,created_at) VALUES (?,?,?,?)",
                    (execution_id, "execution.operator_settled", "operator", self.store._now()),
                )

    def _run_once(self):
        """Internal background iteration; a fixture may drive it synchronously."""
        if not self.enabled or self._stop.is_set():
            return False
        if isinstance(self.fence, _LocalAuthorityFence) and self.admission_mode is None:
            self.last_reason = "gateway_admission_unintegrated"
            return False
        try:
            claimed = self._claim()
        except Exception:
            self.last_reason = "admission_unavailable"
            return False
        if claimed is None:
            return False
        request, gateway, route = claimed
        custody, keys = None, []
        try:
            custody, keys = self._custody(request, gateway, route)
            script_arguments = {}
            if request.operation == "workspace.script.v1":
                with self._guard() as lease:
                    with self.store._journal() as conn:
                        self._check(conn, lease, request)
                        _, binding, _, _, origin = self._fresh(conn, request.execution)
                        script_request, script = self._script_payload(
                            request, conn, binding, origin
                        )
                script_arguments = {"script_request": script_request, "script": script}
            result = SSHTransport(
                self._authorize,
                enabled=True,
                limits=request.limits,
                driver=self._driver,
                wall=self.store.clock,
                admission_mode=self.admission_mode,
                gateway_admission=(
                    self._wire_admission if self.admission_mode is not None else None
                ),
            ).execute(
                request,
                gateway,
                route,
                *keys,
                cancelled=_Cancellation(self, request),
                **script_arguments,
            )
        except Exception:
            # No raw exception strings or key-bearing objects enter the journal.
            # Even an unknown connect/exec acknowledgement cannot cause a retry.
            with self.store._journal() as conn:
                stage = self._run(conn, request)["stage"]
            result = ExecutionResult(
                "not_started" if stage == 0 else "uncertain", "transport_unavailable"
            )
        finally:
            for key in keys:
                key.discard()
            if custody is not None:
                custody.close()
        try:
            self._finish(request, result)
        except Exception:
            # The committed claim remains reserved and will recover uncertain.
            self.last_reason = "result_unavailable"
        return True
