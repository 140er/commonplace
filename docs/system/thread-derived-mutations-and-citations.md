### Thread-derived mutations and citations

- Within one workspace, a fact with any readable thread source is mutable.
  The read rule considers both flat provenance and source rows. The worker
  rechecks each authorized fact's wing and source set inside its transaction;
  rows changed or added after the probe do not acquire authorization.
- Every lookup and worker call uses the authenticated principal's workspace.
  A foreign workspace identifier resolves as missing. Internal mutation scopes
  are stripped from client arguments and hidden from tool schemas.
- Every principal, including an owner, needs read access to cited threads,
  private source ids, drawers and closets. Missing, unreadable or undecodable
  citations use the same not-found reply. Existing mutations on unreadable
  facts and rooms are no-ops; owners have no bypass.
- Provenance is decoded up to four percent-decoding rounds and checked for
  control/format characters after decoding, before whitespace is stripped.
  Recognized tokens anywhere in a provenance string must resolve readable;
  original strings are forwarded and stored unchanged. Plain strings with
  encoded controls are also refused, and partial hex tokens in filenames
  such as `thr_2024_notes.md` are recognized. Private source ids allow path
  separators, so the complete private token remains literal rather than
  guessing where a path-embedded id ends.
- Source deletes enumerate explicit ids and filter drawers, closets, AAAK
  rows and revisions by their own readable room. A roomless row needs a
  readable current drawer or, when its drawer is unresolved/absent, readable
  normalized source tokens. A resolved unreadable drawer or resolver error
  always keeps the row. Snapshots are rechecked before current drawers go;
  revision checks hold the revision lock. Unassociated legacy rows remain,
  even for an owner's MCP call. Unscoped local calls retain source-wide purge.
- Tunnel deletion rechecks endpoint snapshots, including drawer ids. Hallway
  deletion rechecks every room under its lock. Taxonomy and room-list results
  remove unreadable thread keys for every principal.
- Non-reader extraction leaves its cursor unchanged so a later reader can
  finish filing. A missing optional closet collection means no closet rows;
  other probe failures propagate. Parser/access caches expire with a response.
- No scope is persisted and no existing user text is rewritten. Rollback is
  a code revert; the per-request revocation window remains unchanged.

- **Deck settings:** the Workspace section Deck adds to Settings is plain links to existing routes; Admin appears only when `/api/me` says `can_admin` is true, the same rule the account menu uses.

- **Deck look (opt-in):** a second look and navigation for the web app, chosen per browser (`localStorage` `cmmn.ui`; nothing is stored on the server and no request carries it). It changes markup and CSS only: the same routes, APIs, permissions and CSP apply, the font it adds is a same-origin file, and with the switch off the app draws exactly as before. `#/today` and `#/compute` are reserved for Deck screens and resolve to the home screen until a screen registers for them.

- **Deck bots:** the same bots routes show responsive tiles or a focused conversation with bot chips while Deck is on; the classic layout stays unchanged. A project bot’s glance reads open work and dated completions through the existing signed-in person’s project API, groups the bot work statuses, and offers no mutations. A session draft (`cmmn.deck.draft`, `{bot,text}`) is consumed only into the named bot’s loaded composer, without sending, in either look; another bot’s draft survives and malformed input is discarded.

- **Deck Today home:** when Deck is on, `#/` lands on `#/today`; with it off, Today remains an unknown address and its module is never loaded. Today reads only the existing bot roster and waiting blocks, prefers a project bot for repeated questions, posts `{version, choice}` to the existing decide route, and reloads a changed question after 409. A successful answer stays settled until refresh; failures keep the question open. Refresh preserves the composer controls, and stopping aborts pending reads and answers and removes listeners. The composer stores only its explicit hand-off `{bot, text}` in session storage (`cmmn.deck.draft`) before opening that bot; the receiving thread is a separate slice. Working rows and project agent counts use reported data; no finished-work history is inferred.

- **Deck Compute:** `#/compute` reads only `/api/runners` and `/api/bots`. Revoked machines are omitted; disabled machines are Off, enabled owner-attested machines are Ready, and other enabled machines wait for attestation. These are enrollment states, not online status; attestation is the owner's statement, not verification. The map reserves label boxes outside its link lanes, widens within a scrollable field for crowded layers and keeps SVG type at real pixel size. Physical details, sites and running work have honest empty states. Add machine opens Admin; the two bot actions store a `cmmn.deck.draft` in session storage and open General without sending it.

- **Bots web screens (S5):** `#/bots` and `#/bots/<id>` (`web/static/bots.js`, `botstream.js`, `bots.css`) talk only to the `/api/bots*` contract and the existing `POST /api/cards/<n>/decide`; they hold no conversation text beyond the open page, store nothing in the browser except an unsent draft in memory, and never send a model or mode choice. Every server string is escaped before it is drawn. A turn is streamed with `fetch` and a stream reader; a connection lost after the `ack` event is not a failure, the page polls `GET /api/bots/<id>/messages?after=` until `running` is false. The roster is polled every 15 s and an open thread every 5 s (1.5 s while a turn runs), both paused while the tab is hidden. A failed send keeps the text and retries under the same `request_id`. Work stays "work", "task" and "question" in the screen's wording.

- **Bots-first navigation (S8):** `#/` resolves to `#/bots` for a person who has at least one project and to `#/start` for one who has none; no storage, API or permission changes. The rail and phone tabs list Bots and Memory; the Bots badge is the number of questions waiting on the person, read from the same `GET /api/bots` roster the bots screen loads (the shell reads it itself only on other screens, so the screen adds no second poll), and it counts the General bot's total when that bot exists because that total already covers every project. Projects, Needs me and Console moved into a "Workspace" group of the account menu, still gated by the same rules as before (Admin only for `can_admin`); no route was removed.
