"""Encoding validation and adversarial non-base64 message text."""

import base64
import random
import re
import sys
import time
import unicodedata

import pytest
from hypothesis import given, settings, strategies as st

from mempalace.index_hygiene import (
    base64_candidate_ranges,
    find_base64_spans,
    index_text,
    is_word_character,
)


def test_candidate_detection_is_bounded_on_adversarial_short_runs():
    # Without the start boundary, each 199-character run is retried 199 times.
    text = ("A" * 199 + " ") * 20_000
    linear_scan = re.compile(r"[A-Za-z0-9+/_\-]+").findall
    candidate_times, baseline_times = [], []
    for _ in range(3):
        start = time.perf_counter()
        assert base64_candidate_ranges(text) == []
        candidate_times.append(time.perf_counter() - start)
        start = time.perf_counter()
        assert len(linear_scan(text)) == 20_000
        baseline_times.append(time.perf_counter() - start)
    # Scale to the machine's speed; the old regex takes about 50x the
    # linear scan, while the boundary-checked regex takes about 5x.
    assert min(candidate_times) < 20 * min(baseline_times)
    assert min(candidate_times) < 2.0


def test_candidate_ranges_keep_rejected_runs_whole():
    rejected = "A" * 200 + "_+/==="
    text = "A" * 199 + " " + rejected + " end"
    assert base64_candidate_ranges(text) == [(200, 200 + len(rejected))]


@pytest.mark.parametrize("size", [150, 151, 152])
@pytest.mark.parametrize(
    "before,after",
    [
        ("é", ""),
        ("", "é"),
        ("Ω", "漢"),
        ("٢", ""),
        ("", "Ⅳ"),
    ],
)
def test_unicode_letters_and_numbers_preserve_adjacent_runs(size, before, after):
    _, encoded = payload(size)
    text = before + encoded + after
    assert find_base64_spans(text) == []
    assert index_text(text) == text
    assert base64_candidate_ranges(text) == [(len(before), len(before) + len(encoded))]


@pytest.mark.parametrize("form", ["NFC", "NFD"])
@pytest.mark.parametrize("before,after", [("café", ""), ("", "élan")])
def test_composed_and_decomposed_word_edges_are_unchanged(form, before, after):
    _, encoded = payload()
    before, after = (unicodedata.normalize(form, part) for part in (before, after))
    text = before + encoded + after
    assert find_base64_spans(text) == []
    assert index_text(text) == text


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_delimited_spans_preserve_original_unicode_offsets(form):
    _, encoded = payload()
    before = unicodedata.normalize(form, "café") + " '"
    after = "' " + unicodedata.normalize(form, "élan")
    text = before + encoded + after
    [span] = find_base64_spans(text)
    assert (span.start, span.end) == (len(before), len(before) + len(encoded))
    assert text[span.start : span.end] == encoded
    assert index_text(text) == before + span.placeholder + after


@pytest.mark.parametrize("size", [150, 151, 152])
@pytest.mark.parametrize("edge", ["\u0301", "\u093e", "\u20dd", "\u200c", "\u200d"])
@pytest.mark.parametrize("side", ["before", "after"])
def test_marks_and_joiners_at_document_edges(size, edge, side):
    _, encoded = payload(size)
    before, after = (edge, "") if side == "before" else ("", edge)
    text = before + encoded + after
    if edge in "\u200c\u200d":
        # At either outer edge, ZWJ/ZWNJ look through to the document delimiter.
        [span] = find_base64_spans(text)
        assert index_text(text) == before + span.placeholder + after
    else:
        assert find_base64_spans(text) == []
        assert index_text(text) == text
    assert base64_candidate_ranges(text) == [(len(before), len(before) + len(encoded))]


