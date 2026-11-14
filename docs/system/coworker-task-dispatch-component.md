### Coworker task dispatch component

A coworker chat message that carries explicit task syntax reaches the canonical
boards only through `BoardService.ensure_chat_task` and `start_chat_task`: the
origin binding commits atomically with a stable per-actor key, the existing
person-owner fenced runner admission is requested, and a refusal leaves the
card in place without inventing a worker launch. Model output never executes
tools. The coworker store, runtime and task controls stay local to the workspace.
