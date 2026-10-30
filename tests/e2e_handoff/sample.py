"""Synthetic Claude Code transcripts with a single buried tool-result fact."""

import json
from pathlib import Path

from commonplace.handoff.models import HandoffSettings
from commonplace.handoff.transcripts import parse
from commonplace.handoff.trigger import should_handoff, usage_from_tail

HERE = Path(__file__).resolve().parent
FACT = "the staging DB port is 54329, codename HELIOTROPE-7"
TASK = "#1701"


def transcript(path, *, planted=True):
    """Two hundred Claude Code JSONL records, including real tool blocks."""
    records = []
    for index in range(200):
        if index == 0:
            role, content = "user", "Finish the staging rollout and verify its configuration."
        elif index == 199:
            role, content = (
                "assistant",
                "The rollout checks pass. Continue with deployment validation.",
            )
        elif index % 4 == 1:
            role, content = (
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "id": f"call-{index}",
                        "name": "Bash",
                        "input": {"command": f"git diff --stat -- deploy/check_{index}.py"},
                    }
                ],
            )
        elif index % 4 == 2:
            role, content = (
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"call-{index - 1}",
                        "content": (
                            f"Configuration inspection: {FACT}.\nKeep this exact value for validation."
                            if planted and index == 102
                            else f"Check {index}: deployment configuration matches the checked-in template."
                        ),
                    }
                ],
            )
        elif index % 4 == 3:
            role, content = "assistant", f"Check {index} passed; move to the next deployment file."
        else:
            role, content = (
                "user",
                f"Continue validation step {index}; preserve the deployment plan.",
            )
        records.append(
            {
                "type": role,
                "sessionId": path.stem,
                "uuid": f"message-{index}",
                "parentUuid": f"message-{index - 1}" if index else None,
                "timestamp": f"2026-10-30T10:{index // 60:02}:{index % 60:02}Z",
                "message": {"role": role, "content": content},
            }
        )
    records[-1]["message"].update(
        model="claude-sonnet-4-5",
        usage={
            "input_tokens": 10_000,
            "cache_read_input_tokens": 150_000,
            "cache_creation_input_tokens": 9_000,
            "output_tokens": 1_000,
        },
    )
    raw = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    path.write_text(raw, encoding="utf-8")
    messages = parse(raw, "claude-code")
    assert len(records) == len(messages) == 200
    assert raw.count(FACT) == int(planted)
    assert FACT not in "\n".join(json.dumps(record) for record in records[-30:])
    if planted:
        assert messages[102].kind == "tool_result" and FACT in messages[102].text
        assert all(FACT not in message.text for message in messages if message.seq != 102)
    usage = usage_from_tail(str(path), fmt="claude-code")
    assert usage.used_tokens == 170_000 and usage.window_tokens == 200_000
    assert usage.fraction == 0.85
    assert should_handoff(usage, HandoffSettings(enabled=True))
    return messages
