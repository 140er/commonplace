# Kanban UI v2: polish pass, in the site's look

Same screens, data and behaviour as [v1](../kanban-opus/DECISIONS.md). The look now comes from the Commonplace site (PR #8, `landing/public/site.css`): the same tokens, mono type, square tiles on a 6px gap grid, orange blocks and terminal panels. `png/before-*` is v1, `png/after-*` is v2, at the same sizes. To re-render, run `./shot.sh`.

## Taken from the site

- **Tokens:** `--bg`, `--surface`, `--surface-2`, `--ink`, `--muted`, `--accent`, `--paper`, `--ok` and `--wait` are copied as they are. The app adds one quieter text step (`#80847b`, 4.9:1) for IDs and times, plus `--surface-3` for empty meter blocks.
- **Tiles, not boxes:** every region (sidebar, header, column, panel, log) is a flat tile, separated by the gap. There are no borders or rounded corners.
- **Big type, once per screen:** the page title is 32px bold with -0.05em tracking, like the site's headings. Everything else runs from 11 to 20px.
- **Orange blocks:** the review column header, the Decide count and the selected Decide row. The selected row opens in orange, like the site's expanded agent row. Its picked option turns black with orange text, like the site's `.btn.on-accent`.
- **Terminal panels:** the ask is shown as a recall hit: the verbatim question, then the stamp line in orange. The event log is a terminal (`time actor verb`), with comments in hit blocks. Review cards show their question in a small terminal block.
- **Block cursor and meters:** text fields use the site's orange block cursor. A claim is six small blocks, in the style of the site's toggles: green while healthy, amber in the last fifth.

## Removed (slop tells from v1)

| What | Why |
|---|---|
| 1px borders on every card, column, list and box | They made it look like a default dark dashboard. Depth now comes from surface steps and the gap. |
| Tinted, outlined review column; boxed backlog rail | Column headers are solid blocks with a big count. Only review is orange. |
| 2–3px rounded corners | The site is square. |
| Orange outlines: row accent bar, focus glow, ask box border, card focus ring, orange "old" ages | Too many loud things at once. Orange is now a fill for things that wait on Avery, never an outline. |
| Tinted pills with status dots | Status is a solid square chip, the same as the site's `automatic` blocks. |
| `⚠`, `●` and `✓` glyphs | They looked like emoji. Flags are a small amber `!` block, done items get a green square, and a reservation shows as the agent's name on a `todo` card. |
| Coloured dots before board names | They were decoration with no meaning. |
| The "live" dot, header search box and white New button next to a search field | Secondary chrome. Search sits in the sidebar with `⌘K`. New is one ink block with `c`. |
| `trusted` label, drawer IDs, "Done?" in orange on cards | Secondary labels. Trust appears once, in the ask's stamp line, where the site shows it too. |
| Hatched stripe between Decide groups | A gap does the same job. |
| The 2px-bottom key caps on every button | Number hints appear only on the selected row, as plain digits inside the button. |
| DejaVu Sans Mono fallback rendering | Geist Mono (OFL, in `fonts/`) is narrower and evener, so long agent names and IDs no longer turn into a wall of mono. Nothing loads from the network. |

## Kept calm for daily use

- Only one big, bold thing per screen: the page title. Card titles use weight 500 at 13px; metadata is 12px in the quiet step.
- Unselected Decide rows and board cards are plain surface tiles. Orange marks only what waits on Avery, plus the text cursor.
- The Done column is a dense one-line list with time on the right and no card chrome.
