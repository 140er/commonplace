"""Synthetic Claude Code and Codex usage records; no real conversation text."""

import builtins
import io
import json
from time import perf_counter

import pytest

from commonplace.handoff import trigger
from commonplace.handoff.models import (
    DEFAULT_CONTEXT_WINDOW,
    ContextUsage,
    HandoffSettings,
)


def _claude(used=100, model="claude-sonnet-4-6", **tokens):
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "usage": {"input_tokens": used, **tokens},
        },
    }


def _turn(model="gpt-6.1-codex"):
    return {"type": "turn_context", "payload": {"model": model}}


def _codex(used=100, window=475_000, **tokens):
    info = {"last_token_usage": {"total_tokens": used, **tokens}}
    if window is not None:
        info["model_context_window"] = window
    return {"type": "event_msg", "payload": {"type": "token_count", "info": info}}


def _line(record):
    return json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"


def _transcript(tmp_path, *records):
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b"".join(_line(record) for record in records))
    return path


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-opus-4-6", 200_000),
        ("claude-sonnet-4-6", 200_000),
        ("claude-haiku-4-5-20251001", 200_000),
        ("claude-fable-5", 200_000),
        ("claude-opus-future", 200_000),
        ("claude-sonnet-4-6[1m]", 1_000_000),
        ("claude-opus-4-6-1m", 1_000_000),
        ("other[1m]-model", 1_000_000),
        ("other-1m", 1_000_000),
        ("gpt-5-codex", 400_000),
        ("gpt-5.3-codex-spark", 400_000),
        ("gpt-6.1-codex", 400_000),
        ("gpt-6", 400_000),
        (" GPT-6.1-CODEX ", 400_000),
        ("gpt-6-codex-1m", 1_000_000),
        ("unlisted", DEFAULT_CONTEXT_WINDOW),
        ("", DEFAULT_CONTEXT_WINDOW),
    ],
)
def test_window_for_model(model, expected):
    assert trigger.window_for_model(model) == expected


@pytest.mark.parametrize("model", ["unknown", "claude-opus-4[1m]", "gpt-6-codex-1m"])
def test_explicit_window_wins(model):
    assert trigger.window_for_model(model, 123_456) == 123_456


@pytest.mark.parametrize("fmt", ["auto", "claude-code"])
def test_claude_usage_includes_all_context_tokens(tmp_path, fmt):
    record = _claude(
        101,
        cache_read_input_tokens=102,
        cache_creation_input_tokens=103,
        output_tokens=104,
        cache_creation={"ephemeral_5m_input_tokens": 103},
    )
    path = _transcript(tmp_path, record)
    assert trigger.usage_from_tail(path, fmt=fmt) == ContextUsage(410, 200_000, "claude-sonnet-4-6")


@pytest.mark.parametrize("model", ["unknown", "claude-sonnet-4-6[1m]", "claude-opus-4-6-1m"])
def test_claude_model_and_override(tmp_path, model):
    path = _transcript(tmp_path, _claude(20, model=model))
    assert trigger.usage_from_tail(path) == ContextUsage(20, trigger.window_for_model(model), model)
    assert trigger.usage_from_tail(path, override_window=300_000) == ContextUsage(
        20, 300_000, model
    )


def test_claude_uses_last_call_not_sum(tmp_path):
    path = _transcript(
        tmp_path,
        _claude(10, model="claude-opus-4-6"),
        _claude(20),
        {"type": "user", "message": {"usage": {"input_tokens": 999}}},
        {"type": "assistant", "message": {"content": "synthetic text"}},
    )
    assert trigger.usage_from_tail(path) == ContextUsage(20, 200_000, "claude-sonnet-4-6")


@pytest.mark.parametrize("fmt", ["auto", "codex"])
def test_codex_uses_last_usage_and_reported_window(tmp_path, fmt):
    path = _transcript(
        tmp_path,
        _turn("gpt-5-codex"),
        _codex(999),
        _turn(),
        _codex(120, input_tokens=100, output_tokens=20, cached_input_tokens=80),
        {"type": "event_msg", "payload": {"type": "token_count", "info": None}},
    )
    assert trigger.usage_from_tail(path, fmt=fmt) == ContextUsage(120, 475_000, "gpt-6.1-codex")


@pytest.mark.parametrize("window", [400_000, 475_000, 1_000_000])
def test_codex_override_wins_over_event_and_model(tmp_path, window):
    path = _transcript(tmp_path, _turn("gpt-6-codex-1m"), _codex(10, window=window))
    assert trigger.usage_from_tail(path, override_window=300_000) == ContextUsage(
        10, 300_000, "gpt-6-codex-1m"
    )


