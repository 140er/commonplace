# Spaces

A **space** is the one object a person works in: its board, its memory and its chats.
Board, Memory and Chat are views of the same space, and the board is the default view.
Under the hood a space is the existing board row, so identifiers, links and legacy
routes keep working. A space is identified by its workspace and its exact slug.

## REST

| Method and path | Does |
|---|---|
| `GET /api/spaces` | List spaces: `{spaces:[Space]}` |
| `POST /api/spaces` | Create a space (201) |
| `GET /api/spaces/<slug>` | Read one space |
| `GET /api/spaces/<slug>/cards` | The space's cards, linked cards and child cards |
| `POST /api/spaces/<slug>/cards` | Add a card (201) |
| `GET /api/spaces/<slug>/chat` | Read verbatim chat; `?last=`, `?before=` or `?after=` |
| `POST /api/spaces/<slug>/chat` | Append a verbatim message (201) |

The card and chat routes behave exactly like `/api/boards/<slug>/cards` and
`/api/boards/<slug>/chat`, with three differences: the slug must name an active space
you can read (a repo reference is not accepted), chat pages name the space as `space`,
and writes take their rules freshness only from the `Commonplace-Rules-Seq` header.
Errors are `space_not_found` (404), `invalid_arguments` (400) and `method_not_allowed`
(405). See `docs/boards/API.md` for the full contract.

## Agent tools

| Space name | Same as | Argument |
|---|---|---|
| `space_list` | `board_list` | — |
| `space_add` | `board_add` | `slug`, `name?`, `wing?`, `repo?` |
| `space_set` | `board_set` | `space`, `coordinator` |
| `space_own_wing` | `board_own_wing` | `space` |
| `space_read`, `space_post` | unchanged | `space` (or the legacy `board`) |

The board names stay. Naming two different spaces in one call, or none, is refused.
A connection that may use one name of a pair may use the other: the new names add no
authority.

## Not yet

Renaming and archiving a space, versions and retries, settings and orchestrator
routes under `/api/spaces`, the `space` argument on card tools, the app's wording
and desktop synchronization are planned later steps.
