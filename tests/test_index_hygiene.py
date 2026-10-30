"""Encoding validation and adversarial non-base64 message text."""

import base64
import random

import pytest

from mempalace.index_hygiene import find_base64_spans, index_text


def payload(size=300, prefix=b"", urlsafe=False):
    data = prefix + random.Random(size).randbytes(size - len(prefix))
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    return data, encode(data).decode()


@pytest.mark.parametrize(
    ("prefix", "mime"),
    [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"RIFF1234WEBP", "image/webp"),
        (b"%PDF-1.7", "application/pdf"),
        (b"RIFF1234WAVE", "binary"),
        (b"", "binary"),
    ],
)
@pytest.mark.parametrize("urlsafe", [False, True])
def test_magic_offsets_and_verbatim(prefix, mime, urlsafe):
    data, encoded = payload(prefix=prefix, urlsafe=urlsafe)
    original = f"前文 🐈\nimage='{encoded}'; after needle"
    [span] = find_base64_spans(original)
    assert original[span.start : span.end] == encoded
    assert span.mime == mime
    assert span.decoded_bytes == len(data)
    assert index_text(original) == f"前文 🐈\nimage='[base64 {mime} 300 B]'; after needle"
    assert encoded in original


@pytest.mark.parametrize("size", [150, 151, 152, 153, 299, 300, 301])
def test_padding_and_threshold(size):
    _, encoded = payload(size)
    assert bool(find_base64_spans(encoded)) == (len(encoded.rstrip("=")) >= 200)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Ordinary conversation with words and punctuation.",
        "/project/" + "some-long-component/with-many-parts/" * 30,
        "https://example.test/" + "source-maps/build-output/" * 30 + "?v=123",
        "0123456789abcdef" * 40,
        "ABCDEF0123456789" * 40,
        "a" * 400,
        "const a={x:1,y:2};function f(x){return x+1;}" * 30,
        ".button{color:red;background:blue;padding:0;margin:1px;}" * 30,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "data:text/plain,hello%20world",
        "data:image/png;base64," + "A" * 400,
    ],
)
def test_negative_text_is_unchanged(text):
    assert find_base64_spans(text) == []
    assert index_text(text) == text


def test_rejects_entire_mixed_alphabet_run_and_malformed_padding():
    _, encoded = payload()
    assert "/" in encoded and "+" in encoded
    for invalid in (
        encoded + "-___",
        "_---" + encoded,
        encoded[:200] + "=" + encoded[201:],
        encoded + "=",
        encoded + "====",
        encoded[:-1],
    ):
        assert find_base64_spans(invalid) == []


def test_rejects_noncanonical_pad_bits():
    _, encoded = payload(151)
    assert encoded.endswith("==")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    last = alphabet.index(encoded[-3])
    invalid = encoded[:-3] + alphabet[last + 1] + "=="
    assert base64.b64decode(invalid, validate=True) == base64.b64decode(encoded)
    assert find_base64_spans(invalid) == []


def test_data_uri_and_long_jwt_segments_follow_the_encoding_rule():
    _, encoded = payload(prefix=b"%PDF-1.7")
    # Claimed MIME is untrusted; valid long JWT segments are base64 too.
    text = f"data:image/png;base64,{encoded} short.{encoded}.signature"
    spans = find_base64_spans(text)
    assert len(spans) == 2
    assert all(span.mime == "application/pdf" for span in spans)
    assert index_text(text).startswith("data:image/png;base64,[base64 application/pdf 300 B]")


def test_multiple_and_wrapped_runs():
    _, encoded = payload()
    text = encoded + "\n" + encoded
    assert len(find_base64_spans(text)) == 2
    assert index_text(text) == "[base64 binary 300 B]\n[base64 binary 300 B]"
    wrapped = "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    assert index_text(wrapped) == wrapped


def test_no_valid_substring_of_long_mixed_path_is_removed():
    _, encoded = payload()
    path = "/generated-files/" + encoded + "/asset-output"
    assert index_text(path) == path


def test_entropy_cutoff_is_strict():
    # Eight symbols occur twice and sixteen once per 32 characters: H=4.5.
    exactly = ("ABCDEFGH" * 2 + "IJKLMNOPQRSTUVWX") * 8
    assert find_base64_spans(exactly) == []
    above = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef" * 8  # H=5
    assert len(find_base64_spans(above)) == 1


@pytest.mark.parametrize("urlsafe", [False, True])
def test_random_canonical_payloads_round_trip_across_padding_sizes(urlsafe):
    for size in range(150, 250):
        data, encoded = payload(size, urlsafe=urlsafe)
        [span] = find_base64_spans(encoded)
        assert (span.start, span.end, span.decoded_bytes) == (0, len(encoded), len(data))
