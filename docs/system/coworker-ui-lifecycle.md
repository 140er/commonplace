### Coworker UI lifecycle

`/app/#/coworkers` adds a discoverable private conversation surface without
replacing existing project chat. Server data drives bot profiles, DM/group
membership, messages, jobs/task links and scoped memory. Multiple-bot groups
require an explicit or unambiguous recipient; ordinary group discussion starts
no bot broadcast. Message receipt and pending work are distinct from answers; canonical task
admission is labeled Task created, not task completion.
Conversation controls wait for both the detail and authorized bot directory,
including when detail arrives first. Input limits match server message/memory
bounds. The composer remains available during background jobs; ambiguous HTTP retries
reuse their original request key. Conversation changes, lost access, workspace
changes and sign-out invalidate older responses and clear private views. No
worker identity, model response or live execution is fabricated in this UI.
An addressed human message retains its person author even when `bot_id` names
the recipient. Bot names, badges and styling require the exact matching bot actor.
Human group invitations currently use explicit existing workspace user IDs;
a member directory is not implemented by this slice.
