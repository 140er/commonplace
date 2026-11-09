"""Synthetic real crypto and filesystem tests; no SSH, operator keys or network."""

import builtins
import copy
import json
import os
import pickle
import sqlite3
from dataclasses import FrozenInstanceError, asdict, replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from commonplace.compute.credentials import (
    CredentialBinding,
    CredentialEnrollment,
    CredentialError,
    CredentialHandle,
    CredentialRequest,
    CredentialStore,
    initialize_wrapping_key,
    internal_transport_private_bytes,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX protected custody boundary")


@pytest.fixture
def custody(tmp_path):
    data = tmp_path / "data"
    service = tmp_path / "service-key"
    data.mkdir(mode=0o700)
    service.mkdir(mode=0o700)
    key_path = service / "compute.wrap"
    database = data / "compute.sqlite3"
    context = object()
    now = [1000.0]
    calls = []
    permission = [True]

    def authority(request, supplied_context):
        calls.append((request, supplied_context))
        if isinstance(permission[0], Exception):
            raise permission[0]
        return permission[0] if supplied_context is context else False

    args = (database, key_path, data, authority)
    initialize_wrapping_key(key_path, data, database_path=database)
    store = CredentialStore.initialize(*args, clock=lambda: now[0])
    instances = [store]

    def reopen():
        opened = CredentialStore(*args, clock=lambda: now[0])
        instances.append(opened)
        return opened

    state = SimpleNamespace(
        store=store,
        database=database,
        key_path=key_path,
        data=data,
        service=service,
        context=context,
        now=now,
        calls=calls,
        permission=permission,
        args=args,
        reopen=reopen,
        binding=CredentialBinding("workspace", "owner", "gateway", "node", "svc", "target", 1),
    )
    try:
        yield state
    finally:
        for instance in instances:
            instance.close()


def enroll(custody, *, binding=None, expires_at=2000.0):
    binding = binding or custody.binding
    enrollment = custody.store.generate(binding, custody.context, expires_at=expires_at)
    custody.store.activate(enrollment.credential_id, binding, custody.context)
    return enrollment


def acquire(custody, enrollment, *, store=None, context=None, binding=None):
    return (store or custody.store).acquire(
        enrollment.credential_id,
        binding or enrollment.binding,
        custody.context if context is None else context,
    )


def execute(custody, statement, parameters=()):
    with sqlite3.connect(custody.database) as conn:
        conn.execute(statement, parameters)


def rows(custody, table):
    assert table in {"compute_credentials", "compute_credential_keysets", "compute_credential_meta"}
    with sqlite3.connect(custody.database) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(f"SELECT * FROM {table}").fetchall()


def test_real_ed25519_roundtrip_reopen_and_only_public_enrollment(custody):
    enrollment = enroll(custody)
    assert set(asdict(enrollment)) == {"credential_id", "binding", "expires_at", "public_key"}
    assert enrollment.public_key.startswith("ssh-ed25519 ")
    with acquire(custody, enrollment) as handle:
        raw = internal_transport_private_bytes(handle)
    private = serialization.load_ssh_private_key(raw, password=None)
    public = serialization.load_ssh_public_key(enrollment.public_key.encode())
    public.verify(private.sign(b"synthetic-message"), b"synthetic-message")
    assert (
        private.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
        == enrollment.public_key
    )

    custody.store.close()
    reopened = custody.reopen()
    with acquire(custody, enrollment, store=reopened) as handle:
        assert internal_transport_private_bytes(handle) == raw
    assert isinstance(enrollment, CredentialEnrollment)
    json.dumps(asdict(enrollment))


def test_no_plaintext_private_keys_or_unwrapped_data_keys_on_disk(custody):
    enrollment = enroll(custody)
    with acquire(custody, enrollment) as handle:
        pem = internal_transport_private_bytes(handle)
    private = serialization.load_ssh_private_key(pem, password=None)
    seed = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    # Independently decrypt the synthetic wrap to check the real algorithm/AAD.
    meta = rows(custody, "compute_credential_meta")[0]
    keyset = rows(custody, "compute_credential_keysets")[0]
    aad = json.dumps(
        [
            "commonplace-compute-custody",
            1,
            "workspace-key",
            meta["store_id"],
            meta["wrapping_key_id"],
            "workspace",
            1,
        ],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    wrapped = keyset["wrapped_dek"]
    wrapping = custody.key_path.read_bytes()
    dek = AESGCM(wrapping).decrypt(wrapped[:12], wrapped[12:], aad)
    assert len(dek) == 32 and dek != wrapping
    for path in custody.data.rglob("*"):
        if path.is_file():
            stored = path.read_bytes()
            assert pem not in stored
            assert seed not in stored
            assert dek not in stored
            assert wrapping not in stored
            assert b"PRIVATE KEY" not in stored
    record = rows(custody, "compute_credentials")[0]
    assert len(record["encrypted_key"]) == len(pem) + 28
    assert len(record["state_tag"]) == 28
    assert sorted(p.name for p in custody.service.iterdir()) == ["compute.wrap"]
    assert custody.key_path.stat().st_mode & 0o777 == 0o600
    assert custody.database.stat().st_mode & 0o777 == 0o600


def test_random_keys_are_separate_per_workspace_and_nonces_are_fresh(custody):
    first = enroll(custody)
    second = enroll(custody, binding=replace(custody.binding, version=2))
    third = enroll(custody, binding=replace(custody.binding, workspace="other"))
    assert len({first.public_key, second.public_key, third.public_key}) == 3
    records = rows(custody, "compute_credentials")
    assert len({row["encrypted_key"][:12] for row in records}) == 3
    assert len({row["state_tag"][:12] for row in records}) == 3
    keysets = rows(custody, "compute_credential_keysets")
    assert len(keysets) == 2
    assert len({row["wrapped_dek"] for row in keysets}) == 2


def test_generation_is_pending_and_rotation_does_not_replace_an_enrolled_key(custody):
    first = enroll(custody)
    pending = custody.store.generate(replace(custody.binding, version=2), custody.context)
    assert pending.expires_at == custody.now[0] + 30 * 24 * 60 * 60
    with pytest.raises(CredentialError, match="credential_pending"):
        acquire(custody, pending)
    with acquire(custody, first) as handle:
        original = internal_transport_private_bytes(handle)
    custody.store.activate(pending.credential_id, pending.binding, custody.context)
    with acquire(custody, pending) as handle:
        assert internal_transport_private_bytes(handle) != original
    with acquire(custody, first) as handle:
        assert internal_transport_private_bytes(handle) == original


def test_callback_receives_exact_request_binding_action_and_context(custody):
    enrollment = enroll(custody)
    with acquire(custody, enrollment) as handle:
        internal_transport_private_bytes(handle)
    custody.store.revoke(enrollment.credential_id, enrollment.binding, custody.context)
    assert [request.action for request, _ in custody.calls] == [
        "generate",
        "activate",
        "acquire",
        "acquire",
        "revoke",
    ]
    for request, context in custody.calls:
        assert type(request) is CredentialRequest
        assert request.enrollment.binding == custody.binding
        assert request.enrollment.credential_id == enrollment.credential_id
        assert request.enrollment.expires_at == enrollment.expires_at
        assert context is custody.context
    assert custody.calls[0][0].enrollment.public_key == ""
    assert all(request.enrollment == enrollment for request, _ in custody.calls[1:])


@pytest.mark.parametrize("permission", [False, None, 1, "owner", {"owner": True}])
def test_callback_requires_literal_true_not_truthy_or_caller_owner_flag(custody, permission):
    custody.permission[0] = permission
    with pytest.raises(CredentialError, match="credential_denied"):
        custody.store.generate(custody.binding, custody.context)
    assert rows(custody, "compute_credentials") == []
    assert rows(custody, "compute_credential_keysets") == []


def test_authority_and_exceptions_are_rechecked_for_live_handles(custody):
    enrollment = enroll(custody)
    handle = acquire(custody, enrollment)
    custody.permission[0] = False
    with pytest.raises(CredentialError, match="credential_denied"):
        internal_transport_private_bytes(handle)
    custody.permission[0] = RuntimeError("synthetic-context-canary")
    with pytest.raises(CredentialError) as error:
        internal_transport_private_bytes(handle)
    assert str(error.value) == "credential_denied"
    assert "synthetic-context-canary" not in repr(error.value)
    assert error.value.__suppress_context__
    handle.close()


def test_unauthorized_context_denies_all_operations_without_mutation(custody):
    enrollment = enroll(custody)
    stranger = {"owner": True}
    with pytest.raises(CredentialError, match="credential_denied"):
        custody.store.generate(replace(custody.binding, version=2), stranger)
    for operation in (custody.store.activate, custody.store.revoke, custody.store.acquire):
        with pytest.raises(CredentialError, match="credential_denied"):
            operation(enrollment.credential_id, enrollment.binding, stranger)
    assert len(rows(custody, "compute_credentials")) == 1
    assert rows(custody, "compute_credentials")[0]["state"] == "active"


@pytest.mark.parametrize(
    "change",
    [
        {"workspace": "foreign"},
        {"owner": "foreign"},
        {"gateway": "foreign"},
        {"node": "foreign"},
        {"account": "foreign"},
        {"version": 2},
        {"purpose": "gateway", "node": ""},
    ],
)
def test_reference_does_not_grant_another_binding(custody, change):
    enrollment = enroll(custody)
    with pytest.raises(CredentialError, match="credential_denied"):
        acquire(custody, enrollment, binding=replace(enrollment.binding, **change))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace", "different"),
        ("owner", "different"),
        ("gateway", "different"),
        ("node", "different"),
        ("account", "other"),
        ("version", 2),
        ("credential_id", "cc_" + "0" * 32),
        ("expires_at", 3000.0),
        ("keyset_version", 2),
        ("public_key", "ssh-ed25519 synthetic-tamper"),
    ],
)
def test_all_persisted_metadata_is_authenticated_even_with_matching_caller(custody, field, value):
    enrollment = enroll(custody)
    execute(custody, f"UPDATE compute_credentials SET {field}=?", (value,))
    binding = enrollment.binding
    if field in asdict(binding):
        binding = replace(binding, **{field: value})
    credential_id = value if field == "credential_id" else enrollment.credential_id
    with pytest.raises(CredentialError):
        custody.store.acquire(credential_id, binding, custody.context)


