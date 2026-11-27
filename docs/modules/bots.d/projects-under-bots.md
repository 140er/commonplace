- **Owns:**

  - Projects under bots (card #676, `docs/system/bot-project-tree.md`): `project_links.py` holds the `bot_projects` table (created when the file is opened), `fold` and `ProjectLinksMixin` of `BotStore` (`seed_projects`, `project_links`, `move_project`, `release_project`); `ensure_defaults` seeds links for projects that have none. `routes.py` serves `GET`/`PUT`/`DELETE /api/bots/<id>/projects[/<slug>]` for the bot's owner in a web session and adds `projects` to each listed bot.

- **Promises:**

  - A project is under at most one of a person's bots and a bot owns any number; the link is the person's own, so it is never shown to or changed by anyone else (another person's bot is 404, a shared bot's owner-only change is 403). The seeding only inserts, repeating it changes nothing, and no bot, message, note or link is deleted (archiving a bot keeps its links). Every real move is one `bot_project:move` audit row without text.

- **How it fails:**

  - A project the person cannot read or an archived bot is 400, a body that is not empty is 400, an unknown route tail is 404; a file from an earlier release is upgraded when opened.

- **Tests:** `tests/test_bots_project_tree.py` covers the mapping (own project bot, name match, ambiguous, unmatched), idempotent seeding, reopening an older file, moves with read-back, who may move and what a member sees.