def test_all_unicode_marks_connectors_and_formats_preserve_visible_edges():
    _, encoded = payload()
    for codepoint in range(sys.maxunicode + 1):
        edge = chr(codepoint)
        category = unicodedata.category(edge)
        if category.startswith("M") or category in ("Pc", "Cf"):
            if category == "Cf":
                # An outer Cf reaches the document delimiter; ZWSP itself is a separator.
                assert len(find_base64_spans(edge + encoded)) == 1, f"before U+{codepoint:04X}"
                assert len(find_base64_spans(encoded + edge)) == 1, f"after U+{codepoint:04X}"
            else:
                assert find_base64_spans(edge + encoded) == [], f"before U+{codepoint:04X}"
                assert find_base64_spans(encoded + edge) == [], f"after U+{codepoint:04X}"
            if edge != "\u200b":
                assert find_base64_spans("x" + edge + encoded) == [], f"before U+{codepoint:04X}"
                assert find_base64_spans(encoded + edge + "x") == [], f"after U+{codepoint:04X}"


@pytest.mark.parametrize(
    "before,after",
    [
        pytest.param("\ufeff", "", id="bom-at-document-start"),
        pytest.param("notes\n\ufeff", "\n", id="bom-mid-text"),
        pytest.param("see ", "\u200b next", id="zwsp-after-run"),
        pytest.param("สวัสดี\u200b", "\u200bครับ", id="zwsp-between-words"),
        pytest.param("שלום \u200e", "", id="lrm-before-run"),
        pytest.param("value \u2068", "\u2069 sent", id="fsi-pdi-isolates"),
        pytest.param("x ", "\u00ad", id="trailing-soft-hyphen"),
    ],
)
def test_format_characters_keep_delimited_payload_coverage(before, after, caplog):
    from mempalace.stored_text import IndexSource, index_text as hinted_index_text

    _, encoded = payload(prefix=b"\x89PNG\r\n\x1a\n")
    text = before + encoded + after
    [span] = find_base64_spans(text)
    assert (span.start, span.end) == (len(before), len(before) + len(encoded))
    expected = before + span.placeholder + after
    assert index_text(text) == expected
    source = IndexSource(text)
    assert hinted_index_text(text, source.metadata(0, len(text))) == expected
    assert "Ignoring stale or invalid index span hint" not in caplog.text


def test_all_letters_and_numbers_keep_word_edges_after_decomposition():
    for codepoint in range(sys.maxunicode + 1):
        char = chr(codepoint)
        if unicodedata.category(char)[0] in "LN":
            decomposed = unicodedata.normalize("NFD", char)
            assert is_word_character(decomposed[0]), f"first U+{codepoint:04X}"
            assert is_word_character(decomposed[-1]), f"last U+{codepoint:04X}"


@pytest.mark.parametrize("before,after", [("…", "—"), ("«", "»"), ("\u2003", "\n"), ("🧭", "🌿")])
def test_unicode_punctuation_and_whitespace_delimit_runs(before, after):
    _, encoded = payload()
    [span] = find_base64_spans(before + encoded + after)
    assert (span.start, span.end) == (len(before), len(before) + len(encoded))


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
    assert span.media_type == mime
    assert span.decoded_bytes == len(data)
    assert index_text(original) == f"前文 🐈\nimage='[base64 {mime} 300 B]'; after needle"
    assert encoded in original


@pytest.mark.parametrize("size", [147, 148, 149, 150, 151, 152, 153, 299, 300, 301])
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
    assert all(span.media_type == "application/pdf" for span in spans)
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


@pytest.mark.parametrize("shape", ["IMG={}", "?image={}&", "--data={}"])
def test_assignment_delimiters_do_not_hide_payloads(shape):
    _, encoded = payload(prefix=b"\x89PNG\r\n\x1a\n")
    text = shape.format(encoded)
    [span] = find_base64_spans(text)
    assert text[span.start : span.end] == encoded
    assert index_text(text) == shape.format("[base64 image/png 300 B]")


