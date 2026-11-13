### Goals tree in card context

- The workspace has one small goals tree (6 levels, 200 goals, plain single-line text) that only a signed-in workspace owner writes, through the REST routes or the MCP tools `goal_set` and `card_goal`; every connection can read it with `goal_list`. A card points at at most one goal, and a goal that is archived stays in the context of cards already pointing at it.
- `task_show` and `task_take_next` add the card's goal chain, top-level first, as `goals` only when a goal is set; cards without one are unchanged. The goal text fits a fixed 1200-character budget: higher goals are dropped from the top level down and counted, and the card's own goal always stays.
- `goal_set` and `card_goal` are checked writes under the rules freshness gate (`docs/design/RULES.md`): goals are instructions in agent context.
- Interim choices pending owner: the tree is workspace-wide (no per-board goals), agents cannot point a card at a goal themselves, and there is no web goal picker yet.
