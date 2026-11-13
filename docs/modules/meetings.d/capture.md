- **Owns:**

  - Capture and local transcription for meeting-join bots (`docs/design/meeting-join-bots.md`, section 7). `capture.py` is the real `Sink` a meeting runs on: a `Segmenter` per stream cuts 16 kHz mono PCM into 20 s windows with a 2 s overlap (the cut moves to the quietest block of the last four seconds when one is quiet enough), `stitch` removes the words the overlap repeated, and one daemon thread per meeting (started by the first full window) transcribes the windows on `gateway.voice_engine`, the engine dictation already uses, and writes `meeting_segments` through `MeetingStore.add_segment`. `Worker` builds a `CaptureSink` per meeting by default; tests pass their own sink. `voice.py` gained `dictating()` (held by `/api/voice/transcribe` while it uses the engine) and `wait_for_dictation()`.

- **Promises:**

  - Audio exists only in memory: windows are dropped as soon as they are transcribed, on discard, on finish and when the process dies. `capture.py` imports nothing that can reach a file, a socket or a subprocess and logs only error types, never audio or text; a test scans the data directory, the temp directory and the logs after a run for the audio and the spoken words.

  - Streams follow the provider. A frame's speaker id starts a stream of its own and its segments carry that id; a mixed stream has none, and the engine strips an id a provider without `per_speaker_audio` puts on its frames, so a segment names a speaker only when the stream does. Nothing is derived from a voice.

  - A delay is a gap, never silence. A window further than 60 s behind real time (the engine is busy with dictation or other meetings) or that the engine failed on is dropped and recorded as a gap segment, text `(gap)`, so notes can say words are missing; back to back gaps are one segment, and the first one adds `transcript_delayed` to the meeting's journal, shown as `notices` on `GET /api/meetings/<id>` for the inviter. Real silence writes nothing. A failure anywhere in a window (engine, store, settings) is a failed window, and three in a row end the meeting `failed:transcription` and discard it. A bounded queue and a stream cap keep memory fixed.

  - Meetings sit behind dictation: at most `max_concurrent` engine slots per workspace, and each window waits for any dictation request in flight (a window already running is not interrupted). Concurrent calls are safe: `VoiceEngine.transcribe` holds the engine lock for the whole call, so a meeting window and a dictation request take turns on it. The engine is local; there is no fallback to a service. With voice off or its models missing, `POST /api/meetings` is refused 409 `needs_local_transcription`, and a meeting invited before the engine went away ends `failed:needs_local_transcription` before the bot joins.

  - Retention is the notes slice's rule, now with real segments: they are written only while the meeting is `live` or `ending` (a late write after a stop, a kill or an end is refused), deleted right after the notes unless the owner's `transcript_ok` and the inviter's per-meeting tick both allow keeping them, and otherwise by the 24 h sweep. A kept transcript can be deleted alone. On `finish` the capture transcribes what it holds (waiting at most two minutes, the rest becomes a gap) before the notes run.

- **How it fails:**

  - `needs_local_transcription` (409) on invite; meeting ends `failed:needs_local_transcription` (engine gone before the start) or `failed:transcription` (three windows in a row failed). An engine error on a window is a gap and a warning with the error type.

  - Not built here: in-meeting answers, provider captions as a stream, a provider adapter, and speaker labels beyond the provider's own stream ids. The engine takes one window at a time, so a long window holds a dictation request for its duration.

- **Tests:**

  - `tests/test_meetings_capture.py` covers the segmenter (windows, overlap, pauses, odd frames, short tails, stitching), per-speaker and mixed streams, gaps and delay, the engine slots and dictation priority, the no-disk and no-log checks, retention (deleted after notes, kept only with both allowances, deletion, sweep, stop), `needs_local_transcription` and the clean workspace (nothing created, no thread, no engine call). `tests/test_commonplace_voice.py` checks the dictation marker. A generated 220 Hz tone as the PCM fixture: `tests/fixtures/meetings/`.
