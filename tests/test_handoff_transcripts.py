"""Synthetic transcripts only: no user session data belongs in these tests."""

import json
from itertools import groupby
from pathlib import Path
from time import perf_counter

import pytest
from hypothesis import given, settings, strategies as st

from commonplace.handoff.transcripts import detect_format, parse

FIXTURES = Path(__file__).parent / "fixtures" / "handoff"


def _fixture(name):
    return (FIXTURES / f"transcripts_{name}").read_bytes().decode("utf-8")


def _line(value, ending="\n"):
    return json.dumps(value, ensure_ascii=False) + ending


def _claude(content, role="user", **extra):
    return {"type": role, "message": {"role": role, "content": content}, **extra}


def _codex(item_type, **extra):
    return {"type": "response_item", "payload": {"type": item_type, **extra}}


def _assert_dense(messages):
    assert [message.seq for message in messages] == list(range(len(messages)))


@pytest.mark.parametrize(
    ("name", "fmt"),
    [("claude.jsonl", "claude-code"), ("codex.jsonl", "codex"), ("messages.json", "messages")],
)
def test_fixture_detection_and_explicit_format(name, fmt):
    text = _fixture(name)
    assert detect_format(text) == fmt
    assert parse(text) == parse(text, fmt)
    _assert_dense(parse(text))


def test_claude_blocks_and_metadata_are_verbatim():
    text = _fixture("claude.jsonl")
    messages = parse(text)
    assert len(messages) == 11
    assert messages[0].text == "  Café ☕\n第二行  "
    assert [(m.role, m.kind) for m in messages[:6]] == [
        ("user", "message"),
        ("assistant", "message"),
        ("assistant", "other"),
        ("assistant", "tool_call"),
        ("tool", "tool_result"),
        ("assistant", "other"),
    ]
    assert messages[2].text == "Keep the exact words."
    call = messages[3]
    assert (call.name, call.call_id, call.text) == ("read_file", "call_a", '{"a": 1, "z": "雪"}')
    assert call.at == "2026-01-01T00:00:01Z"
    assert call.raw == messages[1].raw == messages[2].raw
    result = messages[4]
    image = {"type": "image", "source": {"type": "base64", "data": "AAAA"}}
    assert result.text == "line one\nline two\n" + json.dumps(image, sort_keys=True) + "\n❌  "
    assert result.call_id == call.call_id
    assert json.loads(result.raw)["message"]["content"][0]["is_error"] is True
    assert json.loads(messages[5].text)["type"] == "image"
    assert all((m.kind, m.role, m.text) == ("other", "system", "") for m in messages[6:])


def test_claude_fixture_has_crlf_blank_line_and_live_tail():
    text = _fixture("claude.jsonl")
    assert "\r\n\r\n" in text
    assert not text.endswith(("\r", "\n"))
    assert "".join(raw for raw, _ in groupby(m.raw for m in parse(text))) == "".join(
        raw for raw in text.splitlines(keepends=True) if raw.strip()
    )


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_claude_role_defaults_to_line_type(role):
    message = parse(_line({"type": role, "message": {"content": "words"}}))[0]
    assert (message.role, message.kind, message.text) == (role, "message", "words")


