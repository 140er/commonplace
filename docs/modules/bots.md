## bots

- **Paths:** `commonplace/bots/`, `docs/bots.md`, `tests/_bots_helpers.py`, `tests/test_bots_api.py`, `tests/test_bots_store.py`
- **Owns:**

  - Per-person chat bots: one workspace file `bots.sqlite3` (opened like `chat.sqlite3`) holding bots, append-only messages, the tool-action journal, pending confirmations, the work a bot filed, the posts it forwarded and memory notes, plus the person-only `/api/bots` read and management routes (`docs/bots.md`).

  - `auth.person` is the single gate for `/api/bots*`: a signed-in person session, 404 for any agent token or machine credential, 401 for no credentials.

  - `blocks.HYDRATORS` fills stored block references (`work`, `decision`, `action`, `setup`) with the reader's own view through `BoardService`; an invisible or missing item becomes `gone`, an unknown type is returned as stored. `modelcfg.resolve` turns the chat front settings into a model or one of `needs_configuration`, `needs_turn_on`, `key_missing`.

- **Promises:**

  - A bot and its thread belong to one person; every store method takes the user id and answers `Missing` for another person's bot, so a foreign id looks like an unknown one (404).

  - Messages are append-only (triggers refuse UPDATE and DELETE) and verbatim; one reply per user message; a request id sent again with the same text returns the stored message, with other text it is a conflict. Memory removal is a tombstone.

  - Defaults (one project bot per readable, non-archived project plus General) are created lazily and an archived default is never recreated. At most 50 live bots per person.

  - Text bounds count the raw characters (what is stored), so padding cannot exceed them; text that cannot be stored as UTF-8 is `invalid`. A pending confirmation past its expiry can only become `expired`, whatever the requested state.

- **How it fails:**

  - Out-of-bound input is 400 `invalid`, the bot limit and conflicts are 409, a busy store is 429; error bodies carry fixed text, never conversation text.

  - A memory id outside the integer range is an unknown id (404); a `board` that is not a project slug string is 400.

  - The message, stop and confirm routes are not served yet; they answer 404.

- **Tests:**

  - `tests/test_bots_store.py` covers bounds, append-only triggers, idempotent sends and conflicts, one reply, paging, unread, the action journal, pending compare-and-set, forwards and work lookups, memory tombstones, per-user isolation and reopening the store.

  - `tests/test_bots_api.py` covers defaults per readable project, create/rename/archive and the 50-bot bound, thread paging, block hydration and status words, waiting decisions, read marks, memory, redacted display, and 404 for another person, another workspace and agent tokens.

  - Regression tests in both bots test files cover padded text bounds, unstorable text, huge memory ids, non-string boards, non-object bodies, expiry on cancel and refused cross-origin writes.
