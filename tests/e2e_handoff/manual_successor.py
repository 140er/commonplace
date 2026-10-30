"""A Codex stand-in learns the buried answer only from the old thread tools."""

import json
import os
import re
import sys
from pathlib import Path

from wire import ThreadClient

prompt = Path(sys.argv[1]).read_text()
assert "COMMONPLACE_HANDOFF_ID" not in os.environ  # T3 cannot pass this environment on.
assert os.environ["HANDOFF_FAKE_PROVIDER"] == "codex"
thread_id = re.search(r"^Thread: (thr_[0-9a-f]+)$", prompt, re.MULTILINE)[1]
handoff_id = re.search(r"commonplace-handoff-id: (hof_[0-9a-f]+)", prompt)[1]
client = ThreadClient(os.environ["COMMONPLACE_URL"], os.environ["COMMONPLACE_TOKEN"])
client.initialize()
hits = client.tool("thread_recall", thread_id=thread_id, query="staging database port")["hits"]
answer = None
for hit in hits:
    messages = client.tool(
        "thread_get",
        thread_id=hit["thread_id"],
        start=hit["seq_start"],
        end=hit["seq_end"],
    )["messages"]
    for message in messages:
        found = re.search(r"staging DB port is (\d+), codename ([A-Z][A-Z0-9-]+)", message["text"])
        if found:
            assert message["kind"] == "tool_result"
            assert found[1] not in prompt and found[2] not in prompt
            answer = {"port": found[1], "codename": found[2]}
            break
    if answer:
        break
assert answer, "The fact was not recovered from the old thread"
result = {
    **answer,
    "handoff_id": handoff_id,
    "thread_id": thread_id,
    "provider": os.environ["HANDOFF_FAKE_PROVIDER"],
    "calls": client.calls,
}
Path(os.environ["HANDOFF_MANUAL_RESULT"]).write_text(json.dumps(result))
