## chat-and-voice

- **Paths:** `commonplace/chat/`, `commonplace/voice.py`, `commonplace/phone_push/`
- **Owns:**

  - The control-database `project_model_catalog` table, created beside the model settings, holds each board's list of models owners can pick for projects.

  - Person-message and space-created listeners (the chat front, project seats) share `events.is_stored` to confirm an event is the one the log holds.

  - The workspace chat front agent, server-local dictation and opt-in phone notifications.

  - The control-database `bot_model_calls` table (bot model requests per person and day, numbers only); `store.counts_today` adds it to project chat's count so both share one budget, and `store.take_bot_call` checks and counts one bot request in a single transaction.

  - `front.skipping()` is a thread-local context manager: a project-chat post made inside it on the same thread is ignored by the front agent's event handler (no receipt, model job, runner relay or fallback card). Bots wrap their forwarded posts in it.

  - `commonplace/chat/model.py` holds the shared connection code (`resolve_endpoint`, `Call`) behind both `complete` and the streaming client `commonplace/chat/model_stream.py`, which yields reply text, complete tool calls and one stop reason as the provider sends them.

- **Promises:**

  - Chat status counts cards in needs-me while ignoring per-board coordinator notices.
  - Opt-in phone needs-you evaluation includes actionable Approvals, so owner origin recovery still notifies after coordinator revocation.

  - Chat answers come from the configured provider or the poster's own runner, never a silent fallback; audio stays in request memory; push is opt-in.

  - `model_stream.stream` takes every limit from its caller (timeout for the whole call, output tokens, input characters, response bytes), makes no retry, uses the same origin, key-host and loopback checks as `complete`, and can be cancelled, which closes the socket.

  - Voice is switched per instance with `COMMONPLACE_VOICE` (default on) and picks each language's model with `COMMONPLACE_VOICE_MODEL_EN` and `COMMONPLACE_VOICE_MODEL_NL`; the defaults equal the previous fixed models. A switched-off instance refuses voice with 503, and the free-plan daily transcription cap applies to every clip.

- **How it fails:**

  - Without a provider or runner the chat shows a receipt and no answer; push delivery failures are recorded, not retried forever.

  - A streaming call fails with only the codes `timeout`, `http_error`, `bad_response` and `stopped`, never provider error text; a tool call with a name that was not offered, or arguments that are not a JSON object, is a `bad_response`.

  - An empty or invalid voice setting stops startup with a message naming the setting, and a missing model at load time is an error; neither falls back to another model.

- **Tests:**

  - The `tests/test_chat_` files, `tests/test_commonplace_voice.py`, the `tests/test_commonplace_phone_push_` files.
  - `tests/test_chat_model_stream.py` holds the streaming transport against loopback fake Anthropic and OpenAI-compatible servers: events arrive while the server holds the rest back, fragmented and parallel tool calls, wire shapes, caller limits, deadline, cancel, and the key-host and loopback refusals.
  - `tests/test_commonplace_voice.py` covers the instance switch, model settings, invalid-setting errors and the free-plan cap at, over and under its limit.
  - Coordinator-revocation recovery verifies the origin approval item and retains notification, priority and duplicate-suppression assertions.
