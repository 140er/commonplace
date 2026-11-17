# Coworker setup: before and after

The same production controllers were captured at 1280×844 and 390×844 CSS pixels.
Before uses develop at `313b88d5`; after uses this change. Both runs used Playwright
1.65.2, its installed Chromium and synthetic workspace data. Screenshots remain
outside version control, following the usability audit's evidence convention.

| Width | Dead end | Before | After |
| --- | --- | --- | --- |
| 1280 | Empty DM | Create a bot before starting a DM. | Create a bot before starting a DM. **Create bot** opens the shared editor. |
| 390 | Empty DM | Create a bot before starting a DM. | Create a bot before starting a DM. **Create bot** opens the shared editor. |
| 1280 | Create forms | Conversation: Create a bot / Instructions / Create bot. Personal: New bot / How to work / Create. | Both: Create bot / Name / How to work / Create bot; shared name wording; the existing server limit is 60 for personal bots and 80 for conversation bots, with Unicode accepted. |
| 390 | Create forms | Conversation: Create a bot / Instructions / Create bot. Personal: New bot / How to work / Create. | Both: Create bot / Name / How to work / Create bot; shared name wording; the existing server limit is 60 for personal bots and 80 for conversation bots, with Unicode accepted. |
| 1280 | Empty roster | No Coworkers recorded yet. | No bots on this project yet. **Add a bot** links to the creation directory. |
| 390 | Empty roster | No Coworkers recorded yet. | No bots on this project yet. **Add a bot** links to the creation directory. |
| 1280 | Missing model | Needs configuration / needs_configuration | Needs a model: choose one in **Project settings**, under Chat answers. Then send your message again. |
| 390 | Missing model | Needs configuration / needs_configuration | Needs a model: choose one in **Project settings**, under Chat answers. Then send your message again. |
| 1280 | Missing machine | Task created · runner unavailable / No runner is configured. The task remains available. | Needs a machine: **Add machine**. The task remains available; open it before starting work. |
| 390 | Missing machine | Task created · runner unavailable / No runner is configured. The task remains available. | Needs a machine: **Add machine**. The task remains available; open it before starting work. |
| 1280 | Failed provider | Failed / provider_unavailable | The provider could not be reached. **Try again in the conversation**. |
| 390 | Failed provider | Failed / provider_unavailable | The provider could not be reached. **Try again in the conversation**. |

For each width, capture stems are `dm-prerequisite`, `conversation-create`,
`personal-create`, `project-roster` and `recovery`, suffixed with the width and
`.png`. `strings-1280.json` and `strings-390.json` contain the actual visible text.
Each set is saved separately as before and after.

The four original dead-end harness cases all failed against plain develop before
implementation and pass after implementation. Additional assertions cover human
names, invalid-name draft preservation and members without create permission.
Browser checks create a conversation bot through the prerequisite action and then
open a DM with that bot selected. The existing durable gateway browser case
independently creates a bot and two DMs, receives synthetic model replies, reloads
persisted messages and verifies conversation isolation and cancellation.

The setup-banner regression for an unknown model code was reproduced for an owner
before the fallback fix; the member case remained passing. Both now preserve the
existing setup text, and the owner also retains the settings link. Thirty contract
cases compare the client validator with both real stores, including non-ASCII
letters, spaces, punctuation, emoji boundaries and invalid UTF-8 text. The client
no longer applies connection-identifier rules to human display names.
