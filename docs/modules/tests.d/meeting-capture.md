- **Tests:**

  - `tests/test_meetings_capture.py` covers meeting capture and local transcription over a stub engine and a generated tone as the PCM fixture, `tests/fixtures/meetings/tone-220hz-0.5s.pcm` (see the `meetings` module block): windows and stitching, per-speaker and mixed streams, gaps for a delayed or failing engine, engine slots and dictation priority, no audio on disk or in logs, retention, `needs_local_transcription` and the clean workspace.
