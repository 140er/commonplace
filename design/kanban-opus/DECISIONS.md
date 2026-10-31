# Kanban UI (Opus): decisions

Static mockups for the boards in [docs/boards/DESIGN.md](../../docs/boards/DESIGN.md). No app code. Render with `./shot.sh` (headless Chrome); `build.py` writes `board.html` and `card.html` from one card list.

| Screen | File | PNG |
|---|---|---|
| Decide, with #145 open | `decide.html` | `png/decide-1440.png` |
| Board | `board.html` | `png/board-1440.png` |
| Card panel over the board (#142, doing) | `card.html` | `png/card-1440.png` |
| Decide on a phone | `mobile.html` | `png/decide-mobile-390.png` |

## Look

- **Look B, made into an app.** The site's near-black greens and its one orange. Orange means "waits on you": the Decide count, the `review` column, the selected row, the focus ring. Nothing else is orange.
- **Sans for what people wrote as titles, mono for everything the machine stamps or an agent said.** IDs, actors, times, keys, asks and comments are mono. Verbatim text sits in quotes or a log block, the way recall shows memories, so it reads as data, not UI.
- Hairline borders and 3px corners, no gradients, no glow. Density near Linear: 13px base, 30px nav rows, 28px controls.
- System fonts only, like the site. No web fonts, no third-party requests.

## Decide

- One list, oldest first. The two groups (asks, then agent-filed backlog) are split by a hatched gap, not a header.
- The selected row shows the key caps (`1`, `2`) and the reply field; other rows show their options without caps. `j`/`k` move the selection, so the caps never lie about which row a key hits.
- Age turns orange past an hour: oldest-first only helps if old looks old.
- Untrusted authors are muted (`grok:coordinator`); §9 flags are a yellow `⚠`. No "untrusted" label.
- The panel opens beside the list at 1440, so deciding and reading the context happen without leaving the queue.

## Board

- **Five columns, backlog as a 40px rail** with its count and a `⚠` when something in it is flagged (DESIGN.md: "narrow strip with only its count"). Agents never take from it, so it should not compete for width.
- `review` is the only tinted column, and its cards show the ask, so the board answers "what waits on me" without opening anything.
- **The claim is a 2px line under the card**: full when claimed, empty at expiry, yellow under 20% left. It shows "this agent may be stuck" without a timer or a label. People's holds (`sample`, blue) have no line, because they never expire.
- `●` before an actor in `todo` means reserved for that agent. Drawer icon + count is linked memories.
- `done` is quiet: no card chrome, a green check, the last 20.
- A focused card (keyboard) gets the orange ring; an open card gets a light border.

## Card panel

- Overlays the right 520px of either screen, board dimmed behind, as DESIGN.md says. Squeezing five columns beside a panel made every card two words wide.
- Order: status, title, holder and claim, body, (ask with buttons, when in `review`), linked drawers, event log, comment field. No section headings: each block has its own shape.
- In `review`, the "then" outcomes from API.md (`return`, `todo`, `done`, `backlog`) are one segmented control after `→`, first and selected: back to the agent that asked.
- Invalidated drawers are struck through, not labelled.
- The log is a timeline. Moves are one muted line; comments and handoff notes are verbatim blocks. Expiry has a yellow dot.

## Mobile

- Decide is the phone screen: two-up 40px option buttons, the reply field on the selected row, a three-tab bar with the count on Decide. Boards on a phone are a follow-up; deciding is what needs a thumb.

## Open

- The board header has no wing, members or filters. v1 has no labels or assignees beyond the holder, so there is nothing to filter yet.
- `New` (`c`) and the `+` on `todo` need a create form; not drawn.
