### Subagent questions

A subagent must first propose a card and obtain approval and release before asking.
On an existing released card, a subagent's `ask` targets the board's coordinator when that connection is live,
unrestricted, can read and write the board wing, and can use `needs_me` and
`decide`. The question appears in that coordinator's queue instead of the owner's.
Only that addressed, still-live coordinator or a signed-in workspace owner can
answer. The coordinator can instead use `decide(then="escalate", reply=<reason>)`
to send the unchanged question, options, context and return recipient to the owner;
an empty reason is refused. Owner and top-level asks, completion reviews and
explicit person handoffs retain their existing route. When no coordinator is
available, existing owner routing and proposal requirements remain in effect.
If a routed coordinator becomes unavailable or is replaced, the pending ask
appears in the owner queue again without changing its stored words or audit log.