def test_codex_items_events_and_reasoning():
    messages = parse(_fixture("codex.jsonl"))
    assert len(messages) == 17
    assert (messages[0].kind, messages[0].text) == ("other", "")
    assert (messages[1].role, messages[1].text) == ("system", "Synthetic instructions")
    assert [m.text for m in messages[2:4]] == ["  Hello 🌍\n", "雪"]
    call, result, custom, custom_result, shell = messages[4:9]
    assert (call.role, call.kind, call.name, call.call_id) == (
        "assistant",
        "tool_call",
        "read_file",
        "call_a",
    )
    assert call.text == '{ "path": "café.txt" }'
    assert (result.role, result.kind, result.call_id, result.text) == (
        "tool",
        "tool_result",
        "call_a",
        "  Original output\n",
    )
    assert (custom.name, custom.call_id, custom.text) == (
        "apply_patch",
        "call_b",
        "*** Begin Patch\n*** End Patch",
    )
    assert custom_result.text == 'Done ✓\n{"data": "AAAA", "type": "image"}'
    assert (shell.name, shell.call_id) == ("local_shell", "shell_a")
    assert json.loads(shell.text) == {"type": "exec", "command": ["echo", "雪"]}
    assert [m.text for m in messages[9:11]] == ["Synthetic reasoning", "More exact words"]
    assert all(m.kind == "other" for m in messages[9:11])
    assert [m.text for m in messages[11:14]] == ["Finished.  ", "User event", "Assistant event"]
    assert all((m.kind, m.role, m.text) == ("other", "system", "") for m in messages[14:])
    assert all(m.at.startswith("2026-01-01T") for m in messages)


def test_messages_preserve_source_object_spelling_and_tool_links():
    text = _fixture("messages.json")
    messages = parse(text)
    assert len(messages) == 6
    assert messages[0].text == "  café ☕\n  "
    assert messages[0].raw == '{ "role" : "user", "content" : "  caf\\u00e9 ☕\\n  " }'
    assert messages[1].raw is messages[2].raw
    assert all(message.raw in text for message in messages)
    assert (messages[2].call_id, messages[3].call_id) == ("call_a", "call_a")
    assert messages[4].text == '{  "term": "🌍" }'
    assert (
        messages[5].kind,
        messages[5].role,
        messages[5].name,
        messages[5].call_id,
        messages[5].text,
    ) == (
        "tool_result",
        "tool",
        "lookup",
        "call_b",
        "  Found\n",
    )


@pytest.mark.parametrize("wrapper", [False, True])
def test_messages_multiline_objects_and_wrapper_metadata(wrapper):
    record = '{\n "role": "user",\n "content": "same\\twords", "timestamp":"time"\n}'
    text = "[\n" + record + "\n]"
    if wrapper:
        text = '{"before": [1, {"messages": []}], "messages": ' + text + ', "after": {}}'
    message = parse(text)[0]
    assert (message.text, message.raw, message.at) == ("same\twords", record, "time")


def test_messages_duplicate_wrapper_key_matches_json_semantics():
    text = '{"messages": [{"role":"user","content":"old"}], "messages": [{"role":"user","content":"new"}]}'
    messages = parse(text)
    assert len(messages) == 1
    assert messages[0].text == "new"
    assert messages[0].raw == '{"role":"user","content":"new"}'


@pytest.mark.parametrize("text", ["[]", '{"messages":[]}', ' \r\n {"meta": 1, "messages": []} \n'])
def test_empty_message_array(text):
    assert detect_format(text) == "messages"
    assert parse(text) == []


@pytest.mark.parametrize("fmt", ["auto", "claude-code", "codex", "messages"])
@pytest.mark.parametrize(
    "text", ["", " \n\r\n", "random text", "{}", '"hello"', "42", "null", "[1]", '{"messages":1}']
)
def test_garbage_raises_value_error(text, fmt):
    with pytest.raises(ValueError):
        parse(text, fmt)


@pytest.mark.parametrize("value", [None, b"[]", 3, {}, []])
def test_non_text_input_raises_value_error(value):
    with pytest.raises(ValueError):
        parse(value)
    with pytest.raises(ValueError):
        detect_format(value)


@pytest.mark.parametrize("fmt", ["unknown", "Claude", "", None])
def test_invalid_format_raises_value_error(fmt):
    with pytest.raises(ValueError):
        parse("[]", fmt)


@pytest.mark.parametrize(
    "record",
    [
        {"content": "words"},
        {"role": 3, "content": "words"},
        {"role": "user"},
        {"role": "user", "content": None},
        {"role": "user", "content": 1},
    ],
)
def test_invalid_message_shape_raises_value_error(record):
    text = json.dumps([record])
    with pytest.raises(ValueError):
        detect_format(text)
    with pytest.raises(ValueError):
        parse(text, "messages")


