# Memory map, look B + ctOS info

Look B (`../memory-map`) is unchanged here: same palette, fonts, frames, theme colours, layout and interactions. On top of it sits the information layer from the ctOS preview (PR #11), redrawn in look B's own tokens. `app.css` and `map.css` are copied from look B. `mix.css` holds only the additions. Open `index.html`; nothing loads from the network. `./shot.sh <ctOS folder>` re-renders `png/`, including the three-way comparison.

## What came over from ctOS

- **Profiler fields in the panel:** a type tag (Memory, Person, Card) as an ink block, an ID code such as `MEM-201C-545`, last seen, people or themes, and two 10-block meters: links (degree against the maximum, orange) and recent (age of the latest memory, green).
- **Header read-outs:** nodes, links, filter state (text, week, hidden themes) and a local clock in orange.
- **Hover:** the focused node's orange links draw out from it, then a light pulse runs along them on a loop. An ID tag with a leader line sits next to the node.
- **Signal strip:** the memories-per-week strip is drawn as stacked blocks, with a blinking orange marker and a lit/total read-out.

The cyan grid, scanlines, vignette, glitch and condensed font were left out. `prefers-reduced-motion` turns off the draw-in, pulse, blink and bar transitions.

## Renders

| File | State |
|---|---|
| `png/map-1440.png` | Map, nothing selected |
| `png/memory-1440.png` | A memory open |
| `png/hover-1440.png` | Hovering Mila, with the pulse and ID tag |
| `png/person-1440.png` | People layout, Sam open |
| `png/map-390.png`, `png/memory-390.png` | Phone: map and an open memory |
| `png/compare-b-ctos-mix-1440.png` | Look B, ctOS and mix, same memory open |
