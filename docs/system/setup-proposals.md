### Setup proposals approved by the owner

Card #675. An agent proposes one full workspace setup and the signed-in owner approves it in one tap, the
way a Coworker config is accepted by its sha256 (`orch_config_propose` / `orch_config_accept`). It is built
in three steps: the proposal store and its reads (slice 1), the owner's sha-bound apply (slice 2) and the
approval row in Waiting on you and Settings (slice 3).

#### Record

A proposal is one row of `setup_proposals` in the control database (`CREATE TABLE IF NOT EXISTS`, additive;
`commonplace/projects/setups.py`). Its `id` is a version number per workspace (`v1`, `v2`, ...), never
reused. It holds the canonical body, the sha256 of that body (column `digest`, field `sha256` in every read), `status` (`proposed`, `applying`,
`applied`, `partial` or `rejected`), the proposer and decider as actor refs (`user:<id>`, `conn:<name>`),
their times and, after apply, the per-item results.

The body has three optional parts:

- `bots`: up to 20 `{key, name, instructions}`. `key` is a short label (`[a-z0-9][a-z0-9_-]{0,31}`) that
  coworkers in the same proposal point at; the name and instructions follow the Coworker bot limits (1 to
  80 and 0 to 4,000 characters).
- `coworkers`: up to 40 `{key, board, bot, title}`. `board` is a project slug; `bot` is the key of a bot
  in the same setup; `title` is 0 to 120 characters.
- `models`: `{workspace: {role: choice}, projects: {board: {role: choice}}}` for the roles `main`,
  `worker`, `reviewer` and `gate`. A choice is `{provider, model}`, or `null` to clear that role's
  choice. Roles that are left out keep what they have.

Unknown keys, duplicate keys, a coworker pointing at a key that is not in `bots`, text that the redactor
recognises as a credential (in a name, instructions, a title or a model label), more than 50 projects and a body over 64 KiB are refused before anything is
stored. Proposing exactly the body of a proposal that is still `proposed` answers that proposal again
instead of a new version, and at most 20 proposals wait at once.

#### Canonical JSON and the sha256

The validated body is serialised with sorted object keys, no insignificant whitespace (`","` and `":"`
separators) and UTF-8 without escaping (`ensure_ascii=False`); list order is kept, because it is the
order items are created in. The `sha256` is the lowercase hex digest of those bytes. The stored body is
those exact bytes, so anyone can recompute the digest from a read. An apply names the `sha256` the owner
saw; a different digest, or a proposal that is no longer `proposed`, applies nothing.

#### Reads and the readable diff

`setup_propose` and `setup_show` (MCP), and `GET|POST /api/setup-proposals` and
`GET /api/setup-proposals/<id>` (REST), answer only the workspace owner: the signed-in owner, or a
connection of the owner. A member, a member's connection and a machine credential get the 404 of an
unknown route, and the tools are not listed for them. A connection also gets 404 for a proposal that
names a project its own policy cannot read; proposing such a project is refused like an unknown one.

Every read carries `changes`: one line per item in plain words, computed against the workspace as it is
now. A bot or Coworker is `create`. A model role is `set` (with the current choice and the new one),
`clear` or `unchanged`. A line that would fail today (a project that is gone or that the reader cannot read) says so in
`problem`. A model that is not yet in the project's list or the suggestions is marked `adds_to_list`:
apply adds it to that list first, the way the owner does in Agent models. `summary` is the first
three lines and a count. Nothing in a read holds a token, a key or a credential: the body has none
(refused on propose) and model choices are labels.

#### Apply

Only the signed-in owner (a web session, never an agent token and never a token with role owner) applies a
proposal, by posting the `sha256` shown to `POST /api/setup-proposals/<id>/apply`. The owner's own agent
gets 403 `owner_only`; anyone else gets the 404 of an unknown route. The server first moves the row from `proposed` to `applying` in one
statement that also checks the digest, so one proposal is applied at most once. It then runs each item as
that owner through the existing service paths, in body order:

- bots: the Coworker bot store (`create_bot`, the path of `POST /api/coworkers/bots`), always with no
  connection;
- Coworkers: a project-bound conversation of the owner with that bot (`create_conversation` with
  `kind=dm`, the path of `POST /api/coworkers/conversations`), whose board check is the owner's own;
- models: `models.catalog_add` for a model that is not on the list yet, then `models.put` per project
  and `models.put_workspace` for the default (the paths of `POST|PUT /api/projects/<board>/models` and
  `POST|PUT /api/settings/models`).

The items live in different stores (the workspace's Coworker database and the control database), so one
transaction cannot cover them. Each item therefore has its own result: `{ok: true, id}` or
`{ok: false, error}`. A Coworker whose bot failed is skipped with `bot_failed`. The proposal ends
`applied` when every item succeeded and `partial` otherwise; it is never retried in place, and the rest can
be proposed again. Every item result is written as soon as it is known, so an interrupted apply shows what
was created. Rejecting (`POST /api/setup-proposals/<id>/reject` with the same `{sha256}`) moves `proposed` to `rejected` and changes nothing else; a proposal that is ignored
stays `proposed` and changes nothing.

#### Read-back and audit

After apply the proposal reads back `status`, `decided_by`, `decided_at` and `results` with the created
ids (bot ids, conversation ids, and per role the model as `model_mode` reports it). The audit log gets
`setup.propose` (proposer), `setup.apply` and `setup.reject` (the owner), each with the proposal id and
digest, next to the audit rows the services already write (`project.models`, `models.workspace`,
`coworker.create`). Events carry refs only: id, version and digest.

#### Why apply cannot widen anyone's access

- Apply runs only for the signed-in owner and only as that owner: every item calls a service that already
  requires the owner (bots, model settings) or checks the owner's own board access (Coworkers). Nothing the
  owner could not click together by hand can happen.
- The applying principal is the owner's web session. The proposer's connection is only recorded; it is
  never the principal of a write and gains no new right.
- No item touches connections, scopes, consent or accounts. A bot is created with `connection_id`
  unset, so no connection is linked to it and no `bots:*` scope or chat consent is granted. Model choices
  name a provider and model only; no key, token or account link is stored.
- The digest binds the approval to the exact bytes shown. A changed proposal is a new version with a new
  digest, and a stale tap applies nothing.
- An agent can only create a `proposed` row. Without the owner's tap that row changes nothing, and it is
  limited in size and number.

Bots of `POST /api/bots` (each person's own bots, one per project is made automatically) are not part of a
setup: anyone makes those for themselves. Coworker configs (`orch_configs`) keep their own proposal flow.

#### Approval in Waiting on you (slice 3)

`GET /api/needs-me` carries `setup_proposals` for the signed-in owner only: one item per waiting
proposal, newest first and at most 20, with `kind` `setup`, `id`, `ref`, `sha256`, the proposer,
`summary`, the plain-words `changes` and the number of `problems`. It is counted in `count`, agent
tokens get no such list, and members cannot read the owner queue. Today shows each item ahead of config
proposals and cards, with the same markup as a config proposal: the proposer, the one-line summary, the
number of changes, a Changes list (`+` creates, `~` sets or clears, `=` stays), Approve and Reject.
Approve posts the item's own `sha256` to the apply route; Reject asks once and posts it to the reject
route. A refused or stale tap keeps the item, says why and re-reads the queue. The confirmation says
that everything under Changes is in place, or how many parts could not be done.