@pytest.mark.parametrize(
    "text", ["[] []", "[]\ngarbage", '{"messages":[]}\n{}', '[{"role":"user","content":"x"},]']
)
def test_invalid_or_trailing_messages_json(text):
    with pytest.raises(ValueError):
        parse(text)
    with pytest.raises(ValueError):
        parse(text, "messages")


@pytest.mark.parametrize("fmt", ["claude-code", "codex"])
@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r", ""])
def test_jsonl_raw_line_endings_blanks_and_whitespace(fmt, ending):
    record = (
        _claude("\u2028 \u2029 \x85 🌍")
        if fmt == "claude-code"
        else _codex("message", role="user", content="\u2028 \u2029 \x85 🌍")
    )
    raw = " \t" + json.dumps(record, ensure_ascii=False) + " \t" + ending
    messages = parse("\r\n \t\r\n" + raw)
    assert len(messages) == 1
    assert messages[0].raw == raw
    assert messages[0].text == "\u2028 \u2029 \x85 🌍"


@pytest.mark.parametrize("fmt", ["claude-code", "codex"])
@pytest.mark.parametrize(
    "tail", ["{", '{"type":', '{"type":"assist', '{"x":tru', '{"x":"\\u12', '{"x":[1,']
)
def test_truncated_live_tail_is_retained(fmt, tail):
    record = (
        _claude("complete")
        if fmt == "claude-code"
        else _codex("message", role="user", content="complete")
    )
    messages = parse(_line(record) + tail)
    assert len(messages) == 2
    assert (messages[-1].role, messages[-1].kind, messages[-1].text, messages[-1].raw) == (
        "system",
        "other",
        "",
        tail,
    )
    _assert_dense(messages)


@pytest.mark.parametrize("number", ["-", "1.", "1e", "1e-", "1E+", "-0.5E-", "1.2e+"])
def test_tail_cut_inside_number_is_retained(number):
    tail = '{"input":{"value":' + number
    messages = parse(_line(_claude("complete")) + tail)
    assert messages[-1].kind == "other"
    assert messages[-1].raw == tail


def test_every_partial_prefix_of_a_live_line_is_retained():
    record = _claude(
        [
            {
                "type": "tool_use",
                "input": {"value": -1.25e-7, "flag": False, "nothing": None, "text": "雪 🌍"},
            }
        ],
        role="assistant",
    )
    raw = json.dumps(record, ensure_ascii=True)
    complete = _line(_claude("complete"))
    for end in range(1, len(raw)):
        messages = parse(complete + raw[:end])
        assert messages[-1].kind == "other", raw[:end]
        assert messages[-1].raw == raw[:end]


@pytest.mark.parametrize("bad", ["not JSON", "{broken}", '{"x": }', '{"x":"bad\\q"}', '{"type":\n'])
def test_invalid_tail_is_garbage(bad):
    with pytest.raises(ValueError):
        parse(_line(_claude("valid")) + bad)


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("whitespace", [" ", "\t"])
@pytest.mark.parametrize("token", ["tru", "fals", "nul", "-", "1.", "1e-", '"\\u12'])
def test_whitespace_after_partial_token_is_malformed(codex, whitespace, token):
    first = _codex("message", role="user", content="valid") if codex else _claude("valid")
    tail = '{"x":' + token + whitespace
    with pytest.raises(ValueError, match="line 2"):
        parse(_line(first) + tail)


@pytest.mark.parametrize("tail", ['{"x": ', '{"x":true ', '{"x":1 ', '{"x":"text '])
def test_whitespace_in_recoverable_tail_is_retained(tail):
    messages = parse(_line(_claude("valid")) + tail)
    assert (messages[-1].kind, messages[-1].raw) == ("other", tail)


def test_invalid_interior_line_is_garbage():
    with pytest.raises(ValueError, match="line 2"):
        parse(_line(_claude("valid")) + '{"type":\n' + _line(_claude("later")))