def test_gateway_target_purpose_and_node_are_cryptographically_bound(custody):
    enrollment = enroll(custody)
    execute(custody, "UPDATE compute_credentials SET purpose='gateway',node=''")
    gateway_binding = replace(enrollment.binding, purpose="gateway", node="")
    with pytest.raises(CredentialError):
        custody.store.acquire(enrollment.credential_id, gateway_binding, custody.context)


@pytest.mark.parametrize("field", ["encrypted_key", "state_tag"])
def test_ciphertext_and_status_tag_tamper_fail_closed(custody, field):
    enrollment = enroll(custody)
    handle = acquire(custody, enrollment)
    raw = bytearray(rows(custody, "compute_credentials")[0][field])
    raw[-1] ^= 1
    execute(custody, f"UPDATE compute_credentials SET {field}=?", (bytes(raw),))
    with pytest.raises(CredentialError, match="credential_unavailable"):
        internal_transport_private_bytes(handle)
    with pytest.raises(CredentialError, match="credential_unavailable"):
        acquire(custody, enrollment)
    handle.close()


@pytest.mark.parametrize("malformed", [b"", b"short", "not-a-blob"])
def test_malformed_ciphertext_is_a_safe_typed_error(custody, malformed):
    enrollment = enroll(custody)
    execute(custody, "UPDATE compute_credentials SET encrypted_key=?", (malformed,))
    with pytest.raises(CredentialError, match="credential_unavailable"):
        acquire(custody, enrollment)


