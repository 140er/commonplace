"""M02/M03: framing controls only; these synthetic bytes prove no cryptography."""

from dataclasses import FrozenInstanceError, replace

import pytest

from commonplace.sealed import SealedEnvelope, SealedStoreError, ServerWriter, parse_envelope


def envelope(kind="memory", size=48):
    return SealedEnvelope(
        1, kind, "a" * 32, "b" * 32, b"\x00\xff\r\n" * 3, bytes(n % 256 for n in range(size))
    )


@pytest.mark.parametrize(
    "extra",
    [
        "plaintext",
        "content",
        "text",
        "key",
        "raw_key",
        "code",
        "recovery_code",
        "embedding",
        "vector",
        "workspace",
        "workspace_id",
        "author",
        "user_id",
        "trust",
        "written_at",
    ],
)
def test_wire_record_rejects_plaintext_keys_embeddings_and_stamps(extra):
    packet = envelope().to_wire()
    packet[extra] = "synthetic-forbidden-field"
    with pytest.raises(SealedStoreError) as failure:
        parse_envelope(packet)
    assert failure.value.code == "INVALID_RECORD"
    assert "synthetic-forbidden-field" not in str(failure.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format_version", True),
        ("format_version", "1"),
        ("format_version", 2),
        ("kind", "passkey"),
        ("kind", "both"),
        ("kind", "MEMORY"),
        ("record_id", "A" * 32),
        ("key_id", "b" * 31),
        ("record_id", "../"),
        ("nonce", "AAAA"),
        ("nonce", "!!!!"),
        ("nonce", "é"),
        ("nonce", "AP8NCgD/DQoA/w0K="),
        ("nonce", b"AP8NCgD/DQoA/w0K"),
        ("ciphertext", "AA==\n"),
        ("ciphertext", "AB=="),
        ("ciphertext", ""),
        ("ciphertext", None),
    ],
)
def test_version_kind_lengths_and_printed_only(field, value):
    packet = envelope("printed-recovery").to_wire()
    packet[field] = value
    with pytest.raises(SealedStoreError) as failure:
        parse_envelope(packet)
    assert failure.value.code == "INVALID_RECORD"


@pytest.mark.parametrize("kind", ["memory", "printed-recovery"])
def test_canonical_wire_preserves_opaque_bytes_and_immutable_types(kind):
    original = envelope(kind)
    assert parse_envelope(original.to_wire()) == original
    with pytest.raises(FrozenInstanceError):
        original.kind = "memory"
    assert original.ciphertext.hex() not in repr(original)


@pytest.mark.parametrize("size", [0, 15, 1_048_577])
def test_memory_length_refusal_never_truncates(size):
    with pytest.raises(SealedStoreError) as failure:
        envelope(size=size)
    assert failure.value.code == "INVALID_RECORD"


def test_exact_size_boundaries_and_no_raw_printed_key():
    for size in (16, 1_048_576):
        record = replace(envelope(), ciphertext=b"\xff" * size)
        assert parse_envelope(record.to_wire()).ciphertext == record.ciphertext
    for size in (16, 32, 47, 49):
        with pytest.raises(SealedStoreError):
            replace(envelope("printed-recovery"), ciphertext=b"x" * size)
    with pytest.raises(SealedStoreError):
        replace(envelope(), nonce=bytearray(12))


@pytest.mark.parametrize(
    "field", ["format_version", "kind", "record_id", "key_id", "nonce", "ciphertext"]
)
def test_missing_field_refuses(field):
    packet = envelope().to_wire()
    del packet[field]
    with pytest.raises(SealedStoreError) as failure:
        parse_envelope(packet)
    assert failure.value.code == "INVALID_RECORD"


@pytest.mark.parametrize(
    ("field", "value"),
    [("user_id", "other-user"), ("connection_id", "other-connection"), ("trust", "untrusted")],
)
def test_writer_fields_are_immutable(field, value):
    writer = ServerWriter("synthetic-user", "", "trusted")
    with pytest.raises(FrozenInstanceError):
        setattr(writer, field, value)


def test_writer_is_internal_validated_metadata():
    writer = ServerWriter("synthetic-user", "", "trusted")
    for changes in ({"user_id": ""}, {"connection_id": None}, {"trust": "owner"}):
        with pytest.raises(SealedStoreError):
            replace(writer, **changes)
