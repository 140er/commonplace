- **Owns:** `boards/assignees.py` owns the list of agents a person may hand a new card
  to (`GET /api/boards/<slug>/assignees`, person sessions only); `boards/rest.py` routes it.
- **Promises:** The list holds the agents the person's console shows (the workspace
  owner sees every agent, a member only their own), narrowed to the connections the
  board accepts as an assignee. Each entry is `ref`, `name` and `starts_run`. `starts_run`
  is true only when the person owns the workspace, the board has a configured Coworker
  and the agent is that Coworker's worker; handing a card to any other agent never
  starts a run.
- **How it fails:** A board the person cannot see answers 404, an agent credential answers
  403, and a person who may not add cards to the board gets an empty list. A failed
  start lookup reads as "starts nothing".
- **Tests:** `tests/test_board_add_who.py` lists the agents for the owner and a member,
  and compares the text the form shows with the jobs the server then queues.