def test_cross_workspace_wrapped_dek_substitution_fails(custody):
    enrollment = enroll(custody)
    enroll(custody, binding=replace(custody.binding, workspace="second"))
    execute(
        custody,
        "UPDATE compute_credential_keysets SET wrapped_dek=(SELECT wrapped_dek "
        "FROM compute_credential_keysets WHERE workspace='second') WHERE workspace='workspace'",
    )
    with pytest.raises(CredentialError, match="credential_unavailable"):
        acquire(custody, enrollment)


@pytest.mark.parametrize("field", ["wrapped_dek", "version"])
def test_wrapped_dek_tamper_or_version_change_fails(custody, field):
    enrollment = enroll(custody)
    original = rows(custody, "compute_credential_keysets")[0]
    altered = b"\x00" * len(original[field]) if field == "wrapped_dek" else 2
    execute(custody, f"UPDATE compute_credential_keysets SET {field}=?", (altered,))
    with pytest.raises(CredentialError, match="credential_unavailable"):
        acquire(custody, enrollment)


def test_lost_workspace_keyset_cannot_be_silently_recreated(custody):
    enrollment = enroll(custody)
    execute(custody, "DELETE FROM compute_credential_keysets")
    with pytest.raises(CredentialError, match="credential_unavailable"):
        acquire(custody, enrollment)
    with pytest.raises(CredentialError, match="credential_unavailable"):
        custody.store.generate(replace(custody.binding, version=2), custody.context)
    assert rows(custody, "compute_credential_keysets") == []


