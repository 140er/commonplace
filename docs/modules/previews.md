## previews

- **Paths:** `commonplace/previews/`, `tests/test_previews.py`, `tests/test_previews_account.py`, `tests/test_previews_serving.py`, `tests/test_previews_browser.py`, `commonplace/web/static/cardpreviews.js`, `tests/cardpreviews.mjs`, `tests/test_card_previews_ui.py`, `tests/test_card_previews_browser.py`, `tests/test_previews_round2.py`, `tests/test_previews_delete.py`
- **Owns:**

  - Persistent previews, slice 1 (card #612, design in `docs/system/previews.md`): a Coworker publishes a bundle of files for a card and people who can open that card list, stop and reopen it later. Rows live in the control database (`previews`, `preview_revisions`, append-only `preview_events`, `preview_rules`); each revision's files live under `<workspace dir>/previews/<id>/r<n>/` and are written once.

  - `bundle.py` checks a bundle before anything is stored (size, file count, paths, entry page, credential-shaped text); `store.py` holds the tables, the limits and the fixed reason texts; `service.py` is publish, stop, reopen and the stop rule; `routes.py` is `/api/previews` for signed-in people; `tools.py` is `preview_publish`, `preview_list` and `preview_stop` for Coworkers; `serving.py` is `GET /p/<id>/<path>`, the files of a ready preview with the isolation headers and the state pages; `links.py` is the short-lived page link (`preview_links`) that stands in for the session for the sandboxed page's own requests.

  - `web/static/cardpreviews.js` is the Previews section of the card panel (after Runs): every preview of the card with its state in words, the reason and next step, Open in new tab, Stop and Open again, a note on the 30-minute key and the static limit, the workspace's usage and stop rule, and for the owner a form to change the rule that lives outside the redrawn list, so a redraw keeps it open, with its text and focus.

- **Promises:**

  - A preview is reachable only through its card: an unknown id, another workspace's id and a card the caller cannot see answer the same 404. Agent tokens are refused on the people routes (403) and use the tools; untrusted tokens do not see the write tools.

  - Four states, each with a fixed reason and a next step: `building`, `ready`, `stopped`, `failed`. A publish that stays `building` for more than 15 minutes reads as `failed` (`publish_interrupted`).

  - Nothing is deleted by itself: stopping keeps files and quota refusals never evict. Files go only when a workspace owner deletes a preview's files (confirmed with the repeated id; the folder is renamed aside under the control-database lock and removed after it is released, see "Deleting files" in the design note) or the whole workspace is deleted. A publish whose preview was stopped while it wrote its files refuses itself (`changed_meanwhile`) instead of reviving it; a commit that fails after the files were placed removes them. Placing a revision is a rename under the control-database lock; a leftover directory is renamed aside there and removed after the lock is released, and no file writing or recursive removal runs under that lock. Limits: 5 MiB and 500 files per bundle, 25 ready previews and 500 MiB per workspace, 200 previews kept per workspace (refused attempts that never published do not count; the newest 50 refused attempts are kept; at the cap a publish is refused as `too_many_previews`), 10 publishes per card per hour (a malformed request uses none). The stop rule is off by default; when the owner switches it on it is applied when a preview is next read or requested, and stopping keeps the files.

  - Responses name no filesystem path, host or environment value; a credential-shaped value in a file refuses the publish and names only the file and the kind.

  - Files are served only to signed-in people who can open the card, or under a 30-minute page key of such a person for that one preview (anyone with that exact address can open it until it ends; the texts say so), re-checked on every request; signed-out browsers get a sign-in page, agent tokens a 403 page, and unknown, foreign or hidden previews one identical 404 page. Every file carries the sandbox policy (inline scripts and styles allowed, no popups, forms or other websites), so a preview cannot read the viewer's cookies or storage or call `/api/`; the sign-in page and the ready text say the 30-minute key out loud.

  - Nothing outside the revision folder is read: decoded paths follow the published-path rules, links are never followed, and the state pages quote nothing from the request.

  - The card's Previews section keeps no module-level cache (a redraw repaints only what the same panel showed a moment ago, so nothing carries over to another session or workspace) and never shows an empty panel: no previews says so, a server it cannot reach says so with Try again (or keeps the last list and says it is old), and a card the person cannot see shows no section at all. Only the server's own `/p/<id>/` address is ever a link.

- **How it fails:**

  - A refused bundle returns 422 with its reason (`no_index_html`, `too_large`, `too_many_files`, `bad_path`, `contains_secret_like_text`), 409 for `workspace_quota_full` (25 served), `workspace_space_full` (500 MiB; stopping frees no space), `too_many_previews`, `person_stopped` and `changed_meanwhile`, 429 for `rate_limited` and 503 for `storage_unavailable`; a new name keeps a failed row the card shows, an existing preview keeps serving its current revision.

  - A preview that is not ready answers with a page in words: 410 stopped, 503 failed, being published or files missing on the server, 404 for an address not in it. An expired or unknown page link sends a signed-in browser back to the plain address and asks anyone else to sign in.

- **Tests:**

  - `tests/test_previews.py` covers publish and persistence, revisions and idempotence, the card note, uniform refusal across workspaces and hidden cards, every refusal reason, stop and reopen, the stop racing a republish, the cap, pruning of refused attempts, no recursive file work under the lock, quotas and the rate limit with a moved clock, stale `building`, storage failure, leak checks, append-only events, the stop rule, pull request listing and the pure path and credential checks.

  - `tests/test_previews_account.py` covers account export and deletion with published previews and their events present: records are exported without the published files, a purge removes the preview rows and files with a deleted workspace, the append-only events stay, and another workspace's previews are untouched.

  - `tests/test_previews_serving.py` covers the served files and headers, content types, redirects, the uniform 404, the sign-in and agent pages, path discipline and links, every state page, missing files, the age and pull request stop rules with a moved clock, a hostile page's `Origin: null` writes, a restarted server and the page link (no session needed, one preview only, re-checked, expiry and pruning). `tests/test_previews_browser.py` (Playwright) opens a hostile page in Chromium: its own stylesheet and script load, the cookie, storage and `/api/` do not. It also tries nine ways a page can move the viewer page or reach another site, under a shared cookie domain, and pins that the tab stays on the viewer and the other site receives nothing. These browser cases are skipped, with a reason, where the Playwright package is not installed (the file's docstring gives the command; `CP_BROWSER_E2E=1` makes a missing package fail).

  - `tests/cardpreviews.mjs` (run by `tests/test_card_previews_ui.py`) covers every state, the empty and offline views, escaping, link checks, the stop rule form, Stop, Open again, refusals and the panel placement; `tests/test_card_previews_browser.py` (Playwright) shows every state in the real shell at desktop and phone size, 44 px touch targets on a phone, one click to open in a new tab and Stop from the keyboard.

  - `tests/test_previews_round2.py` covers the review round: the 30-minute key and the three kept per person, no key for an address that names no file, the texts that name the key, person words and the stored file and kind of a refusal, start and publish times, links on every served page, a Coworker refused on a preview a person stopped, the same files served again without a copy, card lines for Stop and Open again, credential-like names, a leading `~`, a linked folder, and the schema upgrade.

  - `tests/test_previews_review2.py` covers review round 2 on the server: each limit named in its own words, stale failure details cleared, a credential-shaped file name refused by kind only, a Coworker refused when it reopens what a person stopped, the key told on the sign-in page and in the ready row, the being-published page, the page policy and the tool words (versions, KiB, served).

  - `tests/cardpreviews.mjs` also covers the review round on the card: start versus publish times, the failure file and kind, accessible names, the offline wording and the not-loaded view, focus after Stop, Open again, a refusal and saving the rule, focus through a redraw, the status line, polling while publishing, and that the shell's focus keys match; `tests/test_card_previews_browser.py` adds 320 px, 768 px touch and the open rule form, 44 px checks off the desktop, focus after Stop and focus through a polling redraw.

  - `tests/test_previews_delete.py` covers deleting files: owner only with the repeated id, the removal with the lock released, a delete racing a republish (deterministic, no sleeps), a preview still being published, a removal that leaves files and the retry, a refused rename that changes nothing, the cap after a delete, the column added to a first-slice revision table, and the served page of a deleted preview. `tests/cardpreviews.mjs` covers the owner-only Delete files button, its confirmation and the result.

  - Review round 2 on the card adds to `tests/cardpreviews.mjs` the stop-rule form kept through a list repaint, a panel redraw and a refused Save, focus that counts only when the control really holds it, the status said before a request and for the offline view, the key and static note, the version and publisher words and the phone font size; `tests/test_card_previews_browser.py` types in the open rule form, delivers a feed event and checks it keeps its state and focus.
