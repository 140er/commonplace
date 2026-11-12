# Meeting-join bots: design note

Card #463. Status: design note, nothing built yet. Docs only; no code in this PR. Builds on the Bots
layer (`docs/bots.md`, `commonplace/bots/`, card #429) and follows the structure of the chat-platform
note (`docs/design/chat-platform-bots.md`, card #455): one adapter interface, a surface policy
stricter than the web app, everything off until configured. Related: shared team bots (#462),
learning memory for bots, server-local dictation (`commonplace/voice.py`).

## 1. Problem and goal

- Decisions and action items are said out loud in video meetings and then lost, or retyped by hand
  by whoever remembers.
- Goal: a signed-in person invites a bot into one meeting. It joins as a visibly named
  participant, listens, and afterwards files notes, decisions and proposed action items into that
  person's own workspace. It answers in the meeting chat only when addressed.
- Everyone in the meeting is told, in the meeting, that a bot is listening and taking notes, and
  any host can remove it. If it cannot tell them, it does not listen.
- Audio is transcribed on the user's own machine by default; nothing leaves the workspace unless
  the owner switched an external service on for meetings.
- Default off at three levels (workspace owner, person, each meeting). A provider is one adapter;
  adding one adds no new rules.

## 2. Decisions

- **One lifecycle, many adapters.** Google Meet, Zoom, Teams and others are `MeetingAdapter`
  implementations (section 5). Join, leave, the audio or caption stream, chat send and chat receive
  are the whole interface. The meeting engine, consent rules, transcription, notes and answers
  never name a platform. Provider names appear only in adapter modules and fixtures.
- **Official bot-join interfaces only.** An adapter uses a platform's documented way for an
  application to join as a named, non-human participant. Headless-browser guests that pose as a
  person, scraping and anything that hides the bot from the participant list are out (section 15).
- **The bot is not a principal, and not in the meeting as the person.** It joins under the
  provider app identity with a server-built display name ("Notes bot for <inviter>"). It holds no
  Commonplace token. Everything filed afterwards is filed as the inviter, re-resolved from the
  session layer (`sessions.person_principal`), and only after the inviter's tap (section 8).
- **Consent is fail-closed.** No capture starts until the in-meeting announcement was delivered
  (adapter-confirmed). If it cannot be delivered (host disabled chat, send error), the bot leaves
  and keeps nothing. A host removing the bot, or anyone in the meeting asking it to stop, ends capture
  at once and discards what was captured (section 6).
- **The inviter owns the output; nobody else gets it.** Notes, proposed actions and any
  transcript are the inviter's private data in the workspace. Participants, the workspace owner
  and other members get nothing automatically and can read nothing (the Bots-layer rule: owners
  are not special readers). Sharing into a project is a separate, explicit act by the inviter.
- **Generated text is labelled and never anyone's words.** Notes and answers are model output and
  are stored and shown as generated, with evidence pointers. A transcript, when kept, is the
  verbatim record and is labelled as other people's speech. Nothing generated is stored as a
  person's message or filed as if the inviter said it (the Bots-layer verbatim rule, applied to
  meetings).
- **Local-first.** Transcription uses the same server-local engine as dictation (`voice.py`:
  Moonshine and faster-whisper, PCM in memory only). Notes and answers use the model the owner
  configured for chat (`bots/modelcfg.py`); if that model is hosted, meetings refuse to use it
  until the owner turns on a separate "external model for meetings" switch (open question 3).
  No external speech-to-text service is part of this design.
- **No conversation content in the control database or logs.** Meeting text lives in
  `meetings.sqlite3` (encrypted with the workspace when encryption is on). The control database
  holds counters and audit rows without text, as the Bots layer does.
- **Agents do not administer meetings.** Invites, credentials, switches and approvals are
  signed-in web actions with same-origin writes. MCP gets one read-only status tool.

## 3. What exists and what changes

Reused as is: the Bots turn engine's model call, redaction, per-person budget and concurrency
limits; the frozen-text, compare-and-set pattern of `bot_pending`; the encrypted per-workspace
SQLite convention (`mempalace.sqlite_crypto`) and the sealed credentials of the per-person GitHub
grants; `voice.py`'s engine, model config and PCM-in-memory rule; project boards and `create_work`;
the person's memory wing and its secret scan; the audit log; `ratelimit`.

New: `commonplace/meetings/` (store, lifecycle, engine, adapter interface, fake adapter, routes),
one workspace file `meetings.sqlite3`, and a small control-database table of per-day counters.

Also new, small: a streaming segmenter around `VoiceEngine.transcribe` (the engine today takes at
most 60 s of PCM per call and is shaped for dictation), and a way to ask a resolved model whether it
is local (loopback or the hub's own runtime) or hosted.

## 4. Data model

New workspace file `meetings.sqlite3`. Ids are `uuid4().hex`; times are UTC `YYYY-MM-DDTHH:MM:SSZ`;
provider ids are opaque text and always the provider's immutable ids, never display names.

```sql
CREATE TABLE meeting_settings (            -- one row, the workspace owner's switches
  enabled          INTEGER NOT NULL DEFAULT 0,   -- master switch, default off
  transcript_ok    INTEGER NOT NULL DEFAULT 0,   -- owner allows people to keep transcripts at all
  external_model_ok INTEGER NOT NULL DEFAULT 0,  -- owner allows a hosted model for meetings
  max_concurrent   INTEGER NOT NULL DEFAULT 1,   -- meetings the hub captures at once
  max_minutes      INTEGER NOT NULL DEFAULT 120, -- hard stop per meeting
  daily_meetings   INTEGER NOT NULL DEFAULT 10,  -- whole workspace
  changed_by TEXT NOT NULL, changed_at TEXT NOT NULL
);
CREATE TABLE meeting_connections (          -- provider credentials, one row per configured provider
  id          TEXT PRIMARY KEY,             -- also the unguessable part of the intake URL
  provider    TEXT NOT NULL,                -- adapter name
  label       TEXT NOT NULL,                -- owner-typed, never trusted
  secrets     BLOB NOT NULL,                -- sealed; write-only, never read back by any API
  enabled     INTEGER NOT NULL DEFAULT 0,   -- default off; cannot be enabled before a passing `test`
  tested_at   TEXT,
  created_by  TEXT NOT NULL, created_at TEXT NOT NULL, disabled_at TEXT,
  UNIQUE (provider, label)
);
CREATE TABLE meeting_people (               -- a person's own opt-in and defaults
  user_id          TEXT PRIMARY KEY,
  enabled          INTEGER NOT NULL DEFAULT 0,   -- a person who never opted in cannot invite
  keep_transcript  INTEGER NOT NULL DEFAULT 0,   -- default for new meetings; per meeting overridable
  language         TEXT NOT NULL DEFAULT 'en',   -- a language the local engine supports
  notes_wing       TEXT,                         -- where approved notes are filed; NULL = own wing
  changed_at       TEXT NOT NULL
);
CREATE TABLE meetings (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,                -- the inviter; the only reader
  connection_id TEXT NOT NULL REFERENCES meeting_connections(id),
  provider      TEXT NOT NULL,
  join_ref      BLOB,                         -- sealed meeting link/passcode; wiped when the meeting ends
  ext_meeting   TEXT,                         -- opaque provider meeting id once known (no passcodes)
  title         TEXT NOT NULL,                -- inviter-typed; provider titles are untrusted data
  bot_name      TEXT NOT NULL,                -- the exact name shown in the meeting
  state         TEXT NOT NULL,                -- see state machine, section 6
  keep_transcript INTEGER NOT NULL DEFAULT 0,
  board         TEXT,                         -- optional pinned project for in-meeting answers
  disclosure    TEXT NOT NULL DEFAULT 'none' CHECK (disclosure IN ('none','titles','full')),
  attested_at   TEXT NOT NULL,                -- the inviter confirmed they may bring a bot (section 6)
  start_at      TEXT,                         -- NULL = join now; else scheduled (up to 12 h ahead)
  announced_at  TEXT,                         -- adapter-confirmed announcement; capture needs this
  started_at TEXT, ended_at TEXT,
  end_reason    TEXT,                         -- done | inviter_left | host_removed | stop_requested |
                                              -- timeout | failed:<code> | cancelled
  request_id    TEXT NOT NULL,                -- client-supplied, unique per person: a retried invite is one meeting
  lease_at      REAL,                         -- worker heartbeat (same idea as bot_leases)
  created_at    TEXT NOT NULL,
  UNIQUE (user_id, request_id)
);
CREATE TABLE meeting_participants (         -- deleted with the meeting
  meeting_id TEXT NOT NULL, ext_participant TEXT NOT NULL,
  label      TEXT NOT NULL,                 -- the provider's display name, untrusted text
  host       INTEGER NOT NULL DEFAULT 0, joined_at TEXT NOT NULL, left_at TEXT,
  PRIMARY KEY (meeting_id, ext_participant)
);
CREATE TABLE meeting_segments (             -- transcript working set; NOT append-only (see below)
  meeting_id TEXT NOT NULL, seq INTEGER NOT NULL,
  ext_participant TEXT,                     -- NULL when the stream is mixed
  t_start_ms INTEGER NOT NULL, t_end_ms INTEGER NOT NULL,
  text TEXT NOT NULL,
  PRIMARY KEY (meeting_id, seq)
);
CREATE TABLE meeting_notes (                -- generated, labelled, always with evidence
  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL,
  kind     TEXT NOT NULL CHECK (kind IN ('summary','decision','action','open_question')),
  text     TEXT NOT NULL,                   -- model text, shown as generated
  evidence TEXT NOT NULL,                   -- JSON: segment seq ranges; every note has at least one
  state    TEXT NOT NULL DEFAULT 'draft',   -- draft | kept | dismissed ; actions: proposed | approved | dismissed
  edited   INTEGER NOT NULL DEFAULT 0,      -- the person changed the text; it is then theirs
  model    TEXT NOT NULL,
  filed_ref TEXT,                           -- memory id or "board:card" once filed
  filed_request_id TEXT,                    -- idempotency key of the filing write
  decided_by TEXT, decided_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE meeting_chat (                 -- in-meeting chat intake journal; one row per provider event
  meeting_id TEXT NOT NULL, ext_event TEXT NOT NULL,
  ext_participant TEXT,
  state TEXT NOT NULL,                      -- received | ignored | answered | refused | failed:<code>
  question TEXT, answer TEXT,               -- kept only for addressed messages; deleted with the meeting
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (meeting_id, ext_event)
);
CREATE TABLE meeting_outbox (               -- every chat post the bot makes; nothing posts without a row
  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL,
  kind TEXT NOT NULL,                       -- announce | reminder | answer | notice | leave_notice
  ref  TEXT NOT NULL,                       -- fixed-text key, or a meeting_chat ext_event for an answer
  state TEXT NOT NULL,                      -- queued | sending | sent | unknown | cancelled
  ext_post TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT,
  UNIQUE (meeting_id, kind, ref)
);
CREATE TABLE meeting_events (               -- lifecycle journal, no text
  meeting_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (meeting_id, seq)
);
```

**Segments are the deliberate exception to append-only.** `bot_messages` refuses edits and
deletes; a transcript of other people's speech should not live longer than needed. With
`keep_transcript = 0`, rows are deleted for real right after notes are generated, or by a 24 h
sweep if generation never completes (crash recovery needs them for a short while). With
`keep_transcript = 1` they stay until the inviter deletes the meeting. Deleting a meeting deletes
participants, segments, notes, chat rows and outbox rows; anything the inviter filed into memory or
cards stays (it is theirs) and is listed so they can delete it too.

**Sealed secrets.** `meeting_connections.secrets` holds provider credentials (app id, client secret,
signing key or certificate) as one authenticated-encryption blob with the connection id as
associated data, using the per-workspace key store the person grants already use. `join_ref` is
sealed the same way because meeting links often carry a passcode, and is overwritten with NULL
when the meeting ends. Write-only: no route and no MCP tool returns either, and no log line, audit
row or error string carries any part of them. If the key store cannot serve the workspace, the
write is refused instead of stored in the clear. No credential lives in the repository, in
environment variables of agents, or in the control database; fixtures use fake values.

**Who may read or write what.**

| Data | Workspace owner | Inviter | Other members, agents, tokens, meeting participants |
|---|---|---|---|
| `meeting_settings` | read, write | read (enabled and limits only) | none |
| connection row without `secrets` | read, write, test, disable | none | none (404) |
| `secrets`, `join_ref` | write only (secrets) | none (the link they typed is not echoed back) | none |
| `meeting_people` row | none | own row | none |
| meeting row: state, times, end reason | counts and states only, no titles | own meetings | none (404) |
| segments, notes, chat rows, participants | none | own meetings | none |
| `meeting_outbox`, `meeting_events` | none (no text, internal) | none | none |

The owner can see that meetings happen and can stop them, never what was said. A bot is not
readable by anyone but its person; meetings follow that rule.

## 5. Surface and adapter interface

Names only.

**Person (signed-in member, same-origin writes)**

- `GET`/`PATCH /api/meetings/me` (opt-in, `keep_transcript`, `language`, `notes_wing`).
- `POST /api/meetings` (invite: link, title, `keep_transcript`, optional `board` and `disclosure`,
  optional `start_at`, the attestation, `request_id`), `GET /api/meetings`, `GET /api/meetings/<id>`,
  `POST /api/meetings/<id>/leave` (the bot leaves now; the inviter's own stop),
  `DELETE /api/meetings/<id>`.
- `GET /api/meetings/<id>/notes`, `PATCH /api/meetings/<id>/notes/<note>` (edit text, dismiss),
  `POST /api/meetings/<id>/notes/<note>/file` (keep a note in memory) and
  `POST /api/meetings/<id>/actions/<note>/approve` (create a card). Both are explicit taps with
  `request_id`.
- `GET /api/meetings/<id>/transcript` (only when kept), `DELETE /api/meetings/<id>/transcript`.

**Owner (signed-in owner, same-origin writes)**

- `GET`/`PUT /api/meetings/settings`.
- `GET`/`POST /api/meetings/connections`, `PATCH`/`DELETE /api/meetings/connections/<id>`
  (`enabled`, `label`, new `secrets`), `POST /api/meetings/connections/<id>/test`.
- `GET /api/meetings/overview` (counts per state, no text), `POST /api/meetings/<id>/stop` (owner
  stop, see section 6).

**Intake (public, no session; adapter-verified)**

- `POST /api/meetings/in/<connection_id>/events`: provider webhooks (participant joined or left,
  chat message, meeting ended, bot removed). Providers that deliver events over an outbound
  socket use `caps.transport = 'socket'` and need no public route.

**Agents and MCP.** One read-only tool, `meeting_status` (own meetings: state, times, counts; no
text). Nothing that invites, joins, files, approves or reads secrets, notes or transcripts.

**CLI.** `commonplace meetings status` and `commonplace meetings disable [--all]` (the kill switch:
switches the master flag off, makes every active meeting leave and discard). Secrets and meeting
links are never accepted on a command line.

**Adapter interface (`commonplace/meetings/adapter.py`).**

```
Adapter.provider: str
Adapter.caps: Caps(chat_send, chat_receive, per_speaker_audio, captions, remove_detect,
                   participant_list, lobby, max_text, transport)
Adapter.parse_link(link) -> JoinRef | Reject(reason)           # validates the host/shape; no network
Adapter.test(connection) -> TestResult                         # credentials and permissions
Adapter.join(connection, join_ref, display_name, idempotency_key) -> JoinHandle | Lobby | Reject
Adapter.leave(connection, handle) -> None                      # idempotent
Adapter.events(connection, handle) -> iterator of Event        # lifecycle + chat + participants
Adapter.audio(connection, handle) -> iterator of Frame         # 16 kHz mono PCM, optional speaker id
Adapter.post_chat(connection, handle, text, idempotency_key) -> PostResult
Adapter.verify(connection, headers, body) -> Verified | Reject # for webhook providers
```

`Event` is one of: `joined`, `admitted`, `lobby_timeout`, `participant_joined|left` (with host flag),
`chat` (with `addressed`, set by the adapter from a real mention or the bot's name at the start
of the message; never inferred from speech), `removed` (the bot was removed or the meeting
locked it out), `meeting_ended`. `Frame` carries PCM and an optional `ext_participant`. Adapters
render and escape chat text for their platform, split it to `caps.max_text`, and neutralize
broadcast mentions. `parse_link` accepts only the provider's own meeting hosts, so an invite link
cannot make the hub call an arbitrary URL (SSRF). A `FakeAdapter` implements the whole interface in
memory, scripts events and audio, and records every post, so the engine is tested with no
platform and no audio device. Adapters that cannot offer `chat_send` are refused for use
(consent needs it, section 6).

## 6. Consent and visibility

**Who may invite.** A signed-in member who opted in (`meeting_people.enabled`) in a workspace
where the owner enabled meetings and at least one connection is enabled. An invite needs the meeting
link and an attestation tap: "I am allowed to bring a notes bot into this meeting, and its
participants will be told it is there." The attestation is stored with a timestamp; it is the
inviter's statement, not a legal conclusion, and the web page says plainly that the inviter is
responsible for permission from the meeting's host. Every meeting is its own explicit act; there is
no calendar auto-join and no standing "join all my meetings" in this design.

**State machine** (`meetings.state`):

```
requested -> joining -> [lobby ->] announcing -> live -> ending -> processing -> done
                 \          \            \          \
                  \          \            \          +--> removed (host_removed | stop_requested)  -> discarded
                   +----------+------------+--> failed:<code> | cancelled | timeout
```

1. **requested.** The invite is stored; a worker takes the lease and starts `joining` (immediately, or
   at `start_at` minus two minutes).
2. **joining and lobby.** The bot joins with the server-built name `"Notes bot for <inviter>"`. The
   inviter's display name is sanitized (length, control characters, no markup). If the platform
   holds new participants in a lobby, the bot waits for a host to admit it, at most 10 minutes
   (`lobby_timeout` leaves and ends the meeting as `failed:not_admitted`). It never tries to bypass
   a lobby or a locked meeting.
3. **announcing.** Before any audio frame is processed, the bot posts the fixed announcement in the
   meeting chat and the outbox row must reach `sent`:
   *"I'm a notes bot, not a person. <Inviter> invited me to listen and take notes. The audio is
   transcribed on their own server. Any host can remove me; type 'bot stop' to make me leave and
   discard everything. Ask <Inviter> about the notes."*
   The text is a server constant (localised), never model output. Frames received before `sent` are
   dropped, not buffered. If delivery fails or `caps.chat_send` is off, the bot leaves and the meeting
   ends as `failed:no_announcement`; nothing is stored.
4. **live.** Capture runs (section 7). A reminder of the announcement is posted when the participant
   count grew since the last announcement, at most once per 10 minutes, so late joiners are told. The
   meeting ends at `meeting_ended`, the inviter's leave, `max_minutes`, or a stop.
5. **Removal and stop, discard rather than ask.** If the provider reports the bot was removed, if
   any participant sends `bot stop` (the only command anyone may give), or if the owner stops the
   meeting, capture ends at once, the bot posts one fixed notice, the working segments and the audio
   buffer are discarded for real, no notes are generated, and the inviter sees "A host removed the
   notes bot. Nothing was kept." A single objection wins; the safe direction needs no authority
   check, and griefing costs the inviter only a re-invite (open question 4).
6. **ending and processing.** Notes are generated from the segments (section 8); then the segments
   are deleted unless `keep_transcript = 1`. `join_ref` is wiped when `ending` starts.

**Visibility summary.** The bot is a named participant that says it is a bot and whose it is; the
announcement says what it does. Participants cannot read notes and are told whom to ask. The bot
never speaks aloud in v1.

**Transcript retention is opt-in twice.** The owner must allow transcripts (`transcript_ok`), and the
inviter must tick keep-transcript for that meeting (default from `meeting_people.keep_transcript`, off).
The announcement is the same either way; when a transcript is kept the notes page says so, and the
inviter can delete it at any time without touching the notes.

## 7. Transcription

- **Where.** The meeting worker runs on the hub's own host, in the same trust zone as the encrypted
  stores. It pulls PCM from the adapter, segments it, and calls the local engine. PCM lives in memory
  only (the `voice.py` rule), is never written to disk, never logged, never passed to a model and
  is dropped on leave, removal, crash or restart. No audio or transcript goes to the control database.
- **Engine.** The existing `VoiceEngine`: Moonshine for English, faster-whisper for other
  languages, models from the existing `COMMONPLACE_VOICE_MODEL_*` settings. Languages beyond
  those two follow the voice settings, not this feature. If voice is not available on the host, the
  meeting cannot be created (`needs_local_transcription`): there is no silent fallback to a service.
- **Segmenter.** 20 s windows with a 2 s overlap, cut at silence when the engine reports one, each
  well below the engine's 60 s call limit, text stitched by timestamp. Per-speaker streams (when
  `caps.per_speaker_audio`) are transcribed separately and carry `ext_participant`; a mixed stream
  produces segments with no speaker and notes then never attribute words to a named person.
  No speaker identification or voice matching is done: nothing here derives who someone is from
  their voice.
- **Budget.** Transcription competes with dictation for CPU. A separate semaphore gives meetings at
  most `max_concurrent` engines, dictation keeps priority, and a meeting that falls behind real time
  by more than 60 s posts one notice to the inviter's meeting page ("transcript is delayed") and
  keeps going; it never drops audio silently, it drops it with a recorded gap segment so notes can
  say "(gap)" instead of inventing the missing part.
- **Optional provider captions.** `caps.captions` lets an adapter hand over the platform's own
  captions instead of audio. That is the meeting provider's service, already in use by the
  participants, but it is still a third party holding the speech, so it is a per-connection
  option the owner sets (`source = 'captions'`), off by default, and disclosed on the invite page
  and in the announcement ("captions come from the meeting platform").
- **Secret guard.** Segment text passes the redaction and secret scan before it reaches a model or
  memory. A detected credential is replaced by a fixed marker in what the model sees and in notes.

## 8. Notes, actions and filing

1. **Generate.** When the meeting reaches `processing`, one model call per meeting (map-reduce over
   segment batches for long meetings) turns segments into a structured result: a short summary,
   decisions, action items and open questions. The model gets the transcript as quoted data and no
   tools. Each item must cite evidence (segment sequence ranges). The server drops any item whose
   evidence does not exist, and drops any attribution to a participant that the segments do not
   carry. Output goes to `meeting_notes` as `draft` (`proposed` for actions). The model is the
   configured chat model; a hosted one is refused unless `external_model_ok` (open question 3).
   Without a usable model nothing is generated and the transcript is handled as per its retention
   setting (kept only if the inviter opted in; otherwise deleted by the 24 h sweep, and the
   inviter is told generation could not run).
2. **Review.** The inviter reads the notes on the meeting page. Each note shows "generated" and
   the evidence ranges (with the transcript, the text; without it, only offsets and speaker
   labels). The inviter can edit a note (it is then marked `edited` and is their words), dismiss it,
   or keep it.
3. **File a note.** *Keep* writes the note into memory as the inviter, with the inviter's own
   explicit tap, through the normal memory write path and its secret scan. The drawer carries
   provenance: meeting id, date, "generated by notes bot", model, evidence ranges. It goes to the
   inviter's own wing or the wing the inviter set in `notes_wing`; a project wing is allowed only if
   the inviter may write to it. Writing there is not routine: no automatic filing, even of the
   summary.
4. **Approve an action.** *Approve* creates a card through the same path as `create_work`, as the
   inviter, on a project the inviter picks and may write to, with `request_id` equal to the note id
   so a retried tap creates one card. The card text is the frozen text the person saw, with a
   provenance line ("proposed by notes bot from the meeting on <date>, approved by <inviter>"). An
   unedited action's author is shown as approved-from-generated; an edited one is the inviter's.
   Cards are never created without the tap, for anyone's request, including one typed into the meeting chat.
5. **Idempotent.** `filed_request_id` and the unique `request_id` on the write mean a double tap,
   a retried request or a replayed event files once. A crash between the write and `filed_ref`
   is resolved by looking up the write by `request_id` before retrying.
6. **Verbatim honesty.** The notes are a derived product and say so. The verbatim record is the
   transcript, which is opt-in. The model never writes into a person's own messages, never into
   existing cards or drawers, and never overwrites an earlier note; regeneration produces a new
   set and the previous one stays until the inviter dismisses it.

## 9. In-meeting answers

- **Addressed only.** A chat message triggers a turn only when the adapter marks it `addressed`
  (a real mention of the bot, or the bot's name at the start) and it is a chat message, not speech.
  Everything else in the chat is ignored: not stored, not sent to a model. Voice addressing
  ("hey notes bot") is out, because wrong triggers would post to everyone.
- **The asker is not a person.** A participant has no Commonplace identity. Their message is
  quoted data. The turn runs with the **inviter's** scope narrowed to the meeting's own
  context, and only read tools exist:
  - *The meeting so far:* the segments and draft notes of this meeting only (everyone present
    already heard it).
  - *The pinned project, if any:* the inviter's read right on that one project intersected with the
    meeting's `disclosure` (`none` by default: nothing; `titles`: titles, statuses, counts,
    open-decision existence; `full`: bodies and comments as the inviter reads them, with a warning
    that every participant, including people outside the workspace, sees replies).
  - No personal memory, no persona text beyond the bot's name and job, no other projects, no
    machines, no write tools. An asker therefore cannot make the bot read, say or do anything the
    inviter could not, and in addition cannot reach beyond the meeting and one chosen project.
- **Writes are not possible from chat.** "Add an action item for X" can be answered with a
  proposed action (`meeting_notes`, kind `action`, state `proposed`, tagged with the asker's label)
  that waits for the inviter's approval later; it never creates a card, and the chat reply says so.
- **Replies.** One chat message, at most 600 characters, prefixed with the bot's name, plain text,
  broadcast mentions neutralized, links the model produced stripped unless they point to the hub.
  Per-meeting answer cap (20) and a per-participant cooldown (30 s); over the cap the bot says once
  that it will not answer further. Questions and answers are stored in `meeting_chat` (for the
  inviter, deleted with the meeting), not in anyone's bot thread.
- **Engine reuse.** An answer is one Bots-engine model call with a new `meeting` surface: same model
  resolution, redaction, per-person budget and concurrency limits as a channel turn in the chat
  note (section "Scope in channels" there). Slice 5 begins with a short spike deciding whether the
  surface runs on a hidden per-meeting thread in the bots store or on a thin call path that reuses the
  same model client, tool dispatcher and budget; either way no second budget and no second tool
  set exists.

## 10. Permission and privacy rules

- **Default off at three levels:** the workspace has meetings off; a person has not opted in; a
  meeting does not exist until that person invites it. A new connection is disabled until a test
  passes and the owner enables it. Transcript retention and hosted models are two further owner
  switches, also off.
- **Owner-only:** the master switch, connections and credentials, transcript and hosted-model
  allowances, limits, the stop. **Person-only:** opt-in, invites, notes, transcript, approvals,
  deletion. A member cannot see another member's meetings; the owner sees counts.
- **Intersection, always.** What the bot reads in a meeting is the meeting's own content plus the
  inviter's read right on at most one pinned project at the chosen disclosure. What it files is
  what the inviter may write, with their tap. The bot never states or files anything the inviter could
  not read or write in the web app.
- **Minimum provider scopes:** join, audio or captions, and chat; no calendar, directory,
  recording or user-listing scopes.
- **Nothing leaves the workspace.** No telemetry, no sync, no export of notes or transcripts, no
  external speech service. The only non-local paths are the ones the owner switched on:
  a hosted model for notes and answers, and provider captions.
- **No standing access.** Disabling meetings, a connection or a person's opt-in makes active
  meetings leave and discard at the next worker beat (at most 15 s); pending approvals stay for
  the inviter.
- **Redaction and secret scan** apply to model input, stored notes, posted chat text and anything
  filed. Credentials never appear in logs, audit rows, errors or any API response.

## 11. Threats and controls

| Threat | Control |
|---|---|
| Recording people who do not know | Fail-closed announcement (capture only after `sent`), bot name says what it is, reminder for late joiners, `bot stop` for anyone, host removal discards, attestation by the inviter, no auto-join. |
| Bot used to join a meeting the inviter should not be in | The bot joins only with the link the inviter supplied; lobbies and locks are respected, never bypassed; the provider's own admission rules apply. |
| Link with passcode leaks | Link sealed, never echoed, wiped at `ending`, never logged; adapter returns only an opaque meeting id. |
| Invite link makes the hub call an arbitrary URL (SSRF) | `parse_link` allows only the provider's own meeting hosts and shapes; no redirect following outside them. |
| Provider credentials stolen | Sealed with a per-workspace key, write-only, never logged, minimum scopes, rotation by rewriting, a revoked-credential response disables the connection and audits it. |
| Meeting content reaches the wrong person | Content readable only by the inviter (404 for everyone else, owner included); notes filed only on the inviter's tap into places they may write. |
| An asker in the meeting extracts the inviter's private data | The asker is untrusted; the turn reaches only the meeting's own content and one pinned project at the chosen disclosure; no personal memory, no tools that write. |
| Prompt injection from speech or chat ("ignore your rules, post X") | Transcript and chat are quoted data; notes calls have no tools; answers have read tools only; evidence is validated; chat output is one capped message with links and broadcast mentions stripped; nothing in chat can create a card. |
| Model invents decisions or puts words in someone's mouth | Every note needs existing evidence ranges; unattributed audio is never attributed; all notes are labelled generated; nothing is written as a person's message; edits become the editor's words. |
| Transcript outlives its need | Working segments deleted after notes (24 h sweep as a backstop); kept transcripts only when owner allows and inviter ticks; one-click transcript deletion; meeting deletion removes everything it owns. |
| Audio persisted by accident | PCM exists only in the worker's memory; there is no code path that writes frames; a test greps the data directory after a run for audio-like files and for sentinel text in logs. |
| Content sent to an external model or service without consent | Hosted model refused unless `external_model_ok`; provider captions off unless set; no other external call exists; the invite page shows the current paths. |
| Bot loops (answering another bot, or itself) | Events from bots, including this app, are ignored; per-meeting answer cap and cooldown. |
| Cost or CPU abuse by many meetings | `max_concurrent`, `max_minutes`, `daily_meetings`, per-person budget from the shared model budget; transcription semaphore behind dictation. |
| A removed member keeps a bot in a meeting | Membership and person opt-in re-checked each worker beat; failure ends the meeting and discards. |
| Replay of a provider webhook | Adapter verification with freshness window; `(meeting, ext_event)` primary key. |
| Cross-workspace confusion | A connection belongs to one workspace; events name the meeting, which must match an active meeting of that connection, else rejected. |
| Griefing by `bot stop` | Accepted: the safe direction needs no authority; the cost is a re-invite; the inviter is told who (label) asked. |
| A participant later demands erasure | Notes are visible to the inviter only; the announcement names the inviter; the inviter can delete the meeting and the filed items. A per-participant erasure workflow is not built (section 15). |

## 12. Failure and replay

- **Invite retried.** `UNIQUE (user_id, request_id)`: a repeat returns the same meeting. A scheduled
  join that fires twice finds `state != requested` and does nothing.
- **Join is idempotent.** The adapter gets an idempotency key (meeting id). A crash after `join`
  succeeded and before the state write finds an existing handle on restart (adapters expose it) or
  leaves and rejoins once; the announcement outbox is unique per `(meeting, kind, ref)`, so a
  rejoin does not announce twice unless it is the explicit late-joiner reminder.
- **Worker crash or hub restart mid-meeting.** The lease expires. Recovery looks at the state:
  `live` with segments already stored is treated as `ending` (the bot leaves, notes are generated
  from what was captured, the meeting ends as `interrupted`). The bot does not silently rejoin and
  keep listening: a rejoin would need a new announcement and a person's decision. The PCM buffer is
  lost by design.
- **Posting is at most once.** As for the chat bridge: `sending` is marked before the call and
  `sent` after; a crash between leaves `unknown`, resolved by the adapter's lookup if it has one,
  otherwise not retried blindly. The announcement is the exception: `unknown` means not confirmed,
  so capture stays off, the bot leaves and the meeting fails closed.
- **Provider drops the bot.** `removed` is handled as in section 6 (discard); a transient
  disconnect with no `removed` event retries the join once within 60 s; capture stays off while
  disconnected and the gap is recorded.
- **Provider down.** `joining` fails after bounded retries as `failed:provider_unavailable`; no
  partial state beyond the meeting row.
- **Notes generation fails.** State stays `processing` with `attempts` bounded; segments remain
  (until the sweep) so a retry can run; each retry produces a new draft set and never duplicates
  a filed note (filing has its own request id).
- **Approve and file replays.** Double tap, retried request or restarted hub: the write finds the
  request id and returns the existing card or drawer.
- **Disable mid-flight.** Active meetings leave and discard; outbox rows become `cancelled`; notes
  already generated remain with the inviter.
- **Bad configuration.** `test` fails with a plain reason (credentials, missing permission, no chat
  send); the connection cannot be enabled until a test passes.

## 13. Slice plan

Six slices, one PR each, in order. UI is built by the interface owner on top of the person and owner
routes; no slice touches `app.js`, `rail.js` or shell files, so the node-harness wrappers are
unaffected. A UI PR that follows runs all node wrapper files.

Common proof for every slice: only the new tests plus the docs obligations and fragments tests run
in isolation (never the whole suite). Style is RED then GREEN: the slice's tests are
written first, shown failing on the base (missing module, route or refusal), then passing on the
head. Each slice has a **clean-workspace test** on a fresh workspace with generic fixture names
only, asserting the feature is absent or off until configured (every `/api/meetings` route 404s or
refuses, nothing is created, no worker starts).

1. **Foundation: store, sealed secrets, switches, kill switch.** `meetings.sqlite3` schema, sealing,
   owner settings and connection routes, `test` through the adapter interface, person opt-in,
   `commonplace meetings status|disable`, audit rows, `meeting_status`. Acceptance: master switch
   off means every route refuses and nothing is created; secrets and `join_ref` are write-only and
   never in a response, log or audit row (a sentinel-token test greps all three); a never-tested
   connection cannot be enabled; a person who did not opt in cannot invite. Proof: RED = routes and
   tables missing on base, GREEN = same tests on head; sentinel leak test; clean-workspace test.
2. **Lifecycle over the fake adapter.** Adapter interface and `FakeAdapter`, invite and attestation,
   worker with lease, state machine, fail-closed announcement, lobby timeout, host removal and
   `bot stop` with discard, leave, `max_minutes`, restart recovery. Acceptance: no frame is
   processed before the announcement is `sent`; a failed announcement ends the meeting with nothing
   stored; removal and stop discard; a repeated invite is one meeting; crash mid-meeting ends as
   `interrupted` and never rejoins; disabling the workspace makes active meetings leave. Proof:
   RED/GREEN on scripted fake meetings; a crash-between-steps test; clean-workspace test.
3. **Capture and local transcription.** Segmenter around the existing engine, per-speaker and
   mixed streams, gap segments, semaphore behind dictation, retention (`keep_transcript`, sweep,
   delete), `needs_local_transcription`. Acceptance: audio never touches disk (a test scans the
   data directory and logs); segments are deleted after notes unless kept; a transcript is stored
   only when owner and inviter both allowed it; segments carry speaker ids only when the stream does;
   a delayed engine records a gap, not silence. Proof: RED/GREEN with recorded generic PCM
   fixtures and a stub engine; the real engine only in the existing opt-in voice tests; clean-workspace
   test.
4. **Notes, review and filing.** Generation with evidence validation, hosted-model refusal,
   `meeting_notes`, edit/dismiss/keep, approve-to-card, memory provenance, idempotency, deletion.
   Acceptance: every stored note has valid evidence; unattributed audio is never attributed; a hosted
   model is refused unless the owner allowed it; nothing is filed without the tap; a double tap
   makes one card; filing respects the inviter's write rights (a project they cannot write to is
   refused); notes are readable by the inviter only. Proof: RED/GREEN against a stub model; a
   prompt-injection transcript test (no tools called, evidence enforced); access matrix test (owner,
   other member, agent token all 404); clean-workspace test.
5. **In-meeting answers.** Chat intake, `addressed` filter, journal and idempotency, the `meeting`
   surface (spike first, see section 9), disclosure levels, caps, cooldown, outbox, proposed actions.
   Acceptance (the card's list): only addressed messages get replies; an answer uses only the
   meeting's content and the pinned project at the chosen level, and never exceeds the inviter's
   read right (a project they cannot read yields a refusal); disabled-by-default; no write is
   possible from chat; a duplicate chat event answers once. Proof: RED/GREEN over the fake adapter;
   a leak test with sentinel data in an unpinned project and in personal memory; replay test;
   clean-workspace test.
6. **First provider adapter.** A short spike first: for each candidate provider (examples: a
   workplace chat/meeting suite, a conferencing service, an office suite) record in the PR whether
   its official interface gives a named non-human participant, an audio or caption stream, chat
   send and receive, and what scopes and hosting it needs. Implement the one that meets all
   three with the fewest requirements. Acceptance: the slice-2 to slice-5 suites pass unchanged
   against it with hand-written generic payload fixtures (one parametrized contract suite runs both
   the fake and the real adapter); forged or stale webhooks are rejected; `parse_link` refuses
   foreign hosts; no live network in tests. Proof: contract tests plus adapter fixtures. Further
   providers are separate PRs that add an adapter and pass the same contract suite.

Follow-ups, not slices: scheduled join from a calendar entry the inviter picks (still one tap per
meeting), attendee-linked answers (a linked member asks with their own scope), a socket transport
for hubs without a public URL.

## 14. Open questions for the owner

1. **Which provider first?** *Recommendation:* decide by the spike in slice 6, not now; take the first
   provider whose official interface offers a named bot participant, an audio or caption stream
   and chat send and receive. If two qualify, take the one the owner's own meetings use.
2. **Who may ask the bot a question in the meeting?** Anyone present, or only linked members?
   *Recommendation:* anyone present in v1, with the strict scope (meeting content plus one pinned
   project at `none` by default). An anonymous asker gets nothing the inviter's meeting did not
   already show. Revisit with linked-asker scopes after the chat-platform linking exists.
3. **May notes and answers use a hosted model?** *Recommendation:* no by default. A separate
   owner switch, `external_model_ok`, names the model and warns that the transcript goes to that
   service. Local-first is the product promise; an unconfigured local model means "no notes yet",
   not a silent fallback.
4. **What happens to what was captured before a host removes the bot or someone types `bot stop`?**
   *Recommendation:* discard everything from that meeting. Any objection wins; keeping the part
   before removal would turn a "stop" into "stop recording from now on", which people will not
   expect. The inviter can still type their own notes.
5. **Should a kept transcript be allowed at all?** *Recommendation:* yes, owner switch off by default
   plus a per-meeting tick, because it is the only verbatim record and the evidence for the notes.
   The owner may leave it off for compliance-minded workspaces; notes then keep offsets only.

## 15. What we would not build

- Joining as a human guest through a headless browser or any means that hides the bot or fakes a
  person's identity; recording a meeting from the outside (screen or system audio capture).
- Auto-joining all meetings, calendar sweeps, or anything that joins without a person's explicit
  per-meeting act and attestation.
- Speaker identification from voice, sentiment or engagement scoring, or any analysis of
  participants as individuals.
- Speaking in the meeting (text to speech), screen sharing, camera, or answering to voice cues.
- Sending notes to the participants, or any automatic distribution of notes, transcripts or
  actions outside the inviter's workspace; per-participant erasure workflows (the inviter's deletion
  is the control).
- External speech-to-text services, a built-in recording store, audio kept on disk, or video of any
  kind.
- A second model path, picker or budget for meetings; a bot that acts on the workspace from the
  chat (no write tools in the meeting surface).
- Writing model text as a person's message, a card or a drawer without the inviter's tap and a
  generated label.
- Platform-specific logic outside an adapter, or provider tokens in environment variables, in the
  repository, or readable through any API.
- Cross-organization or public webinar broadcast capture; meetings where the provider offers no
  way to announce to participants.
