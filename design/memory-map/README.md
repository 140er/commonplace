# Memory map

A static, working prototype of the Memory screen in Commonplace. Open `index.html`; nothing loads from the network. `./shot.sh` re-renders `png/`.

The idea comes from Mercury's Brain view (a force graph of notes). The look comes from the Commonplace site and kanban v2: `app.css` is copied from `design/kanban-opus-v2`, with the same tokens, Geist Mono, square tiles on a 6px gap grid, terminal panels and one orange.

## What's on the map

| On screen | Underneath |
|---|---|
| Memory (small square, theme colour) | a drawer, verbatim text |
| Theme (colour, legend, Themes layout) | a wing |
| Topic (`family / mila` in the panel) | a room |
| Person (ink square, name) | a knowledge-graph entity |
| Facts in a person's panel, with dates | knowledge-graph triples with `valid_from` / `valid_to`; ended facts stay, struck through |
| Card (`#145`) | a board card linked to a drawer |
| Links | mentions, card links, nearby memories in a topic, a few across themes, and facts between people |

UI text never says palace, wing, room or drawer. Sample data is in `data.js` (41 written memories plus generated filler, 329 in total).

## Interactions

- **Hover:** the node turns orange, its links go orange and everything else dims. Memories show their first lines in an orange block.
- **Click:** opens the panel. A memory shows its text verbatim with the stamp, then people, cards and linked memories. A person shows facts and memories. A card shows its linked memories. Clicking any row goes to that node.
- **Drag a node:** pins it, shown by a small orange corner. Double-click lets it go. Dragging the background pans the map.
- **Zoom:** scroll wheel, `+` / `-`, or the stack on the right. `0` or the fit button fits the map.
- **Filter:** `/` focuses the field. It matches text, people, themes and topics, and the orange number is the memory count.
- **Themes legend:** click a theme to hide or show it.
- **Layouts:** Map is a free force layout. Themes pulls each theme into its own cluster, labelled. People puts people on a ring with their memories near them.
- **When strip:** memories per week. Hovering a week lights those memories, and clicking keeps the week as a filter (on top of the text filter). The part of each bar that is lit on the map shows in orange. This is the only chart. It's built here in plain DOM and styled in look B. The mebius charts kit was used as a visual reference only.

On a phone, the sidebar becomes bottom tabs, the legend becomes a scrolling strip, and the panel opens as a sheet over the lower part of the map.

URL state (used by `shot.sh`): `?view=themes|people&open=<id>&hover=<id>&q=<text>&week=<n>&off=<theme,…>&pin=<id,…>`.

## Renders

| File | State |
|---|---|
| `png/map-1440.png` | Map, nothing selected |
| `png/hover-1440.png` | Hovering Mila |
| `png/memory-1440.png` | A memory open |
| `png/themes-1440.png` | Themes layout, hovering a memory |
| `png/people-1440.png` | People layout, Sam open |
| `png/filter-1440.png` | Filter `oma` |
| `png/week-1440.png` | One week kept as a filter |
| `png/map-390.png`, `png/memory-390.png` | Phone, map and open memory |