def test_expiry_is_exclusive_and_rechecked_on_retained_handles(custody):
    enrollment = enroll(custody, expires_at=1001.0)
    handle = acquire(custody, enrollment)
    custody.now[0] = 1001.0
    for action in (
        lambda: acquire(custody, enrollment),
        lambda: internal_transport_private_bytes(handle),
    ):
        with pytest.raises(CredentialError, match="credential_expired"):
            action()
    with pytest.raises(CredentialError, match="credential_expired"):
        custody.store.activate(enrollment.credential_id, enrollment.binding, custody.context)
    custody.store.revoke(enrollment.credential_id, enrollment.binding, custody.context)
    assert rows(custody, "compute_credentials")[0]["state"] == "revoked"
    handle.close()


def test_revocation_is_durable_and_cannot_reactivate(custody):
    enrollment = enroll(custody)
    handle = acquire(custody, enrollment)
    custody.store.revoke(enrollment.credential_id, enrollment.binding, custody.context)
    custody.store.revoke(enrollment.credential_id, enrollment.binding, custody.context)
    for action in (
        lambda: internal_transport_private_bytes(handle),
        lambda: acquire(custody, enrollment, store=custody.reopen()),
        lambda: custody.store.activate(
            enrollment.credential_id, enrollment.binding, custody.context
        ),
    ):
        with pytest.raises(CredentialError, match="credential_revoked"):
            action()
    handle.close()


def test_editing_revocation_or_pending_state_does_not_authorize(custody):
    enrollment = enroll(custody)
    custody.store.revoke(enrollment.credential_id, enrollment.binding, custody.context)
    execute(custody, "UPDATE compute_credentials SET state='active'")
    with pytest.raises(CredentialError, match="credential_unavailable"):
        acquire(custody, enrollment)