@pytest.mark.parametrize(
    "record", [{"type": "future", "value": 1}, {"type": "future", "payload": {"value": 1}}]
)
def test_unknown_only_record_is_preserved(record):
    raw = _line(record)
    message = parse(raw)[0]
    assert (message.kind, message.role, message.text, message.raw) == ("other", "system", "", raw)


def test_repeated_identical_lines_are_not_deduplicated():
    raw = _line(_claude([{"type": "text", "text": "same"}, {"type": "text", "text": "again"}]))
    messages = parse(raw * 2)
    assert len(messages) == 4
    assert messages[0].raw is messages[1].raw
    assert messages[2].raw is messages[3].raw
    assert messages[0].raw is not messages[2].raw
    distinct_raw = [
        next(group).raw for _, group in groupby(messages, key=lambda message: id(message.raw))
    ]
    assert "".join(distinct_raw) == raw * 2
    _assert_dense(messages)


@pytest.mark.parametrize("content", ["", [], None, {"future": True}])
def test_empty_or_unknown_claude_content_keeps_the_record(content):
    raw = _line(_claude(content))
    messages = parse(raw)
    assert len(messages) == 1
    assert messages[0].raw == raw


def test_unknown_blocks_and_non_object_records_keep_raw():
    raw = _line(_claude([{"type": "future", "data": "opaque"}, 3]))
    extra = "null\n[1]\n"
    messages = parse(raw + extra)
    assert [m.kind for m in messages] == ["other"] * 4
    assert [m.text for m in messages[:2]] == ['{"data": "opaque", "type": "future"}', "3"]
    assert messages[2].raw == "null\n"
    assert messages[3].raw == "[1]\n"


@pytest.mark.parametrize(
    "record",
    [
        {"type": ["unknown"]},
        {"type": "assistant", "message": {"role": [], "content": "words"}},
        _claude([{"type": {"unknown": True}, "text": "words"}]),
        _claude([{"type": "tool_result", "content": [{"type": [], "text": "words"}]}]),
        {"type": "response_item", "payload": {"type": ["unknown"]}},
    ],
)
def test_malformed_type_fields_in_a_known_stream_keep_raw(record):
    first = (
        _codex("message", role="user", content="valid") if "payload" in record else _claude("valid")
    )
    messages = parse(_line(first) + _line(record))
    assert messages[-1].raw == _line(record)
    _assert_dense(messages)


def test_tool_result_string_and_empty_blocks():
    blocks = [
        {"type": "tool_result", "tool_use_id": "a", "content": "  exact\n"},
        {"type": "tool_result", "tool_use_id": "b", "content": []},
    ]
    messages = parse(_line(_claude(blocks)))
    assert [m.text for m in messages] == ["  exact\n", ""]
    assert [m.call_id for m in messages] == ["a", "b"]


def test_openai_legacy_function_calls_and_results():
    records = [
        {
            "role": "assistant",
            "content": None,
            "function_call": {"name": "lookup", "arguments": {"z": "雪", "a": 1}},
        },
        {"role": "function", "name": "lookup", "content": "  result\n"},
        {
            "role": "assistant",
            "content": "Answer",
            "tool_calls": [{"id": "next", "function": {"name": "next", "arguments": "{}"}}],
        },
    ]
    messages = parse(json.dumps(records))
    assert messages[0].text == '{"a": 1, "z": "雪"}'
    assert (messages[1].kind, messages[1].role, messages[1].name) == (
        "tool_result",
        "tool",
        "lookup",
    )
    assert [m.kind for m in messages[2:]] == ["message", "tool_call"]


