# Kanban UI v2: polish pass

Same screens, data and behaviour as [v1](../kanban-opus/DECISIONS.md); only the look changed. `png/before-*` is v1, `png/after-*` is v2, same sizes. Render with `./shot.sh`.

## Removed

| What | Why |
|---|---|
| 1px borders on every card, column, list and box | Borders everywhere is the default dark-dashboard look. Depth now comes from four surface steps and a soft shadow on cards. |
| Boxed backlog rail and tinted, outlined review column | Columns sit on the canvas. Status icons in the headers (dashed, empty, half, orange dot, check) carry the column meaning. |
| Monospace for IDs, actors, times and quotes | It read as a wall of code. Geist for all UI, tabular figures for numbers. Mono stays only for `code` in card bodies. |
| Pill badges (`review`, `doing`) and the filled orange count badge | Generic. Status is an icon and a word; the Decide count is a plain orange number. |
| Orange outlines: selected row bar, focus glow, ask box border, card focus ring, old ages | Too many loud things competed. Orange is left on the Decide count, the review status icon, the picked option and the text caret. |
| `⚠` emoji, `●` reserved dot, `✓` glyphs | Emoji-ish. Replaced by drawn 14px icons; a reservation shows as the agent's name on a `todo` card, which no other `todo` card has. |
| Coloured dots before board names | Decoration with no meaning. One board icon, in grey. |
| Green "live" indicator, white "New" button, search box in the header | Secondary chrome. Search moved to the sidebar with `⌘K`; New is a `+` icon. |
| Key caps on every button and the 2px-bottom kbd style | Kept the digits only on the selected row, as faint text inside the button. |
| `trusted`, drawer IDs (`drawer_7f3a`), status pill and the "then" segmented control in the panel | Secondary labels. The return target is one line (`↩ claude-code:vm ⌄`); the other outcomes live in that menu. |
| Boxed log comments and timeline rail | Comments are plain text under the author line; one hairline separates the log from the card. |
| Hatched stripe between Decide groups | A 28px gap does the same job. |
| Claim hairline under `doing` cards | Became a 12px ring next to the held time: same meaning, reads at a glance, amber under 20% left. |

## Changed

- **Type scale** 11 / 12 / 13 / 14 / 20, **spacing scale** 4 / 8 / 12 / 16 / 20 / 24 / 32. Titles 13–14 at weight 450–500, panel title 20/28 at 600.
- **Text contrast:** meta text moved from `#7d8178` to `#8b8f86` (about 5.8:1 on the canvas). The darkest grey is used for icons only.
- **Font:** Geist and Geist Mono ship in `fonts/` (OFL, `fonts/OFL.txt`), with Inter and system fonts as fallback. Still nothing loads from the network.
- **Layout:** the main area is a raised sheet with rounded corners next to a darker sidebar, as in Linear and Things. The Done column is a quiet one-line list.
- **Optical alignment:** IDs sit in a fixed 36px gutter so titles, quotes and options share one left edge in Decide.
