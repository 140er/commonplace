- **Promises:**

  - Dictation has priority over background transcription: `/api/voice/transcribe` holds `voice.dictating()` while it uses the engine, and background work (a meeting's capture, `commonplace/meetings/capture.py`) calls `voice.wait_for_dictation()` before each window, so it waits for any dictation request in flight. A window already running is not interrupted. Dictation itself is unchanged.

- **Tests:**

  - `tests/test_commonplace_voice.py` checks that a dictation request holds the priority marker while it uses the engine and releases it afterwards.
