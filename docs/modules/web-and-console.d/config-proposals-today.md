- **Owns:**

  - Waiting on you lists each proposed Coworker config as an approval (`web/static/deck-today-capy.js` `configHTML`, with `configProposals` in `needs-me.js` and `removeConfig` in `deck-home-today.js`): proposer by name, a one-line summary, a "Changes" section with the diff, Accept and Reject. The sidebar and Today counts include proposals.

- **Promises:**

  - Accept is one click and Reject is two (it asks once with the project settings wording). Both call the existing config routes, so the owner check stays on the server; the project settings list stays the history view.

- **Tests:**

  - tests/deck_config_proposals.mjs, run through tests/test_deck_home_today.py.
