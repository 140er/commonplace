### Agent questions

Agent `ask` calls and `task_update status=review` questions target the board's
appointed coordinator by default when that connection is live, unrestricted,
can read and write the board wing, and can use `space_read`, `space_post`,
`needs_me` and `decide`. They appear in the coordinator's queue instead of the
owner's. Supply a nonempty `person_reason` (at most 500 characters) to explain
why a person is required; the reason is preserved in context and opening history.
Without an authorized coordinator the question reaches the owner. Completion
reviews, person callers and explicit person handoffs keep their owner/person route.
A status line never hides a person decision or an item needing person release.

Only the addressed, still-authorized coordinator or a signed-in workspace owner
can answer; agents never answer their own asks. The coordinator may instead use
`decide(then="escalate", reply=<reason>)` to send the unchanged question, options,
context and return recipient to the owner; an empty reason is refused. An appointed
coordinator may withdraw only an agent-directed decision it requested itself,
using `task_update status=todo|doing` with optional `reason`. Current appointment,
recipient and requester are rechecked. Other agents, member people and coordinators
of other boards have no withdrawal authority. Withdrawal keeps the original ask
in append-only history and does not imply approval. Person decisions cannot be
withdrawn by agents. Existing asks are never rewritten by this routing change.

Subagents still need an existing approved and released card before asking. If a
routed coordinator becomes unavailable or is replaced, the pending ask appears
in the owner's queue without changing its stored words or audit log.

The shipped agent skill teaches the same routing and withdrawal boundaries. The
pinned MCP contract adds only optional `person_reason` to `ask` and `task_update`,
with matching descriptions for `ask`, `task_update` and `decide`; tool names and
visibility for slim, standard and full profiles remain unchanged.
