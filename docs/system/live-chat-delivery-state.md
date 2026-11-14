### Live chat delivery state

The chat page (`GET /api/chat/<board>`) reports a `live` state for the caller's
own newest message: `idle`, `delivered`, `working`, `answered`, `no_agent` or
`no_answer`, with a reason. It is derived from stored facts only: the wake
delivery row of the `space_message` event that carried the message
(`chat_live_posts` maps message to event), the read cursor of the board's
current orchestrator connection (`chat_live_reads`, advanced after that
connection's `space_read`), and any later agent message. Both tables live in
the control database, are keyed by workspace and board, and hold ids and times
but no message text. The server calls no model and no outside service for this
state. A read by a person or by an agent that is not the board's coordinator
records nothing, and a failed cursor write never fails the read. The agent's
read time is returned only to a caller who may see that agent in the console.
`no_answer` follows 60 seconds after a successful wake without a read, 5
minutes without a wake hook or after a read without a reply, and at once when
every wake attempt failed. See `docs/chat.md`.
