# Words in the app and the tool names (card #667)

The user guide section is "Words used in Commonplace" in `docs/projects.md`. This page keeps the technical names next to it. Nothing below is renamed by card #667: only visible labels change.

One name for each thing. The same word is used in the sidebar, in Settings, on the board, on a
card, in the status list, in the Move menu and in these guides. Where a tool or an API route has to
keep an older name, the table says which one it is.

## Cards and where they sit

A card has one **status**. The board shows cards in **lanes**, and a lane groups statuses.

| Status | Where it shows on the board | What it means |
|---|---|---|
| Backlog | Not in a lane (listed under Backlog) | Parked. Nobody is working on it yet |
| Todo | Active | Ready to be picked up |
| Doing | Active | Someone has started it |
| Review | In review | Finished work waiting for a look |
| Done | Shipped | Finished and live |

**Waiting on you** is a lane that shows any card that needs a decision, an answer or an approval
from you, whatever its status. It is not a status, so you never move a card into it. A card in it
says "Needs you" next to its status.

Next to its status, a card also says what is happening to it: Ready, Building, Checking, On hold,
In review, Needs you or Live. These words are a summary for reading the board. The status is what
is stored and what you set.

**Move to…** on a card lists all five statuses. The ones you cannot move to from where the card is
now are listed too, greyed out, with what to do first (for example "Move it to Doing first.").
The server makes the final decision and refuses a move you may not make.

## Names in the app, the tools and these guides

| Concept | In the app | Tool or API name | Chosen name, and why |
|---|---|---|---|
| A project: its board, its memory and its Coworker | Projects (sidebar, Settings, search), New project. Admin used to list projects under "Bots" | `board_add`, `board_list`, `space_add` (the older name for the same call), routes under `/api/boards` | **Project**. It is what a person creates and names. The tools keep "board" and "space"; they mean a project |
| The cards of one project | Board (page heading) | `task_add`, `task_update`, `task_show`; `board` argument | **Board**. The cards of a single project. Work in the sidebar shows the cards of every project in one place |
| A unit of work | Card | The tools say `task`; routes say `/api/cards` | **Card** in the app and the guides. A "task" in a tool name is a card |
| Where a card is in its life | Backlog, Todo, Doing, Review, Done (status list, Move menu, card face) | `status` with the values `backlog`, `todo`, `doing`, `review`, `done` | **Status**, in exactly these five words. Earlier screens said "Ready" and "Live" for statuses; those are now only the summary words above |
| A group of statuses on the board | Waiting on you, Active, In review, Shipped | None. Lanes are only a view | **Lane**. Never used for a status |
| A private chat | Bots (sidebar), New bot | `/api/bots` | **Bot**. A Bot is a chat, not a project and not a connected agent. Each project you can read has one |
| The AI that works a project | Coworker | `orchestrator` in config kinds, routes and some tool names | **Coworker** in everything you read. `orchestrator` stays only where a protocol or stored value needs it |
| An outside AI app with a token | Connected apps; "Any agent" when assigning a card | `agent` tokens, `/api/agents` | **Agent** for the app, **token** for its key |
| Things waiting on a person | Waiting on you (sidebar and lane) | `needs_me` | **Waiting on you**. The tool keeps its name |
| Saved words | Memory | `memory_save`, `memory_search` | **Memory** |

## If you read an older guide or a screenshot

- "Bots" in Admin, with project rows, now reads Projects.
- "Needs me" in a tool or a log is the Waiting on you lane.
- A card that said only "Ready" now says "Ready · Todo". "Building" is "Building · Doing", and "Live" is "Live · Done".
