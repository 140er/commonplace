- **Owns:**

  - In-meeting answers for meeting-join bots (`docs/design/meeting-join-bots.md`, section 9). `answers.py` is the `meeting` surface: a thin call path, not a bot thread. It uses the model the owner configured for chat (local unless the owner allowed a hosted one for meetings, through `notes.usable_model`), the same redaction and the same per-person model budget (`bots.limits`) as notes and bot turns, and offers the model no tools at all. `answer_context.py` builds the only data an answer sees, as quoted JSON. `chat_store.py` is a mixin of `MeetingStore` for the chat journal (`meeting_chat`), the caps and proposed actions. `engine.py` hands chat events to an `Answerer` once the meeting is live; `Event.bot` marks chat sent by a bot.

- **Promises:**

  - Only a message the adapter marked `addressed` is looked at; everything else in the chat (and chat sent by a bot, or without an event id) is dropped before the store and the model: not stored, not logged, not sent. `bot stop` is still handled first and from any message.

  - The asker is not a person. Their message is quoted data, and the answer runs on the inviter's scope narrowed to the meeting: its newest transcript segments (speakers as numbers, never names), its draft notes, and at most one pinned project. The inviter's read right on the pin is resolved fresh for every answer through the board service the web app uses: a pin they cannot read (now, or at all) gets one fixed refusal sentence that does not name the project, and the project is never read. Disclosure `none` (the default) uses nothing of the project, `titles` its name, statuses, counts and the number of open decisions, `full` also bodies and recent comments. No personal memory, no other project, no machine.

  - Nothing can be written from chat, because the model has no tools. A request to add something comes back as one `action` sentence, kept as a `proposed` action note tagged with the asker's label (`requested_by`) and the chat event, and the reply says it only proposed it for the inviter to approve later. Creating a card stays the inviter's tap (`filing.approve_action`, whose provenance now names the asker). A reply that breaks the closed `{answer, action}` schema is dropped.

  - Intake is idempotent and bounded: a row per `(meeting, ext_event)` in `meeting_chat`, so a replayed event answers once (and the outbox is unique per event too); a participant's second question inside 30 s is ignored; after 20 answers (refusals count) the bot posts one fixed notice that it will not answer further, then stays silent. Question and answer text live only in that row, for the inviter, and go with the meeting or a discarding end.

  - A reply is one plain post through the outbox, at most 600 characters (or the provider's `max_text`), prefixed with the bot's name, redacted, with links, `@` and markup removed (a stricter reading of "links stripped unless they point to the hub": every link goes). The model call and the post run on one thread per meeting, so a slow model never delays `bot stop`; a stop or removal closes the answerer before anything is discarded and a late reply is never posted.

- **How it fails:**

  - A journal row ends `answered`, `refused`, `ignored` (cooldown, cap, queue, not live) or `failed:<code>`: `needs_configuration`, `key_missing`, `hosted_model_not_allowed`, `limit`, `rate`, `model`, `schema`, `post`, `internal`. A failed question gets no post. The 20-answer cap is a meeting cap, so a busy meeting can also reach the shared per-minute model rate first.

  - Not built here: the public webhook intake route and any real provider adapter (the next slice), attendee-linked scopes, voice addressing.

- **Tests:**

  - `tests/test_meetings_answers.py` runs over the fake provider and a scripted model: the clean workspace (nothing created, no thread, no model call), only addressed messages, the scope at each disclosure with a leak test (sentinel data in an unpinned project and in personal memory never reaches the model), the refusal for a pin the inviter cannot read, the hosted-model refusal, no write from chat (proposed action, then one card on approval), the closed schema, the reply format, replay, cooldown and cap, and `bot stop` with a slow model.
