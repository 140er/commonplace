- **Owns:**

  - Deck Settings places Bot sharing, What bots may do without asking, Second review and Connected apps in the main column, with the existing Appearance controller in a sticky right column. Below 1100px the columns stack and Appearance comes last. Notifications, workspace navigation, plan, voice, chat, schedules, webhooks, sign out and the classic-look switch remain in the same page with their original forms and handlers.

- **Promises:**

  - Allow, Ask and Deny write the server's existing allowed, ask and off policy values through the shared controller. An unfamiliar stored mode is displayed as Ask and retained exactly until a different choice is explicitly selected. Controls without a supported server value are disabled. Human labels, purpose groups, search and row-title technical names remain. Connected apps use plain client labels; scope titles put human words before the raw ID.

  - Settings adds a role-aware workspace subtitle, grey inactive segments and red selected Deny, optional hub-provided limits, 44px phone controls on the title row, and compact purpose headings. Appearance keeps independent 2px scene previews and the 60% vignette default; custom slider tracks use dark unfilled portions, violet fill and 16px thumbs.

  - Bot sharing reads and writes the existing workspace sharing route and honors its owner flag. Private defaults, access logs and owner-only grants are fixed guarantees grouped under Always on; the real sharing switch remains separate. Second review opens the existing project settings; there is no new workspace-wide review switch. All visible values and project names come from the hub.

  - Members retain sign out, navigation, personal Appearance, notifications, webhooks and their own bot grants. Only workspace policy, management, voice, schedules and connection management controls are disabled; existing controllers and server authority still decide access. Common Settings installs one scroll listener per scrolling box, even when late renders replace its children.

- **Tests:**

  - `tests/test_deck_settings_capy.py` enumerates server policy values and installed actions with the Node contracts. `tests/test_deck_settings_capy_browser.py` optionally checks desktop and phone geometry, every original form, policy writes, sharing and live Appearance controls.
