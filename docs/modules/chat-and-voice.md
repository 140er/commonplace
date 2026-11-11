## chat-and-voice

- **Paths:** `commonplace/chat/`, `commonplace/voice.py`, `commonplace/phone_push/`
- **Owns:**

  - The control-database `project_model_catalog` table, created beside the model settings, holds each board's list of models owners can pick for projects.

  - The workspace chat front agent, server-local dictation and opt-in phone notifications.

- **Promises:**

  - Chat status counts cards in needs-me while ignoring per-board coordinator notices.
  - Opt-in phone needs-you evaluation includes actionable Approvals, so owner origin recovery still notifies after coordinator revocation.

  - Chat answers come from the configured provider or the poster's own runner, never a silent fallback; audio stays in request memory; push is opt-in.

  - Voice is switched per instance with `COMMONPLACE_VOICE` (default on) and picks each language's model with `COMMONPLACE_VOICE_MODEL_EN` and `COMMONPLACE_VOICE_MODEL_NL`; the defaults equal the previous fixed models. A switched-off instance refuses voice with 503, and the free-plan daily transcription cap applies to every clip.

- **How it fails:**

  - Without a provider or runner the chat shows a receipt and no answer; push delivery failures are recorded, not retried forever.

  - An empty or invalid voice setting stops startup with a message naming the setting, and a missing model at load time is an error; neither falls back to another model.

- **Tests:**

  - The `tests/test_chat_` files, `tests/test_commonplace_voice.py`, the `tests/test_commonplace_phone_push_` files.
  - `tests/test_commonplace_voice.py` covers the instance switch, model settings, invalid-setting errors and the free-plan cap at, over and under its limit.
  - Coordinator-revocation recovery verifies the origin approval item and retains notification, priority and duplicate-suppression assertions.
