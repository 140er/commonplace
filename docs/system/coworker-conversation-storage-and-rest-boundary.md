### Coworker conversation storage and REST boundary

The coworker app adds private DM/group audiences alongside existing Space chat;
it does not mirror private messages into public board timelines. Its
workspace-local `coworkers.sqlite3` uses the existing workspace crypto connection
and creation gates; no plaintext-encryption guarantee beyond that configured
mechanism is implied. Bot presentation/configuration grants no worker connection
or execution rights. Existing connections, workspace users and canonical cards
remain authoritative. Bot creation is owner-only.

The person-session `/api/coworkers` routes require a mandatory exact allowed
Origin and bounded non-simple JSON request for writes. They persist original
messages and addressed jobs atomically without provider calls. Reads and writes
require current conversation membership, with foreign/private IDs concealed.
Jobs retain only the originating session digest, never the cookie, and recheck
that authority before dispatch/publication. Exact retries return stored intake;
changed text/recipient under the same key conflicts. Multi-bot group messages
without a single resolved target do not wake every bot. Explicit conversation
memories remain verbatim; tombstones hide active recall and fence responses
created with older visibility. Pending jobs survive restart; uncertain running
jobs become interrupted instead of blind redispatch. Runtime installation is
separate; a stored pending job is not evidence of model response or worker launch.
