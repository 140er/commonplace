## previews

- **Paths:** `commonplace/previews/`, `tests/test_previews.py`, `tests/test_previews_account.py`
- **Owns:**

  - Persistent previews, slice 1 (card #612, design in `docs/system/previews.md`): a Coworker publishes a bundle of files for a card and people who can open that card list, stop, reopen and (owners) delete it later. Rows live in the control database (`previews`, `preview_revisions`, append-only `preview_events`, `preview_rules`); each revision's files live under `<workspace dir>/previews/<id>/r<n>/` and are written once.

  - `bundle.py` checks a bundle before anything is stored (size, file count, paths, entry page, credential-shaped text); `store.py` holds the tables, the limits and the fixed reason texts; `service.py` is publish, stop, reopen, delete and the stop rule; `routes.py` is `/api/previews` for signed-in people; `tools.py` is `preview_publish`, `preview_list` and `preview_stop` for Coworkers.

- **Promises:**

  - A preview is reachable only through its card: an unknown id, another workspace's id and a card the caller cannot see answer the same 404. Agent tokens are refused on the people routes (403) and use the tools; untrusted tokens do not see the write tools.

  - Four states, each with a fixed reason and a next step: `building`, `ready`, `stopped`, `failed`. A publish that stays `building` for more than 15 minutes reads as `failed` (`publish_interrupted`).

  - Nothing is deleted automatically: stopping keeps files, quota refusals never evict, and only an owner's explicit `DELETE` with the repeated id removes files, or the deletion of the workspace itself. Limits: 5 MiB and 500 files per bundle, 25 ready previews and 500 MiB per workspace, 10 publishes per card per hour. The stop rule is off by default and is stored and shown but not applied yet.

  - Responses name no filesystem path, host or environment value; a credential-shaped value in a file refuses the publish and names only the file and the kind.

- **How it fails:**

  - A refused bundle returns 422 with its reason (`no_index_html`, `too_large`, `too_many_files`, `bad_path`, `contains_secret_like_text`), 409 for `workspace_quota_full`, 429 for `rate_limited` and 503 for `storage_unavailable`; a new name keeps a failed row the card shows, an existing preview keeps serving its current revision.

  - Serving the files at `/p/<id>/` is not part of this slice: the URL is returned but nothing answers it yet.

- **Tests:**

  - `tests/test_previews.py` covers publish and persistence, revisions and idempotence, the card note, uniform refusal across workspaces and hidden cards, every refusal reason, stop, reopen and delete, quotas and the rate limit with a moved clock, stale `building`, storage failure, leak checks, append-only events, the stop rule, pull request listing and the pure path and credential checks.

  - `tests/test_previews_account.py` covers account export and deletion with published previews and their events present: records are exported without the published files, a purge removes the preview rows and files with a deleted workspace, the append-only events stay, and another workspace's previews are untouched.