@pytest.mark.parametrize("window", [None, 0, -1, False, "475000", 475_000.0])
def test_codex_missing_or_invalid_window_uses_model_fallback(tmp_path, window):
    path = _transcript(tmp_path, _turn(), _codex(10, window=window))
    assert trigger.usage_from_tail(path) == ContextUsage(10, 400_000, "gpt-6.1-codex")


@pytest.mark.parametrize("total", [None, False, -1, "30"])
def test_codex_falls_back_to_input_plus_output(tmp_path, total):
    record = _codex(total, input_tokens=22, output_tokens=8, reasoning_output_tokens=6)
    path = _transcript(tmp_path, _turn(), record)
    assert trigger.usage_from_tail(path) == ContextUsage(30, 475_000, "gpt-6.1-codex")


def test_codex_missing_total_uses_input_plus_output(tmp_path):
    record = _codex(input_tokens=22, output_tokens=8)
    del record["payload"]["info"]["last_token_usage"]["total_tokens"]
    path = _transcript(tmp_path, _turn(), record)
    assert trigger.usage_from_tail(path).used_tokens == 30


@pytest.mark.parametrize("window", [None, 475_000])
def test_codex_without_turn_context(tmp_path, window):
    path = _transcript(tmp_path, _codex(12, window=window))
    assert trigger.usage_from_tail(path) == ContextUsage(12, window or DEFAULT_CONTEXT_WINDOW, "")


def test_codex_total_is_not_cumulative_and_zero_is_valid(tmp_path):
    record = _codex(0, input_tokens=100, output_tokens=2)
    record["payload"]["info"]["total_token_usage"] = {"total_tokens": 999_999}
    path = _transcript(tmp_path, _turn(), record)
    assert trigger.usage_from_tail(path) == ContextUsage(0, 475_000, "gpt-6.1-codex")


@pytest.mark.parametrize(
    ("earlier_model", "latest_model", "expected_window"),
    [
        ("gpt-6-codex", "gpt-6-codex-1m", 1_000_000),
        ("gpt-6-codex-1m", "gpt-6-codex", 400_000),
    ],
)
def test_codex_model_from_last_turn_context_even_after_usage(
    tmp_path, earlier_model, latest_model, expected_window
):
    path = _transcript(tmp_path, _turn(earlier_model), _codex(10, window=None), _turn(latest_model))
    assert trigger.usage_from_tail(path) == ContextUsage(10, expected_window, latest_model)


@pytest.mark.parametrize("payload", [{}, {"model": None}, {"model": []}])
def test_codex_missing_model_does_not_reuse_an_older_turn_model(tmp_path, payload):
    path = _transcript(
        tmp_path,
        _turn("gpt-6-codex-1m"),
        {"type": "turn_context", "payload": payload},
        _codex(10, window=None),
    )
    assert trigger.usage_from_tail(path) == ContextUsage(10, DEFAULT_CONTEXT_WINDOW, "")


@pytest.mark.parametrize("records", [(_claude(22), _codex(33)), (_codex(22), _claude(33))])
def test_auto_chooses_latest_usage_across_formats(tmp_path, records):
    path = _transcript(tmp_path, *records)
    assert trigger.usage_from_tail(path).used_tokens == 33


def test_explicit_formats_ignore_other_records(tmp_path):
    path = _transcript(tmp_path, _turn(), _codex(22), _claude(33))
    assert trigger.usage_from_tail(path, fmt="codex").used_tokens == 22
    assert trigger.usage_from_tail(path, fmt="claude-code").used_tokens == 33


@pytest.mark.parametrize(
    "record",
    [
        None,
        [],
        17,
        "text",
        {"type": "assistant"},
        {"type": "assistant", "message": []},
        {"type": "assistant", "message": {"usage": {}}},
        {"type": "assistant", "message": {"usage": None}},
        _claude(None),
        _claude(-1),
        _claude(True),
        _claude("10"),
        _claude(10.0),
        _claude(10, cache_read_input_tokens=-1),
        {"type": "turn_context", "payload": {"model": []}},
        {"type": "event_msg", "payload": []},
        {"type": "event_msg", "payload": {"type": "other"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": []}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {}}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": []}}},
        _codex(None),
        _codex(-1),
        _codex(True),
    ],
)
def test_invalid_records_are_skipped(tmp_path, record):
    path = _transcript(tmp_path, _claude(42), record)
    assert trigger.usage_from_tail(path).used_tokens == 42
    path = _transcript(tmp_path, record)
    assert trigger.usage_from_tail(path) is None


