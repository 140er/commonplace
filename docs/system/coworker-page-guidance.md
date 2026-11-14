### Coworker page guidance

Usability testing found five confusing spots on the Coworker page. Decisions:

- The Working on panel always starts with one plain line. It uses only data the
  page already reads (jobs, their task progress, the Coworker state and the
  project checkpoint). Nothing new is stored or fetched.
- Send stays disabled until a conversation is open, and the page says so next
  to the button. Steer says why it is unavailable instead of silently greying out.
- A bot has one page. The Bots list and the rail both open the bot's own thread
  (`#/bots/<id>`); only a bot known solely to the Coworker registry opens its
  Coworkers view. The Coworkers view stays one click away on the bot page.
- Steer, Chat and Private are explained in one line each next to the composer.
- A missing chat model tells an owner which setting to open and a member whom
  to ask. The copy lives in one module shared by every surface.

Visual treatment stays with the design work; this change only adds structure and copy.
