### Completion reviewer

A card may name one reviewer of its completion, a current workspace member
(`user:<id>`). Only a signed-in workspace owner chooses, changes or clears it
(`reviewer` on `task_add` and `task_update`, and on the card REST routes); agents
and members are refused. Without a reviewer nothing changes: the owner is asked
and only an owner answers. With a reviewer, a move to Done by anyone but that
member (an agent, the assignee or the owner) opens a completion review addressed
to them even on a board that does not gate completions, and `decide` answers it
only for that member. An assignee therefore reviews their own work only when the
owner explicitly chose them. A reviewer who has left the workspace counts as no
reviewer. Changing the reviewer re-addresses an open completion review, and the
chosen reviewer sees it in `needs_me`. The pinned MCP contract adds only the
optional `reviewer` argument to `task_add` and `task_update`.
