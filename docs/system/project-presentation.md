### Project panel and coordinator from project settings

Project settings have two sections that were missing: the project panel and the
coordinator. They are separate from Agent models and from Connect agent, and the
coordinator section says so in plain words: it only chooses which connected agent
coordinates, and it never changes a model or creates, rotates or revokes a token.

The panel editor writes through the existing panel route and its limits (a title,
up to six highlight lines and up to six links). It shows the saved result, and
Clear panel asks first. The editor appears for the workspace owner and for anyone
the server already lets change the project's board; everyone else reads the panel
with the reason they cannot edit it.

The coordinator section lists the agents that are eligible today, as the board
settings rules decide, plus No coordinator. Choosing one opens a preview from fresh
server counts: who stops coordinating, how many open asks addressed to them come
back to the owner, who starts, and that open cards keep their status. Nothing is
written until Confirm change. Replacing or clearing keeps the existing rule that the
previous coordinator's stored approval to answer asks is cleared, and a new
coordinator starts without one. Only the workspace owner sees the controls and the
counts; others read the current coordinator and the reason.

Decisions: no new permission model, route or write path was added. The project
read gained `can_edit_panel`, and the owner-only coordinator read gained counts. A
failed write keeps the old state and re-reads it; a coordinator list that cannot be
read shows a message with Try again, not an empty list.
