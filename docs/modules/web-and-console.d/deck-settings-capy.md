- **Owns:**

  - Deck Settings places Bot sharing, What bots may do without asking, Second review and Connected apps in the main column, with the existing Appearance controller in a sticky right column. Below 1100px the columns stack and Appearance comes last. Notifications, workspace navigation, plan, voice, chat, schedules, webhooks, sign out and the classic-look switch remain in the same page with their original forms and handlers.

- **Promises:**

  - Allow, Ask and Deny write the server's existing allowed, ask and off policy values through the shared controller. An unfamiliar stored mode is displayed as Ask and retained exactly until a different choice is explicitly selected. Controls without a supported server value are disabled. Human labels, purpose groups, search and technical-name disclosures remain. Connected apps use plain client labels; scope titles put human words before the raw ID.

  - Bot sharing reads and writes the existing workspace sharing route and honors its owner flag. Private defaults, access logs and owner-only grants describe existing guarantees rather than inventing separate switches. Second review opens the existing project settings; there is no new workspace-wide review switch. All visible values and project names come from the hub.

- **Tests:**

  - `tests/test_deck_settings_capy.py` enumerates server policy values and installed actions with the Node contracts. `tests/test_deck_settings_capy_browser.py` optionally checks desktop and phone geometry, every original form, policy writes, sharing and live Appearance controls.
