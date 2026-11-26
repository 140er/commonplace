- **Owns:**

  - Waiting on you lists each proposed workspace setup (card #675) as an approval (`web/static/deck-today-capy.js` `setupHTML` and `appliedLine`, with `setupProposals` in `needs-me.js` and `isSetup`, `setupKey` and `removeSetup` in `deck-home-today.js`): proposer by name, a one-line summary, the number of changes, a "Changes" list with one line per bot, Coworker and model role, Approve and Reject. The sidebar and Today counts include setups; setups come first, then config proposals, then cards.

- **Promises:**

  - Approve is one click and posts the digest shown (`{sha256}`) to `POST /api/setup-proposals/<id>/apply`; Reject asks once and posts the same digest to `.../reject`. The owner check and the digest check stay on the server; a 403, 404 or 409 keeps the item, says why and re-reads the queue. The confirmation says whether everything under Changes is in place or how many parts could not be done. Copy says Coworker and Bot, never orchestrator; the item reuses the config proposal's markup and styles.

- **Tests:**

  - `tests/deck_setup_proposals.mjs`, run through `tests/test_deck_home_today.py`. Screenshots of a fresh-owner walkthrough: `docs/usability-audit-372/675-before-today-desktop.png`, `675-after-today-item-desktop.png`, `675-after-today-changes-desktop.png`, `675-after-approved-desktop.png`, `675-after-bots-desktop.png`, `675-after-settings-models-desktop.png`, `675-after-settings-no-consent-desktop.png`, `675-after-today-item-phone.png`, `675-after-today-changes-phone.png`, `675-after-today-reject-ask-phone.png`.