@pytest.mark.parametrize("model", [None, [], 17])
def test_invalid_claude_model_uses_default(tmp_path, model):
    path = _transcript(tmp_path, _claude(10, model=model))
    assert trigger.usage_from_tail(path) == ContextUsage(10, DEFAULT_CONTEXT_WINDOW, "")


def test_malformed_encoding_nesting_and_partial_last_line_are_skipped(tmp_path):
    path = _transcript(tmp_path, _claude(42))
    with path.open("ab") as stream:
        stream.write(b"\xff\nnot json\n" + b"[" * 2000 + b"]" * 2000 + b'\n{"type":')
    assert trigger.usage_from_tail(path).used_tokens == 42


def test_non_ascii_and_no_final_newline(tmp_path):
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(
        b'{"type":"user","message":{"content":"\xc3\xa9"}}\r\n' + _line(_claude(5)).rstrip(b"\n")
    )
    assert trigger.usage_from_tail(path).used_tokens == 5


def test_partial_first_line_is_not_mistaken_for_complete_record(tmp_path):
    # A valid-looking record embedded in a cut-off source line is not a record.
    fake = b"not json " + _line(_claude(999))
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(_line(_claude(42)) + fake)
    assert trigger.usage_from_tail(path, tail_bytes=len(fake) - 9).used_tokens == 42


def test_complete_first_line_at_tail_boundary_is_kept(monkeypatch):
    last = _line(_claude(42))
    data = _line({"type": "user"}) + last
    reads = []

    class TrackedStream(io.BytesIO):
        def read(self, size):
            reads.append(size)
            return super().read(size)

    stream = TrackedStream(data)
    monkeypatch.setattr(trigger, "open", lambda *_: stream, raising=False)
    assert trigger.usage_from_tail("synthetic", tail_bytes=len(last)).used_tokens == 42
    assert reads == [len(last) + 1]
    assert stream.closed


def test_retry_expands_tail_once(tmp_path):
    usage = _line(_claude(42))
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(b"prefix\n" + usage + b"x" * 250 + b"\n")
    assert trigger.usage_from_tail(path, tail_bytes=100).used_tokens == 42


@pytest.mark.parametrize("tail_bytes", [0, -1, True, 1.5, None])
def test_invalid_tail_budget_returns_none(tmp_path, tail_bytes):
    path = _transcript(tmp_path, _claude())
    assert trigger.usage_from_tail(path, tail_bytes=tail_bytes) is None


@pytest.mark.parametrize("fmt", ["messages", "CLAUDE", "", None])
def test_unknown_format_returns_none(tmp_path, fmt):
    path = _transcript(tmp_path, _claude())
    assert trigger.usage_from_tail(path, fmt=fmt) is None


def test_missing_directory_empty_and_invalid_paths_return_none(tmp_path):
    assert trigger.usage_from_tail(tmp_path / "missing") is None
    assert trigger.usage_from_tail(tmp_path) is None
    assert trigger.usage_from_tail(_transcript(tmp_path)) is None
    assert trigger.usage_from_tail("invalid\x00path") is None
    assert trigger.usage_from_tail(None) is None


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_unreadable_file_returns_none(monkeypatch, error):
    def fail(*_):
        raise error("synthetic failure")

    monkeypatch.setattr(trigger, "open", fail, raising=False)
    assert trigger.usage_from_tail("synthetic") is None


@pytest.mark.parametrize("operation", ["seek", "read"])
def test_io_failure_after_open_returns_none(monkeypatch, operation):
    class FailingStream(io.BytesIO):
        def seek(self, *args):
            if operation == "seek":
                raise OSError("synthetic failure")
            return super().seek(*args)

        def read(self, *args):
            raise OSError("synthetic failure")

    stream = FailingStream(b"synthetic")
    monkeypatch.setattr(trigger, "open", lambda *_: stream, raising=False)
    assert trigger.usage_from_tail("synthetic") is None
    assert stream.closed


@pytest.mark.parametrize(
    ("usage", "settings", "expected"),
    [
        (ContextUsage(200_000, 200_000), HandoffSettings(), False),
        (None, HandoffSettings(enabled=True), False),
        (ContextUsage(159_999, 200_000), HandoffSettings(enabled=True), False),
        (ContextUsage(160_000, 200_000), HandoffSettings(enabled=True), True),
        (ContextUsage(250_000, 200_000), HandoffSettings(enabled=True), True),
        (ContextUsage(0, 200_000), HandoffSettings(enabled=True), False),
        (ContextUsage(100, 0), HandoffSettings(enabled=True), False),
        (ContextUsage(10, 100), HandoffSettings(enabled=True, threshold=0.1), True),
        (ContextUsage(89, 100), HandoffSettings(enabled=True, threshold=0.9), False),
        (ContextUsage(90, 100), HandoffSettings(enabled=True, threshold=0.9), True),
    ],
)
def test_should_handoff(usage, settings, expected):
    assert trigger.should_handoff(usage, settings) is expected


