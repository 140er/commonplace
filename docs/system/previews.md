### Persistent app previews: a link a Coworker publishes that outlives its work session (design note, card #612)

This note belongs to the Compute and Deck sections. It is a design and slice plan; nothing described here is built yet, and every "will" below is a plan, not a fact about the running app. Today a person can only look at what a Coworker built while that Coworker's session is alive. The goal is that a Coworker publishes a preview of what it built for a card or pull request, and a person reopens it from that card in one click, in plain words, after the session has ended, without a terminal.

#### What exists today (gap analysis, develop at the start of this note)

- **Compute stores and status vocabulary (reusable).** `commonplace/compute/` keeps one `compute.sqlite3` per workspace with owner and member checks (`Store._owner`, `Store._member`), append-only audit and fixed reason codes. The Overview read envelope and its status shape (`status{code,tone,label,detail,next}`, tones `ok|wait|quiet|bad`, "last seen" ages, `FAILURE_TEXT` reasons in `compute/overview.py`) already say why something is in a state. A preview reuses that shape instead of inventing a second one.
- **Compute cannot serve anything over HTTP.** There is no container runtime, no port, proxy or tunnel concept, and the machine-side access policy forbids forwarding (`AllowTcpForwarding no`, `PermitTunnel no` in `compute/target_installer.py`). Cloud workspace start and stop only record a request that nothing claims in production (`compute/workspace_lifecycle.py`). Workspace CPU, memory and storage limits are stored but not enforced. A running-app preview on a Compute machine therefore needs the later Compute start slice first.
- **Compute keeps no artifact bytes.** Script runs keep stdout and stderr (4 KiB) and artifact metadata (path, size, digest), and drop the bytes (`compute/scripts.py`). Nothing is retained long term and nothing garbage-collects; there is no retention rule to inherit.
- **No preview concept.** The words "preview" in the code are unrelated: an effect preview of an owner action in `compute/overview.py`, an import preview and a change preview. The app's own static files under `/app/` (`commonplace/web/__init__.py`) are the app bundle, not a user-published preview.
- **Gateway (reusable).** Routes are added with `Gateway.add_route` and `add_prefix_route`; the session cookie `cmmn_session` or a bearer token maps to a person and workspace, and membership is re-read on every request (`Gateway.principal_for`, `sessions.person_principal`). Every registered route is authenticated unless listed in `_public_route`, so a `/p/...` route is members-only by default. POST, PATCH and DELETE under `/api/` already require exactly one `Content-Type: application/json` and a same-origin or absent `Origin`. The request body cap is 8 MiB (`MAX_REQUEST_BYTES`). A static file helper (`web/__init__.py`, `_static`) already rejects backslashes, NUL bytes and dot segments and checks the resolved path stays inside its root. New routes must be added to the pinned route inventory in `tests/test_commonplace_public_surface.py`.
- **Card visibility (reusable).** `BoardService.task_show` uses `require_visible(row, principal)`, so "can open the preview" can be defined as "can open the card". Missing and other-workspace ids already return one uniform 404 in places such as `web/task_types.py`.
- **Cards and pull requests (reusable).** Cards carry `repo`, `branch`, `pr_url`, `pr_number`, `pr_repo`, `pr_state` (`open|merged|closed`) and `pr_head`, written by Coworkers through the board tools. `board_repos` maps a repository to a board, so a pull request resolves to a board. There is no inbound GitHub webhook; the card's own `pr_state` report is the only merged/closed signal.
- **MCP tools (reusable).** `LocalTool` in `commonplace/extensions.py` registers a tool with a `scope`, `kind` (read or write), `profiles` (`WORK_PROFILES` is standard and full), `owner_only` and `available`; one visibility gate hides tools the caller may not use. There is no profile called Coworker: Coworker differences come from `available=` and scopes. A new scope must be added to `policy.SCOPES`.
- **Deck (reusable).** The card detail view (`web/static/details.js`) already mounts per-card sections that fetch on mount and render their own states (`cardruns.js`, `cardlinks.js`), and the Needs me pull request panel (`needs.js`) shows the PR link and merge state. The Deck API client maps refusals to `ApiError` with a payload that carries the reason.
- **Secrets helpers (reusable).** `commonplace/redact.py` (`redact`, `redact_value`) finds secret-looking text and secret-named keys.

#### What is missing