@pytest.mark.parametrize(
    "failure", ["missing", "wrong", "short", "empty", "mode", "directory-mode"]
)
def test_wrong_lost_or_unsafe_wrapping_keys_disable_open_and_existing_handles(custody, failure):
    enrollment = enroll(custody)
    handle = acquire(custody, enrollment)
    if failure == "missing":
        custody.key_path.unlink()
    elif failure == "wrong":
        custody.key_path.write_bytes(b"x" * 32)
    elif failure == "short":
        custody.key_path.write_bytes(b"x" * 31)
    elif failure == "empty":
        custody.key_path.write_bytes(b"")
    elif failure == "mode":
        custody.key_path.chmod(0o644)
    else:
        custody.service.chmod(0o755)
    for action in (custody.reopen, lambda: internal_transport_private_bytes(handle)):
        with pytest.raises(CredentialError, match="credential_unavailable"):
            action()
    if failure == "missing":
        assert not custody.key_path.exists()
    handle.close()


@pytest.mark.parametrize("which", ["key", "key-parent", "database", "data-parent"])
def test_symlink_components_are_refused(custody, which):
    enrollment = enroll(custody)
    handle = acquire(custody, enrollment)
    path = {
        "key": custody.key_path,
        "key-parent": custody.service,
        "database": custody.database,
        "data-parent": custody.data,
    }[which]
    target = path.with_name(path.name + "-moved")
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    with pytest.raises(CredentialError, match="credential_unavailable"):
        custody.reopen()
    with pytest.raises(CredentialError, match="credential_unavailable"):
        internal_transport_private_bytes(handle)
    handle.close()


@pytest.mark.parametrize("which", ["key", "database"])
def test_hardlinked_files_are_refused(custody, which):
    path = custody.key_path if which == "key" else custody.database
    os.link(path, path.with_name(path.name + "-hardlink"))
    with pytest.raises(CredentialError, match="credential_unavailable"):
        custody.reopen()


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_sqlite_sidecar_symlinks_are_refused_before_open(custody, suffix):
    target = custody.service / "synthetic-sidecar-target"
    target.write_bytes(b"do-not-touch")
    target.chmod(0o600)
    custody.database.with_name(custody.database.name + suffix).symlink_to(target)
    with pytest.raises(CredentialError, match="credential_unavailable"):
        custody.reopen()
    assert target.read_bytes() == b"do-not-touch"


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o400, 0o700])
def test_database_permissions_are_strict(custody, mode):
    custody.database.chmod(mode)
    with pytest.raises(CredentialError, match="credential_unavailable"):
        custody.reopen()


def test_key_cannot_live_anywhere_in_protected_data_tree(custody):
    for subtree in ("control", "workspaces", "backups"):
        directory = custody.data / subtree
        directory.mkdir(mode=0o700)
        forbidden = directory / "wrap"
        with pytest.raises(CredentialError, match="credential_unavailable"):
            initialize_wrapping_key(forbidden, custody.data, database_path=custody.database)
        assert not forbidden.exists()
        with pytest.raises(CredentialError, match="credential_unavailable"):
            CredentialStore(custody.database, forbidden, custody.data, lambda *_: True)


def test_explicit_key_initialization_refuses_overwrite_and_unprotected_parent(custody):
    original = custody.key_path.read_bytes()
    with pytest.raises(CredentialError, match="credential_unavailable"):
        initialize_wrapping_key(custody.key_path, custody.data, database_path=custody.database)
    assert custody.key_path.read_bytes() == original
    custody.service.chmod(0o755)
    absent = custody.service / "not-created"
    with pytest.raises(CredentialError, match="credential_unavailable"):
        initialize_wrapping_key(absent, custody.data, database_path=custody.data / "new.sqlite3")
    assert not absent.exists()


def test_open_and_requests_never_initialize_missing_store_or_key(custody):
    custody.store.close()
    custody.database.unlink()
    custody.key_path.unlink()
    for action in (
        lambda: CredentialStore(*custody.args),
        lambda: CredentialStore.initialize(*custody.args),
    ):
        with pytest.raises(CredentialError, match="credential_unavailable"):
            action()
        assert not custody.key_path.exists()
        assert not custody.database.exists()
    initialize_wrapping_key(custody.key_path, custody.data, database_path=custody.database)
    with pytest.raises(CredentialError, match="credential_unavailable"):
        CredentialStore(*custody.args)
    assert not custody.database.exists()