@pytest.mark.parametrize("usage_at_end", [True, False])
def test_fifty_megabyte_transcript_stays_fast_and_bounded(
    tmp_path, monkeypatch, record_property, usage_at_end
):
    path = tmp_path / "large.jsonl"
    prefix = b'{"type":"user","message":{"role":"user","content":"'
    suffix = b'"}}\n'
    line = prefix + b"x" * (1024 - len(prefix) - len(suffix)) + suffix
    block = line * 1024
    with path.open("wb") as stream:
        if not usage_at_end:
            stream.write(_line(_claude(123)))
        for _ in range(50):
            stream.write(block)
        if usage_at_end:
            stream.write(_line(_claude(123)))
    assert path.stat().st_size >= 50 * 1024 * 1024

    reads = []

    class TrackedStream:
        def __enter__(self):
            self.stream = builtins.open(path, "rb")
            return self

        def __exit__(self, *args):
            self.stream.close()

        def seek(self, *args):
            return self.stream.seek(*args)

        def read(self, size):
            assert size >= 0
            reads.append(size)
            return self.stream.read(size)

    monkeypatch.setattr(trigger, "open", lambda *_: TrackedStream(), raising=False)
    started = perf_counter()
    usage = trigger.usage_from_tail(path)
    elapsed = perf_counter() - started
    record_property("elapsed_ms", round(elapsed * 1000, 3))
    assert elapsed < 0.2
    if usage_at_end:
        assert usage == ContextUsage(123, 200_000, "claude-sonnet-4-6")
        assert reads == [262_145]
    else:
        assert usage is None
        assert reads == [262_145, 1_048_577]


# ── 1M-context Claude models ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5-5[1m]", "claude-sonnet-5-5-1m", "opus[1m]", "claude-opus-5-5-1m-20261001"],
)
def test_one_million_models_by_name(model):
    assert trigger.window_for_model(model) == 1_000_000


def test_configured_model_marks_one_million_and_override_wins():
    assert trigger.window_for_model("claude-opus-5-5", configured="opus[1m]") == 1_000_000
    assert trigger.window_for_model("claude-opus-5-5", configured="opus") == 200_000
    assert trigger.window_for_model("claude-opus-5-5[1m]", 300_000) == 300_000
    assert trigger.window_for_model("claude-opus-5-5", 300_000, configured="opus[1m]") == 300_000


def test_extra_one_million_models_from_env(monkeypatch):
    monkeypatch.setenv("COMMONPLACE_1M_MODELS", "claude-fable-5-1, other")
    assert trigger.window_for_model("claude-fable-5-1") == 1_000_000
    assert trigger.window_for_model("claude-haiku-5-5") == 200_000


def test_configured_model_reads_env_then_project_then_user(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".claude").mkdir(parents=True)
    (project / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert trigger.configured_model(str(project)) == ""
    (home / ".claude" / "settings.json").write_text('{"model": "sonnet"}')
    assert trigger.configured_model(str(project)) == "sonnet"
    (project / ".claude" / "settings.json").write_text('{"model": "opus[1m]"}')
    assert trigger.configured_model(str(project)) == "opus[1m]"
    (project / ".claude" / "settings.local.json").write_text("not json")
    assert trigger.configured_model(str(project)) == "opus[1m]"
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    assert trigger.configured_model(str(project)) == "claude-haiku-5-5"


def _claude_line(used, model="claude-opus-5-5"):
    return json.dumps(
        {
            "type": "assistant",
            "message": {"model": model, "usage": {"input_tokens": used, "output_tokens": 0}},
        }
    )


def test_usage_above_the_table_window_means_one_million(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(_claude_line(250_000) + "\n")
    usage = trigger.usage_from_tail(str(path))
    assert usage.window_tokens == 1_000_000 and usage.used_tokens == 250_000
    assert trigger.usage_from_tail(str(path), override_window=200_000).window_tokens == 200_000


def test_configured_one_million_changes_the_trigger(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(_claude_line(170_000) + "\n")
    on = HandoffSettings(enabled=True)
    assert trigger.should_handoff(trigger.usage_from_tail(str(path)), on)
    usage = trigger.usage_from_tail(str(path), configured="opus[1m]")
    assert usage.window_tokens == 1_000_000
    assert not trigger.should_handoff(usage, on)