1. A preview entity with a store, a lifecycle and a status vocabulary with reasons.
2. A way to hand a built bundle to the hub, store it durably and serve it to members, with strict content isolation.
3. A retention rule the owner can see, with no automatic deletion.
4. A Coworker API and MCP tool, and the Deck section per card and pull request.
5. For running apps: a container runtime and an HTTP path to a Compute machine. This is out of scope for slice 1 and depends on the Compute start slice.

#### What a preview is

A preview is a named, versioned, read-only snapshot of something a Coworker built, linked to one card (and its pull request when the card has one). It has two kinds:

- **`static`**: a built bundle of files with an `index.html` (a web app build, a rendered report, screenshots as a page). This is slice 1.
- **`app`**: a running app container on a Compute machine. This is designed for here so the record and states do not change later, but it is not built until Compute can start workspaces and expose a port. A preview record already carries `placement` so both kinds share one list.

Fields (all returned by the API; none can hold a secret): `id`, `card` (task number), `pr` (URL and head SHA at publish time, when known), `name`, `kind`, `revision`, `state`, `reason`, `placement`, `size_bytes`, `file_count`, `published_by` (Coworker or person display name), `published_at`, `stops_at` (or null) with the rule that produced it, `url` (relative, for example `/p/pv_ab12.../`), and `last_changed_at`.

Every revision is immutable. Publishing again under the same `name` on the same card creates revision N+1 and moves the stable URL to it. Earlier revisions are kept and listed ("revision 2 of 3"); they count against the workspace quota and are removed only by a person (see Lifetime).

#### Where it is hosted: reusing Compute, honestly

The card asks to reuse Compute and not invent a new host kind. Compute today is a control plane (machines, runners, cloud workspace records, scripts, executions) with no way to serve HTTP, so the plan has two placements behind one record:

- **Slice 1, `placement: hub`.** The static bundle is stored in the workspace's own data directory next to `compute.sqlite3` (a new `previews.sqlite3` plus a content directory, workspace scoped, the same tenant rule and owner/member checks as the Compute store) and served by the hub gateway. The hub is not a new kind of machine and nothing new has to be enrolled; it is the place that already holds the workspace's data and sessions. This is the only way a preview survives the end of a session today without a Compute executor.
- **Later, `placement: workspace`.** An `app` preview runs in a Compute cloud workspace (the record from card #614). It will be served by the same gateway route, which proxies to the machine over the existing pinned SSH transport (a new, owner-approved operation in the compiled operation list, not a general tunnel). It starts when the Compute start slice lands.

The Overview will list previews as a read-only family beside machines, runners and workspaces (the existing status envelope), so the owner sees count, bytes used and the active rule in Compute as well as on the card.

#### URL shape and who can open it

- Preview URL: `/p/<preview_id>/` on the app's own origin, with files below it (`/p/<preview_id>/assets/app.js`). The id is `pv_` plus 16 random hex digits. It is a name, not a secret: access control does not rely on the id being unguessable.
- **Who can open it:** a signed-in person in the same workspace who can open the card the preview belongs to. The session is checked when they open the preview; the page then loads under a short-lived key in its address (see "Review round 1" below): anyone who has that exact address can open the preview until the key ends, 30 minutes later, so passing the address on is like sharing the preview. Revoking a membership or hiding the card ends access at once, also for a key.
- **Never public.** Slice 1 has no public switch. Making a preview public would be an owner-only, per-preview setting needing the project owner's decision first; it is listed under open questions and is not built.
- **Uniform refusal.** An unknown id, another workspace's id and a card the caller cannot see all return the same 404 with the same body. A caller with no session gets the gateway's normal 401; the serving route answers a browser navigation with a small "Sign in to open this preview" page instead of bare JSON.
- **Isolation from the app (the main risk).** Preview files are agent-built and run in the browser on the same origin as the app, so they must never act as the signed-in person. The serving route therefore sends `Content-Security-Policy: sandbox allow-scripts allow-forms allow-popups; default-src 'self' data: blob:; connect-src 'none'; frame-ancestors 'self'`, plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: private, no-store` (no resource or opener policy: see Slice 2 as built). The `sandbox` directive without `allow-same-origin` gives the page an opaque origin: it cannot read app cookies or storage, and its requests do not carry the Lax session cookie and fail the gateway's same-origin `Origin` rule. Scripts, styles, images and fonts load from the preview itself; a preview that needs to call a server API is out of scope for `static`. Slice 2 tests this with a hostile fixture bundle. A dedicated preview origin on a separate registrable domain, with a short-lived signed link that sets a preview-only cookie, is the stronger option for `app` previews and is an open question.
- Serving reuses the `_static` path discipline (reject backslash, NUL and dot segments, resolved-path containment, no symlinks), serves from a per-revision directory the server wrote itself, maps content types from a fixed extension table (unknown extensions are `application/octet-stream` with `Content-Disposition: attachment`), and never lists directories.

#### States, each with a reason

States are the four the card names. `reason` is a fixed code with fixed plain-language text (the Deck shows the text, the API returns both) and a `next` sentence that says what the person can do.

- **`building`**: the hub is validating and storing a bundle (or, later, a runner is building an app). Reasons: `validating`, `storing`; later `building_on_machine`, `waiting_for_machine`. A record left in `building` for more than 15 minutes becomes `failed` with `publish_interrupted`, so a crash never leaves an empty panel.
- **`ready`**: reachable by members. Reasons: `published`; later `running`. A `ready` preview carries an `availability` field: `reachable` or, for `app` previews, `machine_offline` or `machine_unknown` with the last time it was seen working. The Deck then says "The machine that runs this preview is offline; last working 3 h ago" and still offers Stop.
- **`stopped`**: not served, files kept. Reasons: `stopped_by_person`, `stopped_by_coworker`, `pr_merged`, `pr_closed`, `expired` (the visible rule below). Publishing a new revision is not a stop: it just becomes the current one.
- **`failed`**: Reasons: `no_index_html`, `too_large`, `too_many_files`, `bad_path`, `contains_secret_like_text` (names the file and kind, never the value), `workspace_quota_full`, `storage_unavailable`, `publish_interrupted`; later `machine_unreachable`, `out_of_memory`, `build_failed`. Failed publishes keep no served files and use no quota.

Requests to a preview that is not `ready` get a small plain HTML page with the same reason text (HTTP 410 for stopped, 503 for failed or offline), never a blank panel or a bare error. A request for a path inside a ready preview that does not exist gets the preview's own 404, not the app's.

#### Lifetime and cleanup (owner-visible; nothing deleted automatically)

- **Rule shown to the owner.** Every card's Previews section and the workspace Previews settings say the rule in a sentence: for example "Kept until you stop it. A preview can also stop serving 7 days after its pull request is merged or closed, if the owner turns that on." The rule is stored per workspace (`preview_rules`) and every preview shows its own `stops_at` computed from it.
- **Default in slice 1: serve until a person or the Coworker stops it.** No timer is on by default. The owner can enable "stop serving N days after the pull request is merged or closed" (and an optional hard maximum age). Expiry is evaluated lazily when a preview is read or requested (it compares `stops_at` with now), so there is no background deletion job, and the card's `pr_state` self-report is the trigger because no inbound GitHub webhook exists.
- **Stopping never deletes.** A stopped or expired preview keeps its files. The card shows "Open again" for a person with access.
- **Deleting files is a separate, person-only action** (built with card #622, see "Deleting files" below) ("Delete these files", with a confirmation that names the size and the card). It is never done by a Coworker, never by a timer and never by quota pressure. An optional automatic purge of stopped previews is designed as a workspace setting that ships off and may not be switched on until the project owner has approved automatic data deletion; slice 1 does not include the setting.
- **Audit.** Publish, stop, open again and rule changes write a card history line and an append-only record (who, when, reason).

#### Resource limits

Fixed in code with the numbers shown in the API and in the Deck error text; a limit failure is `failed` with its reason, never a silent truncation or an eviction of other previews.

- Per bundle: 5 MiB of file bytes before encoding (the JSON request cap is 8 MiB and base64 adds a third), at most 500 files, paths at most 255 bytes and 10 levels deep, `index.html` required.
- Per workspace: at most 25 `ready` previews and 500 MiB stored across all revisions of everything not deleted. When full, publish is refused with `workspace_quota_full` and the text says what can be done (stopping frees an open place; freeing space needs deleting, a later slice).
- Rate: a Coworker may publish at most 10 revisions per card per hour.
- Serving: responses stream with the existing request timeouts; no preview code ever runs on the server.
- Larger bundles (chunked upload from a runner machine) and `app` limits (CPU, memory, storage, idle time) are later slices, and enforcement of Compute workspace limits is a prerequisite, not part of this note.

#### API and MCP tool

REST (members; writes use the shared JSON Content-Type and same-origin boundary; errors are `{"error","reason"}`; lookups use uniform refusal):

- `GET /api/previews?card=<n>` and `GET /api/previews?pr=<url>`: previews the caller may see, newest first, with state and reason.
- `GET /api/previews/<id>`: one preview with its revisions.
- `POST /api/previews`: publish. Body `{card, name, files: [{path, text | base64}]}` (static). Returns 201 with the record (`state: ready`) or 4xx/422 with the failure reason. Idempotent on `(card, name, content digest)`: re-sending the same bundle returns the existing revision.
- `POST /api/previews/<id>/stop`, `POST /api/previews/<id>/open-again`: person or the publishing Coworker.
- `DELETE /api/previews/<id>` (files): person with owner role only, body must repeat the id as confirmation (card #622).
- `GET`/`PUT /api/previews/rules`: owner reads and sets the stop rule.

MCP tools for a Coworker, in the standard and full profiles, behind a new `previews:write` scope (writes) and `previews:read`: `preview_publish` (card, name, files), `preview_list` (card or pull request), `preview_stop`. Descriptions use "Coworker" and plain words; the tool text says that a preview is visible to workspace members who can open the card and is never public. The tools never accept or return environment values, and a Coworker cannot delete files or change the rule. `preview_publish` also writes the card history line "Coworker published preview <name> (revision N)" so the owner sees every publish.

Secret safety: file text goes through the same secret-shape check as `redact` before it is stored; a hit refuses the publish with the file path and secret kind only. Previews have no environment or configuration of their own in slice 1, so there is nothing to leak from the host; API and MCP responses never include filesystem paths, workspace directory names or host details.

#### Deck: per card and pull request

A "Previews" section in the card details (after the runs section), and a one-line "Preview ready" link in the Needs me pull request panel. Each row: name, revision, state chip in words, the reason sentence, when published and by whom, the stop rule date, and actions: **Open** (new tab, primary), **Stop**, **Open again** (stopped only), and for the owner **Delete files** with confirmation. States: loading, none yet ("No preview yet. When a Coworker publishes one it appears here."), ready, building (shows "Publishing…"), stopped (with reason and Open again), failed (reason and next step), offline (banner with last working time), refused (card not visible: the same screen as a missing card). On a phone (390 px) rows stack, actions are full-width with 44 px touch targets, and Open opens the preview in the same tab with the Deck's back control. Copy says "Coworker", never the internal role name. The workspace Previews settings (owner) shows count, bytes used of the quota, and the stop rule with its switch.

#### Honest scope for slice 1

Slice 1 delivers **static bundles hosted by the hub**, published by a Coworker through the API and MCP tool, listed, opened, stopped and reopened from the card, persistent across sessions and restarts, members only, with every state explained. It does not deliver running app containers, a preview on a Compute machine, public links, a separate preview origin, chunked uploads, automatic deletion, or a GitHub webhook. The record carries `kind` and `placement` so those can follow without changing the API.

#### Slice plan and acceptance

Each slice is its own pull request against develop under 300000 bytes of diff, with tests that fail on the base and pass on the head (no timing sleeps in tests, no full suite) and the docs fragment obligations.

1. **PR 1: server, store and API, plus the MCP tools.** `commonplace/previews/` store (`previews.sqlite3`, immutable revisions, content directory, quota and rate checks, bundle validation, state machine with fixed reasons, rules table, append-only audit), `/api/previews` routes, `previews:read` and `previews:write` scopes, `preview_publish`, `preview_list`, `preview_stop` tools, card history line. Acceptance: publish of a valid bundle returns `ready` and survives a store reopen; every failure reason in the list is produced by a test; a non-member, another workspace and an invisible card all get the same 404; stop keeps files and open-again restores; no response contains a path, environment value or secret text; `building` older than 15 minutes reads as `failed: publish_interrupted`; the route inventory and tool-list pins are updated; a Coworker cannot delete or change rules.
2. **PR 2: hosting and serving with access control.** `/p/<id>/...` gateway route over the stored revision, the isolation headers, content-type table, path discipline, plain state pages (410, 503, 404), lazy expiry, sign-in page for browser navigation. Acceptance: files served only to a signed-in member who can open the card; anonymous, non-member and hidden-card requests are refused uniformly; traversal, symlink, encoded dot-segment and oversized-path attempts never read outside the revision; a hostile fixture bundle cannot read app cookies or call `/api/` as the viewer (test asserts the sandbox header and that an `Origin: null` write is refused); stopped and expired previews show their reason page; a preview published in one process is reachable after the gateway restarts.
3. **PR 3: Deck UI.** The card Previews section, the Needs me link, the owner Previews settings and the rule switch, with phone layout. Acceptance: node-harness tests for every state and action; desktop (1280x800) and phone (390x844) screenshots of ready, none, building, stopped, failed, offline and refused; keyboard operable with visible focus; one click from the card opens a preview that was published in an earlier session; copy test passes (Coworker, plain words, every state has a reason and a next step).

A later slice (separate card, after the Compute start slice) adds `app` previews on a Compute cloud workspace.

#### Decisions (accepted by the project owner, 19 Nov 2026)

1. **Hub-hosted static first.** Slice 1 hosts static bundles on the hub; running apps follow the Compute start slice.
2. **Same origin with the sandbox.** Slice 1 serves previews on the app's own origin under the sandbox policy above; previews that need to call server APIs are out of scope for `static`. A separate preview origin is not built.
3. **Serve until stopped; the stop rule is off.** No timer is on by default. The owner may switch the rule on later; stopping never deletes.
4. **Quota: 25 ready previews, 500 MiB per workspace, 5 MiB per bundle.**
5. **No public links.** Nothing in this goal makes a preview public.
6. **No automatic purge.** Files are deleted only by an owner's explicit action.

#### Slice 1 as built (differences from the plan above)

- Rows live in the control database (tables `previews`, `preview_revisions`, `preview_events`, `preview_rules`, keyed by workspace) and the files under `<workspace dir>/previews/<id>/r<n>/`, not in a separate `previews.sqlite3`: the control database is where per-workspace feature tables (such as the scheduled prompts) already live and it is part of the existing backup. The files are plain files; workspace-level SQLite encryption does not cover them.
- The tools use the existing `memory:read` and `memory:write` scopes and the standard and full profiles, like the card tools, instead of new `previews:*` scopes that no existing token would hold. The write tools are hidden from untrusted tokens, and a token that cannot write the card's wing cannot publish for it.
- The people routes refuse agent tokens (403); Coworkers use the tools. Every preview belongs to a card (publishing needs one), so the "pull request with no card" case does not exist.
- A rate limit of 10 publish attempts per card per hour counts refused bundles too (a malformed request uses none). A workspace keeps at most 200 previews; refused attempts that never published anything do not count, and only the newest 50 of them are kept (they hold no files; the append-only events keep the history). At the cap a publish is refused with its own reason, `too_many_previews`, and the text says plainly that deleting previews is not available yet.
- Deleting preview files (the person-only action and its `DELETE` route) was not in slice 1; card #622 added it (see "Deleting files"). Publishing renames a leftover directory aside under the lock and removes it after the lock is released; no recursive file work runs while the control-database lock is held.
- Reasons added to the list: `rate_limited` (not stored), `too_many_previews` and `changed_meanwhile` (a person stopped the preview while a publish was writing its files; the publish is refused and changes nothing).
- The stop rule can be read and set (owner) and is shown, but is not applied until the serving slice; `stops_at` is null until then.
- The URL `/p/<id>/` is returned but nothing answers it until slice 2.
- Account deletion: the preview rows and files go with a deleted workspace; the append-only `preview_events` stay (ids, actor labels and reason codes, no content), like the audit log. An account export carries the preview rows of an owned workspace but not the published files.

#### Slice 2 as built (serving)

- `GET /p/<id>/<path>` is public to the gateway only so that a signed-out browser gets a sign-in page in words (401) instead of a JSON error; the route checks the session itself. Agent tokens get a 403 page: previews are for people, Coworkers use the tools. An unknown id, another workspace's id and a card the caller cannot see get one identical 404 page.
- **Why there is a link token.** Under the sandbox the page has an opaque origin, so the requests it makes for its own stylesheets, scripts and images are cross-site and carry no session cookie (a real-browser test showed them refused). A signed-in visit to `/p/<id>/<path>` is therefore sent on (303) to `/p/<id>/~<token>/<path>`, and the page's relative links load under the token. The token is random, kept only as a digest (`preview_links`), bound to one preview, one person and one workspace, and lasts 8 hours; every request with it checks again that the person is still a member who can open the card, and that the preview is ready. Another signed-in person's browser never rides on someone else's token: it is sent back to the plain address and checked as itself. A preview could read its own address and send it elsewhere through a popup; that gives the holder that one preview's files for at most 8 hours, which its author already has. Expired tokens are removed when the next one is made, and a person's tokens go with their account.
- Bundles must use relative links (most build tools have a relative base setting); an absolute `/assets/...` link points at the app, not the preview.
- Headers on every file: the sandbox policy above (`sandbox allow-scripts allow-forms allow-popups; default-src 'self' data: blob:; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'`), `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: private, no-store`. There is no `Cross-Origin-Resource-Policy` (a same-origin one blocks the sandboxed page's own files) and no opener policy (browsers refuse a sandboxed page that sets one). The real-browser test confirms that `'self'` lets the page load its own stylesheet and script, while `document.cookie` and storage throw, its origin is `null` and `fetch` to `/api/` is blocked.
- Content types come from a fixed table; anything else is sent as `application/octet-stream` with `Content-Disposition: attachment`. Paths are decoded once and held to the published-path rules; links and anything resolving outside the revision folder are never read; a folder address serves its `index.html`; `/p/<id>` without a slash is redirected (308) so relative links resolve.
- State pages: 410 for stopped (with the reason, such as the stop rule), 503 for failed and for building (with `Retry-After`), 503 when the files are missing on the server, 404 for an address not in the preview. They are fixed text with a script-free policy and quote nothing from the request.
- The stop rule is applied when a preview is read or requested: a ready preview whose time has passed is stopped once (reason `expired`, `pr_merged` or `pr_closed`, event actor `rule`), its files kept. Time counts from when it last became ready, so Open again gives it the full time again; the pull request part counts from the card's last merged or closed report.
- Signing in does not return to the preview by itself (sign-in only returns to app pages); the page says to open the link again.