@pytest.mark.parametrize(
    ("prefix", "media_type"),
    [
        (b"wOF2", "font/woff2"),
        (b'<svg xmlns="http://www.w3.org/2000/svg">', "image/svg+xml"),
        (b'<?xml version="1.0"?>\n<svg>', "image/svg+xml"),
        (b"PK\x03\x04", "application/zip"),
        (b"PK\x05\x06", "application/zip"),
        (b"PK\x07\x08", "application/zip"),
        (b"\x1f\x8b\x08", "application/gzip"),
        (b"<svgish>", "binary"),
        (b'<?xml version="1.0"?><html>', "binary"),
    ],
)
def test_additional_media_headers(prefix, media_type):
    _, encoded = payload(prefix=prefix)
    [span] = find_base64_spans(encoded)
    assert span.media_type == media_type


def test_public_placeholder_format_and_parser():
    from mempalace.index_hygiene import PLACEHOLDER_FORMAT, PLACEHOLDER_RE, Base64Span

    for media_type in (
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/svg+xml",
        "application/pdf",
        "application/zip",
        "application/gzip",
        "font/woff2",
        "binary",
    ):
        expected = f"[base64 {media_type} 245760 B]"
        assert PLACEHOLDER_FORMAT.format(media_type=media_type, decoded_bytes=245760) == expected
        assert Base64Span(0, 327680, media_type, 245760).placeholder == expected
        assert PLACEHOLDER_RE.fullmatch(expected).groupdict() == {
            "media_type": media_type,
            "decoded_bytes": "245760",
        }
    for invalid in (
        "[base64 binary 0 B]",
        "[base64 binary -1 B]",
        "[base64 binary 01 B]",
        "[base64 unknown 10 B]",
        "[base64 binary 10 bytes]",
    ):
        assert PLACEHOLDER_RE.fullmatch(invalid) is None


@pytest.mark.parametrize("width", [64, 76])
def test_short_line_wrapping_is_deliberately_retained(width):
    _, encoded = payload()
    wrapped = "\n".join(encoded[i : i + width] for i in range(0, len(encoded), width))
    assert index_text(wrapped) == wrapped


def test_alphabetic_word_glued_to_run_is_indistinguishable_from_base64():
    _, encoded = payload()
    text = "word" + encoded
    [span] = find_base64_spans(text)
    assert (span.start, span.end, span.decoded_bytes) == (0, len(text), 303)


def test_base32_and_high_entropy_camel_case_can_meet_the_encoding_rule():
    base32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" * 8
    camel = (
        "AlphaBravoCharlieDeltaEchoFoxtrotGolfHotelIndiaJulietKiloLimaMikeNovemberOscarPapaQuebecRomeoSierraTangoUniformVictorWhiskeyXrayYankeeZulu"
        * 4
    )
    camel = camel[: len(camel) // 4 * 4]
    assert len(find_base64_spans(base32)) == 1
    assert len(find_base64_spans(camel)) == 1
    assert find_base64_spans("RepeatedCamelCaseIdentifier" * 20) == []


@settings(max_examples=60, derandomize=True, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=2**32 - 1),
    size=st.integers(min_value=150, max_value=1200),
    prefix=st.text(max_size=60),
    suffix=st.text(max_size=60),
    urlsafe=st.booleans(),
)
def test_payload_insertion_changes_only_payload_and_is_idempotent(
    seed, size, prefix, suffix, urlsafe
):
    # A fixed non-magic prefix keeps this property about span preservation.
    data = b"\x00\x01\x02" + random.Random(seed).randbytes(size - 3)
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    encoded = encode(data).decode()
    text = prefix + "\n" + encoded + "\n" + suffix
    [span] = find_base64_spans(text)
    assert (span.start, span.end) == (len(prefix) + 1, len(prefix) + 1 + len(encoded))
    expected = prefix + f"\n[base64 binary {size} B]\n" + suffix
    assert index_text(text) == expected
    assert index_text(expected) == expected
