## previews

- **Paths:** `commonplace/previews/`, `tests/test_previews.py`, `tests/test_previews_account.py`, `tests/test_previews_serving.py`, `tests/test_previews_browser.py`
- **Owns:**

  - Persistent previews, slice 1 (card #612, design in `docs/system/previews.md`): a Coworker publishes a bundle of files for a card and people who can open that card list, stop and reopen it later. Rows live in the control database (`previews`, `preview_revisions`, append-only `preview_events`, `preview_rules`); each revision's files live under `<workspace dir>/previews/<id>/r<n>/` and are written once.

  - `bundle.py` checks a bundle before anything is stored (size, file count, paths, entry page, credential-shaped text); `store.py` holds the tables, the limits and the fixed reason texts; `service.py` is publish, stop, reopen and the stop rule; `routes.py` is `/api/previews` for signed-in people; `tools.py` is `preview_publish`, `preview_list` and `preview_stop` for Coworkers; `serving.py` is `GET /p/<id>/<path>`, the files of a ready preview with the isolation headers and the state pages; `links.py` is the short-lived page link (`preview_links`) that stands in for the session for the sandboxed page's own requests.

- **Promises:**

  - A preview is reachable only through its card: an unknown id, another workspace's id and a card the caller cannot see answer the same 404. Agent tokens are refused on the people routes (403) and use the tools; untrusted tokens do not see the write tools.

  - Four states, each with a fixed reason and a next step: `building`, `ready`, `stopped`, `failed`. A publish that stays `building` for more than 15 minutes reads as `failed` (`publish_interrupted`).

  - Nothing is deleted: stopping keeps files, quota refusals never evict, and only the deletion of the whole workspace removes them (deleting a preview's files is a later slice, card #622). A publish whose preview was stopped while it wrote its files refuses itself (`changed_meanwhile`) instead of reviving it; a commit that fails after the files were placed removes them. Placing a revision is a rename under the control-database lock; a leftover directory is renamed aside there and removed after the lock is released, and no file writing or recursive removal runs under that lock. Limits: 5 MiB and 500 files per bundle, 25 ready previews and 500 MiB per workspace, 200 previews kept per workspace (refused attempts that never published do not count; the newest 50 refused attempts are kept; at the cap a publish is refused as `too_many_previews`), 10 publishes per card per hour (a malformed request uses none). The stop rule is off by default; when the owner switches it on it is applied when a preview is next read or requested, and stopping keeps the files.

  - Responses name no filesystem path, host or environment value; a credential-shaped value in a file refuses the publish and names only the file and the kind.

  - Files are served only to signed-in people who can open the card, or under a page link of such a person for that one preview, re-checked on every request; signed-out browsers get a sign-in page, agent tokens a 403 page, and unknown, foreign or hidden previews one identical 404 page. Every file carries the sandbox policy, so a preview cannot read the viewer's cookies or storage or call `/api/`.

  - Nothing outside the revision folder is read: decoded paths follow the published-path rules, links are never followed, and the state pages quote nothing from the request.

- **How it fails:**

  - A refused bundle returns 422 with its reason (`no_index_html`, `too_large`, `too_many_files`, `bad_path`, `contains_secret_like_text`), 409 for `workspace_quota_full`, `too_many_previews` and `changed_meanwhile`, 429 for `rate_limited` and 503 for `storage_unavailable`; a new name keeps a failed row the card shows, an existing preview keeps serving its current revision.

  - A preview that is not ready answers with a page in words: 410 stopped, 503 failed, being published or files missing on the server, 404 for an address not in it. An expired or unknown page link sends a signed-in browser back to the plain address and asks anyone else to sign in.

- **Tests:**

  - `tests/test_previews.py` covers publish and persistence, revisions and idempotence, the card note, uniform refusal across workspaces and hidden cards, every refusal reason, stop and reopen, the stop racing a republish, the cap, pruning of refused attempts, no recursive file work under the lock, quotas and the rate limit with a moved clock, stale `building`, storage failure, leak checks, append-only events, the stop rule, pull request listing and the pure path and credential checks.

  - `tests/test_previews_account.py` covers account export and deletion with published previews and their events present: records are exported without the published files, a purge removes the preview rows and files with a deleted workspace, the append-only events stay, and another workspace's previews are untouched.

  - `tests/test_previews_serving.py` covers the served files and headers, content types, redirects, the uniform 404, the sign-in and agent pages, path discipline and links, every state page, missing files, the age and pull request stop rules with a moved clock, a hostile page's `Origin: null` writes, a restarted server and the page link (no session needed, one preview only, re-checked, expiry and pruning). `tests/test_previews_browser.py` (Playwright) opens a hostile page in Chromium: its own stylesheet and script load, the cookie, storage and `/api/` do not.