#### Slice 3 as built (the card)

- The card panel has a **Previews** section right after Runs, in the Deck and the classic look. Each preview shows its state in words (Ready, Being published, Stopped, Could not be published), the server's reason and next step, the revision, when and by whom it was published, and, when the stop rule applies, the day it stops being served. Actions: **Open** (a new tab, one click), **Stop** (ready), **Open again** (stopped). A refusal shows the server's reason and next step, and the list is reloaded so it shows what is true now.
- No preview yet says so in words. If the server cannot be reached the section says so with Try again, or keeps the last list and says it could not refresh. A card the person cannot see shows no Previews section, like a missing card.
- Below the list: the workspace's usage (open previews and space used of the limits) and the stop rule in a sentence. The workspace owner can change the rule there (`can_set_rule` in the list answer; the server checks again). There is no separate Settings page for previews and no link in Needs me yet: the card is one click from a Needs me item. Both are follow-ups.
- On a phone the actions are full width with 44 px targets and nothing scrolls sideways.

#### Review round 1 (server and serving)

- **The page key (decision).** The project owner's acceptance said only signed-in people open a preview. The page's own files load without the session (the page has no origin of its own), so the address carries a key. Two ways were weighed. A cookie bound to the browser that opened the preview would have to be sent on cross-site requests from an opaque origin (`SameSite=None; Secure`, partitioned or not); browsers that block third-party cookies refuse exactly that, so previews would break for many people. The key in the address is therefore kept, made short and said plainly: it lasts **30 minutes**, a new one is made on every signed-in visit, a person keeps at most three per preview (older ones end at once), and a key is made only for an address that names a file. Every request with a key checks again that the person is a member who can open the card and that the preview is served. The tool text and this note say that the address can be passed on within that time. The real-browser test shows a browser with no session opening the address and getting the sign-in page once the 30 minutes are over.
- **Preview pages post nothing**: `form-action 'none'` is part of the file policy (the browser test shows a form in a preview sends no request).
- **Words on the card are for people.** A stored preview shows the reason and the next step for the person reading the card (`PERSON_WORDS`: "Ask the Coworker that published it to publish again ... Reply on this card."), while a refused publish still tells the Coworker what to fix. A refusal about one file keeps that file's name and, for a credential, its kind (never the value) and shows them (`failure`). A preview that was never published has a start time (`started_at`) instead of a publish time.
- **Served pages lead somewhere.** The sign-in page links to sign-in, the not-found page to Commonplace, and the stopped, failed, being-published, not-in-preview and cannot-be-read pages to the card (`/app/#/card/<n>`) and the start page. Plain links work in these pages and in a preview's own pages without allowing top-level navigation (the browser test clicks them). A file that cannot be read says so in read words.
- **A Coworker cannot undo a person.** Publishing to a preview a person stopped is refused (`person_stopped`); a person uses Open again. Publishing the same files as the kept revision serves it again without a copy. Stop, Open again and refused publishes write a line on the card; the line names no address.
- A preview name that looks like a credential is refused; a first path part starting with `~` is refused (it would read as a key); a linked folder on the way to a file is never followed.
- Not changed here (listed for later): the rules `PUT` is outside the session write check that `POST` gets (own item), stale `.tmp-*` and `.stale-*` folders are not swept yet, and stopped previews keep counting toward the 500 MiB until files can be deleted (card #622).
- The browser isolation tests run only where Playwright is installed (they are skipped otherwise, and `CP_BROWSER_E2E=1` makes a missing Playwright fail); this round's evidence ran them in Chromium.
- Open on a phone opens a new tab, as on a desktop.

#### Review round 1 (the card)

- **Times.** A preview that was never published says when it started ("started 2 min ago by ..."), never "published"; the publisher is named as on the card header.
- **What a person reads.** The card shows the server's words for people (see "Review round 1 (server and serving)"), the file and the kind of credential when a refusal named one ("File: config/env.js, which looked like a GitHub token."), and no "Try again" where there is no button.
- **Focus and announcements.** The list is drawn into its own element and results are said in one status line that stays in place (`role=status`), so screen readers hear them. After Stop, focus moves to Open again; after Open again, to Open; after saving the rule, to its summary; after a refusal, to the heading. A redraw (the card panel's own, or the section following a publish) keeps focus on the same control; the shell's focus restore knows the section's keys too. A repainted list never repeats an old result.
- **Offline.** "Showing the last list" is said only when a list is shown; a section that had no list shows the not-loaded view. A connection failure says "Check the connection and try again."; a server error says "Try again."; both have a real Try again button.
- **While publishing** the section asks again every 3 seconds until nothing is being published.
- **Touch and small screens.** On touch screens and below 1000 px every control in the section is at least 44 px, rule inputs use 16 px text, long names wrap at 320 px. Open again's name starts with its visible words; after Open again the status says "Press Open to view it."; the result note follows the state the server returned.
- **Copy.** The rule says "after the card records the pull request as merged or closed" and "after it was last made live"; the usage says stopped previews still count toward the space.
- Not done here (follow-ups): a Previews page in Settings and a link in Needs me; the panel-wide scroll position after a redraw.

#### Deleting files (card #622)

The order is chosen so that the control-database lock is never held while files are removed, and so that a publish racing the delete cannot leave a ready preview without its files:

1. Outside the lock: the caller must be a signed-in workspace owner who can open the card, and the body must repeat the preview's id.
2. Under the lock, in one transaction: a preview still being published is refused (`validating`, 409). Otherwise every revision is marked deleted, the preview becomes stopped with the reason `files_deleted`, an event is recorded, and the preview's folder is **renamed aside** (`previews/.deleting-<id>-<random>`). A rename is cheap and atomic; if the disk refuses it the transaction rolls back and nothing changed (503 `storage_unavailable`). The rename is not part of the SQLite transaction, so if anything fails after it and before the commit lands (a refused commit on a full disk included), the folder is renamed back, still under the lock, and the preview is exactly as it was. If that rename back fails too, the preview must not read ready over a folder that is no longer there: a short transaction records the delete instead, so the preview reads deleted with `files_remaining`, and **Finish deleting** removes the folder.
3. With the lock released: the renamed folder is removed, however long that takes. No error of the removal escapes: if anything is left (or the folder cannot be looked at), a short transaction records `delete_incomplete` and the answer says `files_remaining`, never a server error; deleting again removes what is left (leftovers of the same preview are found by their name).

A publish that was writing its files when the delete happened finds, in its own final transaction, that its preview is no longer ready (or building) and refuses itself with `changed_meanwhile`; its half-written files went aside with the folder or are removed by the publish. A later publish under the same name starts a new revision in a new folder. Deleted revisions do not count toward the space limit and a deleted preview does not count toward the 200-preview cap; Open again is refused with `files_deleted`. The card shows **Delete files** to the owner only, asks for confirmation naming the preview, its size and that it cannot be undone, and says when files were left behind. A deleted preview whose removal left files is listed with `files_remaining: true` (one look at the folder per request, only when a deleted preview is listed, with no lock held), and the card then offers **Finish deleting**.