def test_custody_initialization_is_not_recovery_and_preserves_existing_data(custody):
    enrollment = enroll(custody)
    with pytest.raises(CredentialError, match="credential_unavailable"):
        CredentialStore.initialize(*custody.args)
    with acquire(custody, enrollment) as handle:
        assert internal_transport_private_bytes(handle).startswith(
            b"-----BEGIN OPENSSH PRIVATE KEY"
        )
    assert len(rows(custody, "compute_credentials")) == 1


def test_explicit_initializer_cannot_regenerate_lost_key_over_existing_database(custody):
    enrollment = enroll(custody)
    custody.key_path.unlink()
    with pytest.raises(CredentialError, match="credential_unavailable"):
        initialize_wrapping_key(custody.key_path, custody.data, database_path=custody.database)
    assert not custody.key_path.exists()
    with pytest.raises(CredentialError, match="credential_unavailable"):
        acquire(custody, enrollment)


def test_missing_crypto_fails_closed_without_fallback_or_generated_files(custody, monkeypatch):
    enrollment = enroll(custody)
    handle = acquire(custody, enrollment)
    original_import = builtins.__import__

    def without_crypto(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("synthetic-crypto-unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_crypto)
    for action in (
        custody.reopen,
        lambda: internal_transport_private_bytes(handle),
        lambda: custody.store.generate(replace(custody.binding, version=2), custody.context),
        lambda: initialize_wrapping_key(
            custody.service / "new-key", custody.data, database_path=custody.data / "new.sqlite3"
        ),
    ):
        with pytest.raises(CredentialError, match="credential_unavailable"):
            action()
    assert len(rows(custody, "compute_credentials")) == 1
    assert not (custody.service / "new-key").exists()
    assert not (custody.data / "new.sqlite3").exists()
    handle.close()


@pytest.mark.parametrize("kind", ["fifo", "directory", "oversized"])
def test_nonregular_and_oversized_key_files_fail_without_blocking(custody, kind):
    custody.key_path.unlink()
    if kind == "fifo":
        os.mkfifo(custody.key_path, mode=0o600)
    elif kind == "directory":
        custody.key_path.mkdir(mode=0o700)
    else:
        custody.key_path.write_bytes(b"x" * 33)
        custody.key_path.chmod(0o600)
    with pytest.raises(CredentialError, match="credential_unavailable"):
        custody.reopen()


def test_wrong_key_file_owner_fails_safe_open(custody, monkeypatch):
    inode = custody.key_path.stat().st_ino
    original_fstat = os.fstat

    def foreign_owner(fd):
        info = original_fstat(fd)
        if info.st_ino == inode:
            fields = list(info)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, "fstat", foreign_owner)
    with pytest.raises(CredentialError, match="credential_unavailable"):
        custody.reopen()


def test_initialization_coexists_with_existing_compute_tables(custody):
    custody.store.close()
    custody.database.unlink()
    with sqlite3.connect(custody.database) as conn:
        conn.execute("CREATE TABLE compute_nodes(id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO compute_nodes VALUES ('existing-node')")
    custody.database.chmod(0o600)
    with CredentialStore.initialize(*custody.args) as store:
        store.generate(custody.binding, custody.context)
    with sqlite3.connect(custody.database) as conn:
        assert conn.execute("SELECT * FROM compute_nodes").fetchall() == [("existing-node",)]


@pytest.mark.parametrize("field", ["verifier", "format_version", "store_id", "wrapping_key_id"])
def test_corrupt_custody_header_fails_reopen(custody, field):
    value = b"x" * 48 if field == "verifier" else 2 if field == "format_version" else "invalid"
    execute(custody, f"UPDATE compute_credential_meta SET {field}=?", (value,))
    with pytest.raises(CredentialError, match="credential_unavailable"):
        custody.reopen()


def test_handles_hide_keys_context_and_paths_and_cannot_serialize_or_copy(custody):
    enrollment = enroll(custody)
    handle = acquire(custody, enrollment)
    assert repr(handle) == str(handle) == "<CredentialHandle redacted>"
    assert enrollment.credential_id not in repr(handle)
    assert str(custody.key_path) not in repr(handle)
    for serialize in (pickle.dumps, json.dumps, asdict, vars, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            serialize(handle)
    with pytest.raises(CredentialError, match="credential_denied"):
        handle._private_bytes_for_transport(object())
    with pytest.raises(CredentialError, match="credential_denied"):
        CredentialHandle(custody.store, enrollment, custody.context)
    with pytest.raises(CredentialError, match="credential_denied"):
        custody.store._transport_bytes(enrollment, custody.context)
    with pytest.raises(CredentialError, match="credential_denied"):
        internal_transport_private_bytes(enrollment)
    handle.close()
    handle.close()
    with pytest.raises(CredentialError, match="credential_closed"):
        internal_transport_private_bytes(handle)


def test_closed_store_disables_retained_handle(custody):
    enrollment = enroll(custody)
    with acquire(custody, enrollment) as handle:
        custody.store.close()
        with pytest.raises(CredentialError, match="credential_closed"):
            internal_transport_private_bytes(handle)
    with pytest.raises(CredentialError, match="credential_closed"):
        handle.__enter__()


@pytest.mark.parametrize(
    "change",
    [
        {"workspace": ""},
        {"owner": " owner"},
        {"gateway": "../gateway"},
        {"node": ""},
        {"node": "n" * 129},
        {"account": "-oProxyCommand"},
        {"account": "user@host"},
        {"account": "user;id"},
        {"account": "svc\n"},
        {"purpose": "arbitrary"},
        {"purpose": "gateway"},
        {"version": True},
        {"version": 0},
        {"version": 1.0},
        {"version": 2**31},
    ],
)
def test_bindings_reject_unbounded_or_ambiguous_values(custody, change):
    with pytest.raises(CredentialError, match="invalid_credential_binding"):
        replace(custody.binding, **change)


def test_bindings_are_immutable_reject_unknown_fields_and_support_separate_gateway_key(custody):
    with pytest.raises(FrozenInstanceError):
        custody.binding.account = "root"
    with pytest.raises(TypeError):
        CredentialBinding(**(asdict(custody.binding) | {"owner_approved": True}))
    with pytest.raises(CredentialError, match="invalid_credential_binding"):
        custody.store.generate(asdict(custody.binding), custody.context)
    target = enroll(custody)
    gateway = enroll(custody, binding=replace(custody.binding, node="", purpose="gateway"))
    assert target.public_key != gateway.public_key


@pytest.mark.parametrize("expiry", [True, 1000.0, 999, float("nan"), float("inf"), "2000", -1])
def test_invalid_or_elapsed_expiry_never_persists_credentials(custody, expiry):
    with pytest.raises(CredentialError):
        custody.store.generate(custody.binding, custody.context, expires_at=expiry)
    assert rows(custody, "compute_credentials") == []


def test_nonfinite_clock_disables_retained_handles(custody):
    enrollment = enroll(custody)
    handle = acquire(custody, enrollment)
    custody.now[0] = float("nan")
    with pytest.raises(CredentialError, match="credential_unavailable"):
        internal_transport_private_bytes(handle)
    handle.close()


def test_unknown_credential_id_and_duplicate_version_cannot_substitute(custody):
    enrollment = enroll(custody)
    with pytest.raises(CredentialError, match="credential_denied"):
        custody.store.acquire("cc_" + "0" * 32, custody.binding, custody.context)
    with pytest.raises(CredentialError, match="credential_unavailable"):
        custody.store.generate(custody.binding, custody.context)
    assert len(rows(custody, "compute_credentials")) == 1
    with acquire(custody, enrollment) as handle:
        assert internal_transport_private_bytes(handle)