@pytest.mark.parametrize(
    "content",
    [
        [],
        [
            {"type": "text", "text": "same words"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    ],
)
def test_openai_tool_result_blocks_keep_the_call_link(content):
    records = [{"role": "tool", "tool_call_id": "call_a", "name": "read_file", "content": content}]
    messages = parse(json.dumps(records))
    assert len(messages) == max(1, len(content))
    assert all(
        (message.kind, message.role, message.name, message.call_id)
        == ("tool_result", "tool", "read_file", "call_a")
        for message in messages
    )
    if content:
        assert messages[0].text == "same words"
        assert json.loads(messages[1].text) == content[1]
    else:
        assert messages[0].text == ""


def test_codex_non_text_outputs_and_empty_reasoning():
    items = [
        _codex("function_call", name="lookup", arguments={"x": "雪"}, call_id="a"),
        _codex("function_call_output", call_id="a", output={"nested": [1]}),
        _codex("local_shell_call_output", call_id="b", output="original"),
        _codex("reasoning", encrypted_content="opaque"),
        _codex("future_item", text="unknown"),
        {"type": "event_msg", "payload": {"type": "future_event"}},
        {"type": "future", "payload": None},
    ]
    messages = parse("".join(_line(item) for item in items))
    assert [m.text for m in messages[:3]] == ['{"x": "雪"}', '{"nested": [1]}', "original"]
    assert (messages[3].role, messages[3].kind, messages[3].text) == ("assistant", "other", "")
    assert len(messages) == len(items)


_WORDS = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=80)


@settings(max_examples=60, deadline=None, derandomize=True)
@given(st.lists(_WORDS, min_size=1, max_size=20), st.sampled_from(["\n", "\r\n"]), st.booleans())
def test_jsonl_lossless_property(words, ending, codex):
    lines = []
    for index, word in enumerate(words):
        blocks = [
            {"type": "text", "text": word},
            {"type": "tool_use", "id": str(index), "name": "echo", "input": {"text": word}},
        ]
        record = (
            _codex("message", role="assistant", content=blocks)
            if codex
            else _claude(blocks, role="assistant")
        )
        record["timestamp"] = str(index)
        lines.append(_line(record, ending))
    tail = '{"type":"unfinished'
    text = ending + ending.join(lines) + tail
    messages = parse(text)
    distinct_raw = [raw for raw, _ in groupby(m.raw for m in messages)]
    assert "".join(distinct_raw) == "".join(lines) + tail
    assert [m.text for m in messages if m.kind == "message"] == words
    _assert_dense(messages)


@settings(max_examples=40, deadline=None, derandomize=True)
@given(st.lists(_WORDS, min_size=0, max_size=15), st.booleans())
def test_message_object_lossless_property(words, wrapper):
    objects = ['{ "role": "user", "content": ' + json.dumps(word) + " }" for word in words]
    text = "[\n" + ",\n".join(objects) + "\n]"
    if wrapper:
        text = '{"messages":' + text + ',"metadata":"kept in request"}'
    messages = parse(text)
    assert [message.raw for message in messages] == objects
    assert [message.text for message in messages] == words
    _assert_dense(messages)


@pytest.mark.parametrize("fmt", ["claude-code", "codex", "messages"])
def test_twenty_megabyte_transcript_under_three_seconds(tmp_path, fmt):
    content = "Synthetic line ☕ " * 220
    if fmt == "claude-code":
        record = _claude(content)
    elif fmt == "codex":
        record = _codex("message", role="user", content=[{"type": "input_text", "text": content}])
    else:
        record = {"role": "user", "content": content}
    raw = _line(record)
    row_count = (20 * 1024 * 1024) // len(raw.encode("utf-8")) + 1
    path = tmp_path / "synthetic.jsonl"
    text = "[" + ",".join([raw] * row_count) + "]" if fmt == "messages" else raw * row_count
    path.write_bytes(text.encode("utf-8"))
    assert path.stat().st_size >= 20 * 1024 * 1024
    text = path.read_bytes().decode("utf-8")
    start = perf_counter()
    messages = parse(text)
    elapsed = perf_counter() - start
    assert elapsed < 3, f"{fmt}: {elapsed:.3f}s for 20 MB"
    assert len(messages) == row_count
    assert messages[0].text == messages[-1].text == content
    _assert_dense(messages)
