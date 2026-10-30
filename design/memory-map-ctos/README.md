# Memory map, ctOS look

The look B Memory map (`../memory-map`) restyled as a ctOS-style city-OS HUD, to see if the vibe fits. It uses the same data (`data.js`), force layout, interactions and URL state. Open `index.html`; nothing loads from the network. `./shot.sh` re-renders `png/`.

## What changed from look B

- **Ground:** near-black, with a faint cyan grid that moves with the map (every fourth line brighter), scanlines and a soft vignette over the whole screen.
- **Line work:** tiles are hairline frames with corner brackets. Links are cyan-white hairlines.
- **Type:** labels are condensed uppercase (Liberation Sans Narrow). Readouts and memory text are in Geist Mono. Memory text keeps its own case, verbatim.
- **Nodes:** memories are small squares, people are white rings, cards are outlined tiles. The focused node gets brackets, a ping ring and a leader-line tag with its ID code.
- **Hover:** the node's links draw out from it in about 400 ms, then a scan pulse runs out along each link on a loop.
- **Profiler panel:** a Memory, Person or Card tag with an ID-style code (for example `MEM-201C-545`), a glyph, a name line, then Last seen, People/Themes and two 10-block meters: Links (degree against the maximum) and Recent (age of the latest memory). Person panels keep the facts with dates; ended facts are struck through. The panel opens with a short slice-and-flicker glitch.
- **HUD:** node count, link count, filter state (text, week, hidden themes) and a local clock in the header.
- **Signal:** the memories-per-week strip is a segmented meter with a blinking marker and a lit/total readout.
- **Colour:** theme colours are mixed 45% toward grey-cyan so cyan stays the loudest colour. Pins use the one hot red.
- **Reduced motion:** `prefers-reduced-motion` turns off the draw-in, pulse, ping, glitch, blink and bar transitions.

UI text says Memory, memories, themes and people only.

## Renders

| File | State |
|---|---|
| `png/map-1440.png` | Map, nothing selected |
| `png/memory-1440.png` | A memory open in the profiler |
| `png/hover-1440.png` | Hovering Mila, with the scan pulse |
| `png/person-1440.png` | People layout, Sam open |
| `png/map-390.png`, `png/memory-390.png` | Phone: map and the profiler as a sheet |
| `png/compare-b-vs-ctos-1440.png` | Look B (left) and ctOS (right), same memory open |

Fonts: Geist Mono (OFL, `fonts/OFL.txt`) and Liberation Sans Narrow, shipped unaltered (GPL-2 with the font exception, `fonts/LIBERATION-COPYRIGHT.txt`).
