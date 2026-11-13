### Compute management and Bots' Coworkers

Deck retains Today, Bots, Compute and Settings. Compute defaults to its shared
topology canvas; Management opens the existing authenticated controller at
`#/compute/management`. Legacy classic `#/compute` links redirect to management;
classic navigation and its `#/compute-map` screen remain unchanged.

Coworkers belong under Bots: `#/bots/<bot-id>/coworkers` extends the existing Bot
detail route; `#/bots/-/coworkers` lists the workspace's project-backed Bots.
Rows show the parent Bot, its existing project/repository association and recorded
child threads. The displayed worker role is Coworker, including forwarded replies
in Deck Bot threads. Wire names and stored fields are unchanged. The current
project association does not establish a new durable Bot ownership grant; an
explicit parent/ownership field and authoritative subagent roster remain follow-ups.

The existing conversation controller lives at
`#/bots/-/coworkers/conversations[/<conversation-id>][?message=<message-id>]`.
Legacy `#/coworkers[/<conversation-id>][?message=<message-id>]` links redirect into
this section. Routes are resolved once before dispatch, without reserving an
existing Bot ID. Departing stops controllers, aborts requests and clears transient
forms; late reads cannot repaint the next page. The complete contract is in the
web-and-console module fragment.
