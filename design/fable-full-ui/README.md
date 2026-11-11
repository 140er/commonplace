# Full-app design directions

Three complete directions for the whole product (chat, bots, project, Compute map, settings),
each for desktop and phone. Mock-ups on sample data only; the shipped app is untouched.

Open `index.html` through any static server and switch with `?ui=`:

| Switch | Direction | What is central |
|---|---|---|
| `?ui=a` | Contacts | The conversation. Bots are the navigation, like people in a messenger. |
| `?ui=b` | Deck | Decisions. Home is Today: what waits on you, cleared with one tap each. |
| `?ui=c` | Rooms | The project. A plain page per project with its bot always beside it. |

Screens: `#/chat`, `#/bots`, `#/project`, `#/compute`, `#/settings` (and `#/today` in b).

Screenshots: `bash design/fable-full-ui/shot.sh <output-dir>` writes every screen at 1440x900 and
at 390x844 (2x).

Files: `data.js` is the shared sample data, `kit.js` the shared icons and the topology drawing,
`a|b|c.js` and `a|b|c.css` one direction each. Fonts in `fonts/` are IBM Plex Sans, Inter and Geist,
all under the SIL Open Font License.
