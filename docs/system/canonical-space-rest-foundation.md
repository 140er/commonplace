### Canonical Space REST foundation

`GET /api/spaces` lists `{spaces:[Space]}`, `POST /api/spaces` creates a `Space`
with status 201, and `GET /api/spaces/<slug>` reads metadata. A Space contains
`slug`, `name`, `wing`, `coordinator`, top-level status `counts`, `can_configure`,
`open_url`, `version:1` and `deleted:false`. `open_url` points to
`/app/#/board/<encoded slug>`; only a signed-in workspace owner person has
`can_configure:true`. Legacy board responses remain supported.

The existing board row is the Space. Board, Memory and Chat are views of that
Space, with Board as the default view. Identity is the authenticated workspace and
exact slug; no new identity object or identity migration is needed.
List retains claim expiry; metadata is a plain active-row read without expiry,
transactions, orchestration, seats, settings or credential creation.
`POST /api/spaces` creates through `board_add`, preserving board authorization,
the transaction and the `space_created` event with `data.board`.
Non-person callers must pass the existing board tool visibility gate. Checked
create accepts freshness only through `Commonplace-Rules-Seq`; refresh failures
retain status 428 and their original payload. Optional null name/wing/repo use
slug/shared/no-repo defaults; distinct slugs may share a name.

Canonical validation is 400 `invalid_arguments`; policy or seat refusal is 403
`forbidden`; missing, hidden or archived metadata is 404 `space_not_found`.
Readable slug/repo collisions are 409 `space_exists` / `repo_assigned`, including
archived reservations. Hidden slug collisions are concealed as 404 and hidden repo
owners produce 403 without identity details. These different outcomes do not
eliminate existence oracles. Unknown subpaths are 404 `not_found`; collection
PATCH/DELETE and item POST/PATCH/DELETE are 405 `method_not_allowed` with
`Allow: GET, POST` or `Allow: GET`. Unregistered methods retain gateway errors.
An Idempotency-Key or include_deleted query is rejected with 400
`unsupported_idempotency` or `unsupported_include_deleted`.

#### Space cards and chat

`GET`/`POST /api/spaces/<slug>/cards` and `GET`/`POST /api/spaces/<slug>/chat` are
adapters over the board card and chat operations of one exact Space. They run the
same service calls as `/api/boards/<slug>/cards` and `/chat`, so card payloads,
authority, verbatim chat and rules freshness are unchanged. Differences are the
canonical vocabulary and identity: the slug must match an active, readable Space
exactly (a repo reference or a sole visible board is not accepted; otherwise 404
`space_not_found`); chat reads name the Space as `space` instead of `board`; a body
`space` or `board` must equal the path slug; a body `rules_seq`, an unknown field or
invalid JSON is 400 `invalid_arguments`; freshness comes only from
`Commonplace-Rules-Seq`. Non-person callers must pass the `task_list`/`task_add`
or `space_read`/`space_post` tool gate. Other methods are 405 with `Allow: GET, POST`
and other subpaths are 404 `not_found`.

#### MCP names

`space_list`, `space_add`, `space_set` and `space_own_wing` run the `board_list`,
`board_add`, `board_set` and `board_own_wing` operations with a `space` argument; the
board names remain. `space_read` and `space_post` accept `space` or the legacy
`board`; naming two different spaces, or none, is refused. A connection that lists
either name of a pair is granted both, so an alias never widens authority. Scope,
trust, owner and profile rules, rules freshness and legacy output are unchanged.

Rename, archive, versions/retries/tombstones, settings and orchestrator adapters,
the `space` argument on card tools, UI wording, new-user migration and desktop
synchronization are deferred.
