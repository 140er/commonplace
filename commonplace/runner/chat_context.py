"""Bounded, redacted context for the poster's own runner."""

import json
import re

from commonplace import redact
from commonplace.boards.service import BoardService
from commonplace.chat import worker
from commonplace.chat.types import MAX_INPUT_CHARS, Job
from commonplace.extensions import ToolContext

MAX_CONTEXT_JSON_BYTES = 49_152


def _clean(value, *, quoted=True):
    text = str(value) if value is not None else ""
    if len(text) > redact.MAX_CHARS:
        return "[omitted:too_long]"
    text = redact.redact(text)[0]
    return re.sub(r"[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+", " ", text) if quoted else text


def build(gateway, poster, board: str, message_id: int, context: str) -> tuple[str, str]:
    system = _clean(worker.FRONT_SYSTEM, quoted=False)
    if context == "full":
        job = Job(
            poster.workspace_id, board, message_id, poster.user_id, "running", "", False, "", ""
        )
        system, turns = worker.build_context(gateway, poster, job)
        prompt = turns[0]["content"]
    else:
        service = BoardService(ToolContext(principal=poster, gateway=gateway))
        project = next(b for b in service.board_list() if b["slug"] == board)
        parts = ["Project: " + _clean(project["name"])]
        cards = service.task_list(board)
        with service.store.connection() as conn:
            count = 0
            for card in cards:
                origin = conn.execute(
                    "SELECT restricted_origin FROM tasks WHERE id=?", (card["number"],)
                ).fetchone()
                if origin is None or origin[0] != 0:
                    continue
                parts.append(
                    "Card (quoted data): " + _clean(card["title"]) + " | " + _clean(card["status"])
                )
                count += 1
                if count == 15:
                    break
        messages = service.space_read(board, after=message_id - 1)["messages"]
        message = next(m for m in messages if m["id"] == message_id)
        newest = "Person's question or request: " + _clean(message["text"], quoted=False)
        parts.append(newest[:MAX_INPUT_CHARS])
        prompt = "\n".join(parts)

    def size(start):
        return len(
            json.dumps({"system": system, "prompt": prompt[start:]}, ensure_ascii=False).encode()
        )

    if size(0) > MAX_CONTEXT_JSON_BYTES:
        # Find the longest suffix that fits, preserving the newest message last.
        start, end = 0, len(prompt)
        while start < end:
            middle = (start + end) // 2
            if size(middle) > MAX_CONTEXT_JSON_BYTES:
                start = middle + 1
            else:
                end = middle
        prompt = prompt[start:]
    return system, prompt
