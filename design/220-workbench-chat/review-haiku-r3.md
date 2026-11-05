# Card #220 · B + chat · review round 3 (Haiku)

- **Direction:** `B-chat/`, branch head `2676cf2ffc499f5d8f79d308bfa38088fa0f4d4f` (`design/220-workbench-chat`, "round 3, answer r2 reviews"). The folder matches the branch tree (143 files, byte for byte), except the mirrored `review-haiku-r1.md`, whose committed copy carries the disclosed path and tool-name edits.
- **Reviewer:** Haiku, non-gate review. Read-only: no design file edited, no subagents. Scratch renders and scripts were kept outside the design folder.
- **Read:** the review brief and its B + chat addendum, `B-CHAT-BRIEF.md`, `DESIGN-BRIEF.md`, `DIRECTIONS.md`, `README.md`, `RESPONSES-r1.md`, `RESPONSES-r2.md`, the Haiku and Terra reviews of rounds 1 and 2, B's round-4 reviews, `screens/wb.css` and `screens/wb.js` in full, and the chat markup by search. **Opened all 66 PNGs in `shots/`.**
- **Checks run:** tree and PNG-size checks; a pixel comparison with B round 4; contrast from the `:root` tokens; an independent colour-overlap scan against the local ev0lve theme folder; the recorded keyboard results (read, not re-run); chat-open probes at 1100×800, 1280×800 and 1366×768 rendered through a headless browser.

**Count: BLOCKING 0 · MAJOR 2 · MINOR 14.**

The path a first-timer takes is strong: open the chat, read the answer, follow a row to its card and back. Two things keep this from passing. The one recovery action on the "No answer yet" state leads nowhere useful, and several chat results that earlier rounds called fixed exist only in the script, so no screen or PNG shows them.

---

## 1. Are the earlier findings really fixed?

Status: **Fixed** = shown in a screen or PNG · **Script only** = works in `wb.js` and the keytest, but no screen shows it · **Partly** · **Open**.

### Round 1 (Haiku r1, Terra r1)

| Finding | Status | Evidence |
|---|---|---|
| Haiku M1: no chat control on Needs me or Memory | Fixed | `07`, `07b`, `08`, `09`, `09b`–`09e` carry the control (`08` names Harbor) |
| Haiku M2 and Terra MAJOR 1: a chat row only highlights | Fixed at 1440 · Script only at 1024 and 768 | `06f`, `06f2` match ID and title; tablet routes are in MAJOR 2 |
| Haiku M3: replies labelled "Atlas orchestrator" | Fixed for labels · claim not met | `06a`, `06c`, `09`, `10`, `10e` label the writer. RESPONSES-r1 says `06e` explains quick replies; it does not (MINOR 2) |
| Terra MAJOR 2: all mobile Show links go to A-134 | Fixed | `m11`, `m11b`, `m11c` open their own card and column; `m10` anchors `#row-A-134`, `#row-A-135`, `#row-A-118` |
| Haiku MINOR 1: linked card has no marker | Fixed on `06c`, `06f2`, `m11c` · claim not met on `11`, `11b`, `12` | A-118 is behind the palette or the shortcuts dialog in those frames (MINOR 2) |
| Haiku MINOR 2: outline sticks after focus leaves | Fixed | `wb.js` 229–232; keytest 28 |
| Haiku MINOR 3: recovery actions look like body text | Fixed | `06d` button is bordered; "Can't copy?" is underlined (`.btn.ghost.u`, `wb.css:613`) |
| Haiku MINOR 4: first screen repeats; chat opens by default | Fixed | `03` opens closed; keytest 40 |
| Haiku MINOR 5: mobile unread badge lost on Needs me | Fixed | `m1`, `m2` show "1 new"; `m11` shows none |
| Haiku MINOR 6: mobile ask paths undrawn | Fixed in r2 | `m10b`, `m10c` |
| Haiku MINOR 7: `aria-label` on a generic div | Fixed | `role="group"` with a labelled heading (`06c`, `m10`) |
| Haiku MINOR 8: tablet with chat hides two columns | Fixed at 1024 | jump bar in `r-06c-chat-1024` |
| Haiku MINOR 9: `/` does nothing on most screens | Fixed | keytest 38–39 cover the Needs me and Memory controls; Settings has no chat and the README says so |
| Haiku MINOR 10: README hex counts | Fixed | 36 colours here; no overlap with the theme folder (own scan, section 10) |
| Haiku MINOR 11: "Decide again →" | Renamed | `m7` reads "Approve or decline… →"; the arrow stays (MINOR 12) |
| Haiku MINOR 12: "1 new" has no rule | Fixed | `04` says "1 new reply"; the README gives the rule |
| Haiku MINOR 13: working ring is static | Fixed | `wb.css:617–618`; reduced motion stops it at `wb.css:39` |
| Haiku MINOR 14: "Show N more" under 44 px on phone | Fixed | `wb.css:619` |
| Terra MINOR 1: R only focuses Ask a question | Fixed | `wb.js` 113 clicks `#ask-question`; keytest 38 |
| Terra MINOR 2: mobile Back has no focus return | Fixed | `m10` Back goes to `m1-board.html#m-chat-tab`; `wb.js` 243–244 |
| Terra MINOR 3: white is shared with the theme | Fixed | `#FEFEFC` on every surface |

### Round 2 (Haiku r2, Terra r2)

| Finding | Status | Evidence |
|---|---|---|
| Haiku M1: at 700–1023 px the board is unusable | Fixed | `r-06c-chat-768`: modal sheet, dimmed, board inert; keytest 51–52 |
| Haiku M2: a board card chosen with the chat open is silent | Script only | the strip "You picked A-121…" exists in `wb.js` line 193 only (MAJOR 2) |
| Haiku M3: the phone ask path loses the approval | Fixed | `m10b`, `m10c` |
| Haiku M4: phone chat shows only the happy path; no phone Settings or Memory | Fixed · recovery target wrong | `m12`, `m12b`, `m12c`, `m13`; Memory tab removed. The error's Settings target is wrong (MAJOR 1) |
| Haiku M5: two phone behaviours, no rule; 390 px render blank | Fixed | one rule; `r-06c-chat-390` paints the sheet from the top; keytest 53 |
| Haiku MINOR 1: board cramped at 1100–1279 px | Fixed in CSS · not drawn | 188 / 304 rule (`wb.css:676–679`). My 1100 px probe reads (MINOR 4). A new clip appears at 1280 px (MINOR 1) |
| Haiku MINOR 2: new replies not announced; phone badge says only "new" | Fixed | `role="status"` line outside the toggle (`04` markup); badge reads "1 new" |
| Haiku MINOR 3: Needs me and Memory name one project | Partly, by design | README risk line |
| Haiku MINOR 4: shortcuts reference omits Esc and `]` | Fixed | `12` lists both; keytest 50 |
| Haiku MINOR 5: "Ask the orchestrator" promises a change | Fixed | `m8`: "This can't be recalled here. To follow up, ask the orchestrator." |
| Haiku MINOR 6: Settings "Chat answers" too heavy | Fixed | `10` has a "What quick replies are" disclosure |
| Haiku MINOR 7: empty chat copy is five sentences | Fixed | `06e` and `m12c`: two sentences and three chips |
| Haiku MINOR 8: memory error copy is internal | Fixed | `09d` |
| Haiku MINOR 9: theme colour count does not reproduce | Fixed | README gives the pattern; my scan agrees that nothing is shared |
| Haiku MINOR 10: two promised outcomes undrawn | Partly | "(adds it to Todo)" on `03b` and `m12`, not on `06d` or `m12b` (MINOR 7); the receipt is shown in `03d` |
| Terra MAJOR 1: Harbor composer says "Atlas" | Fixed | `08` markup: `label for="ci"` reads "Message to Harbor chat"; keytest 46 |
| Terra MINOR 1: mobile contextual ask only described | Fixed | `m10b`, `m10c` |
| Terra MINOR 2: chat from Needs me returns to the board | Script only | `m10-chat.html?from=needs` changes its label in `wb.js` 241; the drawn `m10` says "Back to board" (MAJOR 2) |

### What passed in B round 4 (must not get worse)

| Finding | Status | Evidence |
|---|---|---|
| Counts on `05b`, `06`, `11` (B r4 MINOR 1) | Fixed | `04` (4 / 3 / 2 / 14); `05b` (5, with 5 drawn); `06c` (6, with "Show 2 more"); `11` |
| Toasts hold buttons inside `role="status"` (B r4 MINOR 2) | Fixed | `05b` and `08`: the status span holds only the message; the button sits beside it |
| Desktop Stop has no picture (B r4 MINOR 3) | Fixed in README | README flow 5: the desktop "stopped" result shows the `m7` content |
| Single-key switch forgets its state (B r4 MINOR 4) | Fixed | `wb.js` 246–247; `12` shows "On" |
| "Orchestrator connected" pill (B r4 MINOR 6) | Changed by design | pill removed and stated as a trade-off; `03d`'s wording matches the header |
| Settings agent name (B r4 MINOR 7) | Fixed | `10` row reads "Atlas orchestrator (Maya's laptop)" |
| Developer words in two states (B r4 MINOR 8) | Fixed | `10b`, `09d` |
| 320 px project strip (B r4 MINOR 9) | Open, carried | MINOR 13 |
| Esc and focus in dialogs (B r4 fix) | Still passes | keytest lines 1–15 PASS |
| "Every action" in the palette (Terra r4 MINOR 1) | Fixed | `11` footer excludes Approve and Decline |

**Regression check.** A byte comparison with B round 4 differs for 44 of 45 shared shots, which tells nothing. A pixel comparison differs on nearly every screen, including `01-signup`, whose markup is identical to B round 4. The cause is the palette change (`#FFFFFF` → `#FEFEFC` in about 25 rules of `wb.css`, declared in the README). It is visually negligible. The other changes are the declared ones: the toggle replaces the pill, the phone tab bar drops Memory, Settings gains a disclosure, and the board gains a jump bar.

---

## 2. The brief's questions

1. **Chat easy to find, open and close (keyboard too), without hiding the board as the default.** Yes at 1440. The Chat control sits after the project name, with a text label, `aria-expanded` and a `/` hint (`04-board.html`). It opens from Enter, `/`, Needs me or Memory, and closes from Close chat, Esc in the composer, `]` or the toggle itself (`wb.js` 110–111, 119–168). The chat never opens by itself (`03`, `04`). At 1024–1099 px it pushes the board (`r-06c-chat-1024`). At 700–1023 px it is a dimmed, modal sheet over an inert board (`r-06c-chat-768`), so the board cannot be used while chatting. The README says so.
2. **Chat ↔ card link visible.** Yes at 1440: "From chat · 14:03" on new cards, "Linked in chat · 14:03" on A-118, the dashed outline from a focused row, the group "From this request · on the board now", and "Made from chat at 14:03" with the exact request in Details (`06c`, `06f`, `06f2`). Gaps: the time is clipped at 1280 px (MINOR 1), and the board-pick and tablet routes have no screen (MAJOR 2).
3. **Tablet and mobile.** 1024 px is readable with a jump bar (`r-06c-chat-1024`). 768 px is a sheet (`r-06c-chat-768`). 390 px uses the m-screens: a full-screen chat with a labelled back link (`m10`, `m12`). My 1100 px probe reads well; 1280 px clips a tag. The phone approval and ask screens read well (`m3`, `m10b`, `m10c`). Phone memory has no drawn route (MINOR 5).
4. **Not-connected, error and empty states.** All are drawn in plain words: `03` (three-step strip, chat closed), `03b` (waiting; offline with Try again), `03c`–`03e` (connect, waiting, failed with a checklist), `06d` and `m12b` (no answer), `06e` and `m12c` (empty: two sentences and three starter chips), `m12` (no agent, phone). The recovery from "No answer yet" leads nowhere (MAJOR 1).
5. **No ev0lve-theme visuals.** Yes. My scan finds 36 colours in the design and none shared with the local theme folder, which matches the README. No images, textures or theme fonts (the one font is Geist Mono, bundled locally).
6. **Nothing that passed got worse.** See section 1. No B screen became harder to finish, and the copy changes make screens clearer rather than thinner.
7. **Jargon for a first-timer.** No PAT, wing or claim appears. "Agent", "orchestrator" and "MCP" are explained in `01`, `02`, `03` and `03c`. Two terms are not explained where they appear: "Quick reply · local model" (MINOR 6).

---

## 3. BLOCKING

None. Every core flow can be finished. The recovery in MAJOR 1 still leaves "Ask again" and "Make it a card now".

---

## 4. MAJOR

### MAJOR 1 — "No answer yet" has a recovery action that leads nowhere

- **Screens:** `06d-chat-error.html` and `m12b-chat-error.html`. Targets: `10-settings.html` (desktop) and `m13-settings.html` (phone).
- **User problem:** The copy says the agent "may be closed or asleep" and that the message is kept. The options are Ask again, Make it a card now and Check the agent in Settings. On desktop, that last button is `<a class="btn sm" href="#">` (`06d`), so it goes nowhere. On the phone it opens `m13`, which shows the orchestrator as healthy: "Connected · Agent on 'Maya's laptop' · last active 2 min ago". `10` says the same. No screen says the agent is not answering, and none gives restart steps. The only restart checklist is in the connect dialog's failure state (`03e-connect-failed.html`: "No agent has connected in 10 minutes"), and nothing links to it. A first-timer is told the agent may be asleep, then shown a connected agent. Their only next step is to retry.
- **Fix:** (1) Point `06d` at `10-settings.html` and `m12b` at `m13-settings.html`. (2) Draw one Settings state for "connected but not answering since 14:02": an amber orchestrator card (`--wait`) with the three restart steps from `03e` and a "Check again" button. Link both error states to it. (3) Put "Show how to restart the agent" first in both error states, with Ask again second. Map the state in README flow 4.

### MAJOR 2 — Chat results that earlier rounds called fixed have no screen

- **Screens:** `04-board.html`, `06c-chat-answer.html`, `06f-chat-card-details.html`, `r-06c-chat-1024.png`, `r-06c-chat-768.png`, `m10-chat.html`.
- **Earlier finding:** Haiku r2 MAJOR 2, "a board card chosen with the chat open gives no visible result". RESPONSES-r2 calls it fixed: the card's Details replace the chat with the strip "You picked A-121 on the board. The chat is one click away." and a Back to chat button. The keytest checks this live (keytest 47–49). But the strip text is in `wb.js` line 193 only. No screen file contains it, and no PNG shows it. Someone who reads the PNGs sees the silent state that the reviewer flagged.
- **The same gap** covers two more paths the README describes. (a) A chat row opening its Details in the 1024 px pushed slot and in the 700–1023 px sheet (keytest 36 and 52). The frames show the chat only. (b) The Needs me Chat tab's "Back to Needs me" (README line 94; `m10-chat.html?from=needs`, label set by `wb.js` 241). The drawn `m10` says "Back to board". This breaks the brief's rule that every flow can be followed from the PNGs.
- **Fix:** Draw each path and map it in the README. `06g-chat-board-pick.html`: `06f` with A-121 selected and the strip, at 1440 px. `r-06f-chat-1024.png` (chat row → Details in the pushed slot) and `r-06f-chat-768.png` (the sheet). `m10-chat-from-needs.html` with "Back to Needs me" as static markup. Add all of them to `index.html` and to README flows 4 and 5.

---

## 5. MINOR

1. **1280 px: the chat ↔ card tag is clipped** (`06c-chat-answer.html`, `06f-chat-card-details.html`; probe at 1280×800). "From chat · 14:03" on A-134 and A-135 runs past the card edge, and the last digit is cut. The wrap rule (`.shell.chat-open .card-top { flex-wrap: wrap }`, `wb.css:678`) applies only at 1100–1279 px, and `.tag` is `white-space: nowrap` (`wb.css:126`). It fits at 1366 px and at 1440 px. **Fix:** apply the wrap rule from 1100 to 1399 px, and draw one 1280 px frame.
2. **Response claims that the screens do not show** (`06e-chat-empty.html`; RESPONSES-r1.md). RESPONSES-r1 says `06e` "explains it in one sentence" (Haiku M3). `06e` contains no mention of quick replies. RESPONSES-r1 also cites A-118's "Linked in chat" tag in `11`, `11b` and `12`, but in those frames the Doing column is behind the palette or the shortcuts dialog. **Fix:** add a sentence to `06e`, for example "Quick replies come from a model you choose in Settings; the orchestrator writes the rest." Show A-118 outside the dialog in `11`, or correct the response.
3. **The toggle's accessible name includes the count** (`04-board.html` and every opener): `<button class="chat-toggle">Chat<span class="ct-chip new">1 new reply</span><kbd>/</kbd></button>`. The chip sits inside the button, so the name changes with the count. README line 125 says "the button's name stays short". The live line outside the button is correct. **Fix:** move the chip out of the button into the status line, or correct the README.
4. **1100–1279 px has no drawn frame** (`wb.css:676–679`). The designer's 1100 px probe was "not kept". My probe at 1100×800 (`06c` state) reads: four columns, full tags, IDs intact. But the composer hint breaks mid-phrase ("Esc closes the / chat"), and the rail's ⌘/Ctrl K hint is clipped. **Fix:** keep an `r-06c-chat-1100.png` in `shots/` and `index.html`, and shorten the composer hint below 1280 px.
5. **Phone memory: the README's route is not drawn** (README line 151: "Phone users reach memory only through chat quotes and card details"). No phone screen shows a memory quote, and `m10` has none. The phone card views are the move/edit sheet (`m9`) and the board with a highlighted card (`m11`, `m11b`, `m11c`). The "Made from chat" quote exists only on desktop (`06f`). The phone's top-bar search opens the command palette (aria-label on `m1` and `m2`), which is not drawn for phones either. **Fix:** draw one phone card detail with the "Made from chat at 14:03" quote and one phone memory result, or correct the README sentence.
6. **Jargon at the point of use.** "Quick reply · local model" (`06a`, `06c`) has no explanation on its screen. The definition is in `10` behind "What quick replies are", and in the README. **Fix:** add one line under the first quick reply in `06a`: "A quick reply is written by the model you chose in Settings, not by the orchestrator."
7. **"Make it a card now" loses its destination in the error states.** `06d` and `m12b` say "Make it a card now"; `03b` and `m12` add "(adds it to Todo)". **Fix:** use the same label everywhere.
8. **The Details button does nothing in the live prototype.** `#details-btn` (`04-board.html`, `04c-board-tablet-panel.html`) has `aria-controls="ctx"` but no handler in `wb.js`. At 700–1099 px with the chat closed, a board tap updates a panel that is `display:none` (`wb.css:459`), so the only visible change is the outline. README line 91 ("still opens B's modal sheet") holds for the static `r2-04-board-1024-panel` only. `r-06c-chat-1024` also shows Details with the chat open and no stated result. This is a B-era gap, not a regression. **Fix:** wire the button, and state in the README what a tablet card tap does.
9. **README contradicts the layout rule.** Line 121 says the chat pane is "not modal on desktop or tablet". Line 91 and `wb.js` 122–128 make it a modal dialog below 1024 px. **Fix:** write "modal below 1024 px" in both places.
10. **A shortcut key wraps.** `12-shortcuts.html`: "G then B / N / M" breaks across two lines inside its `<kbd>`. **Fix:** `white-space: nowrap` on that key.
11. **The confirm panel covers the note field.** `07c-approve-confirm.html`: the confirm panel (`wb.css:492`) sits over the lower part of "Note to the orchestrator", and only its top edge shows. **Fix:** scroll the details so the field sits above the panel, or move the note above the "Goes outside" callout.
12. **The phone's primary action is ambiguous.** `m7-send-stopped.html`: "Approve or decline… →" in the bottom bar reads as navigation, and neither decision is on that screen. This is B r4 MINOR 5, renamed but not resolved. **Fix:** drop the arrow and link to `m3-needs-me-item.html`, where both buttons are visible.
13. **Carried: the 320 px project strip.** `r1-04-board-320.png`: Harbor's count is cut off, and Ledger and Field notes sit behind a sideways scroll with no cue (B r4 MINOR 9, untouched). **Fix:** wrap the strip, or add a "+2" control.
14. **Tapping outside the tablet chat does nothing.** At 700–1023 px the chat is a sheet over a dimmed `.chat-scrim` (`06a`, `06c`), but `wb.js` has no handler for it. Tablet users often dismiss a sheet by tapping the dimmed area, and only the Close chat button and Esc (which touch users lack) close it. **Fix:** close the chat when the scrim is tapped, and say so in the README.

---

## 6. Flow walk (actions excluding typing)

| Flow | Drawn path | Actions | Result |
|---|---|---|---|
| Start | `01` → `01b` → `02` → `03` → `03c` → `03c2` → `03d` | about 7, with the email link and the paste | Finishable; `03` teaches the orchestrator |
| Board | `04` | 0 | Column counts match the cards |
| Add card | `04` → `05` → `05b` | 2 | Finishable |
| Ask the orchestrator | `04` → `06a` → `06b` → `06c` | 2 (Chat, Send) | Finishable |
| See the card an answer made | `06c` → `06f` → Back to chat | 2 | Finishable at 1440; tablet not drawn (MAJOR 2) |
| Approve (desktop) | `04` → `07` → `07c` → `08` | 3, plus the 60-second hold | Finishable; labels are exact |
| Ask about an approval | `07` → R (`07d`) → Send → Back to the approval | 3 | Finishable |
| Find a memory | `09` → result → `09f` | 2–3, plus typing | Finishable |
| Settings | `04` → `10` → radio | 2 | Finishable |
| Connect an agent | `03` → `03c` → `03c2` → `03d` | 3 in the app, plus setup outside | Finishable |
| Error recovery | `06d` → Check the agent in Settings | 1 click to nothing | **Broken** (MAJOR 1) |
| Phone chat | `m1` → Chat → `m10` → Show → `m11` → Back to chat | 4 taps | Finishable |
| Phone approval | `m2` → `m3` → `m5` → `m6` → `m7` / `m8` | 4 | Finishable |
| Phone ask | `m3` → Ask a question → `m10b` → Back to the approval | 2, plus typing | Finishable |

---

## 7. Accessibility (WCAG 2.2 AA)

**Contrast, computed from the `:root` tokens (`wb.css:5–25`) with the WCAG formula.** Every text pair passes 4.5:1, and every control pair passes 3:1. The README's figures reproduce.

| Pair (where used) | Ratio |
|---|---|
| Body `--text` on surface (cards, chat) | 17.57 |
| Open toggle: surface on text fill (`wb.css:552`) | 17.57 |
| Subtitle `--text-2` on surface (chat head, "no orchestrator yet" chip) | 8.47 |
| Muted `--text-3` on well (column hints, IDs) | 5.02 |
| Muted `--text-3` on agent-bg (lowest chat text pair) | 5.01 |
| "1 new reply" chip, accent on accent-weak (`wb.css:555`) | 6.79 |
| "New since you last looked", accent on surface | 7.76 |
| "No answer yet" block text on block-bg (`.msg-state.err`, `wb.css:425`) | 6.38 |
| Agent tag `--agent` on agent-bg | 6.63 |
| Released / connected `--ok` on ok-bg | 6.51 |
| Phone tab, inactive `--text-2` on bg | 7.69 |
| Toggle border `--line-strong` on pane (non-text) | 3.91 |
| Control border on surface (non-text) | 4.05 |
| Dashed add and empty borders on well (non-text) | 3.48 |
| Focus ring (`wb.css:36`) on pane / on well | 7.50 / 6.67 |
| Dashed linked outline, accent on well (non-text) | 6.67 |
| Chip edge `#AEB8EC` on surface (decorative; the text carries the state) | 1.91 |

**Targets, from the CSS.** All are at least 24 px (2.5.8), and phone targets are at least 44 px where the brief asks. Toggle 32 px (`wb.css:550`). `.btn.sm`, 28 px, for Close chat and Back to chat (`wb.css:51`). Chat rows 36 px (`wb.css:211`), and 48 px on phone (`wb.css:597`). Desktop chips 26 px (`wb.css:152`), and 44 px on phone (`wb.css:658`). Quote remove 28 px (`wb.css:578`). Tablet column jump 32 px (`wb.css:624`). Phone column tabs 44 px (`wb.css:352`), tab bar 56 px (`wb.css:360`), Settings 44 px (`wb.css:653`), Back 44 px (`wb.css:376`), send 44×44 (`wb.css:603`), "Show N more" 44 px (`wb.css:619`).

**Other checks.**
- **Focus:** a 3 px accent outline on `:focus-visible` (`wb.css:36`). Opening the chat moves focus into the composer, and closing returns it to the opener (`wb.js` 141–160).
- **Names and roles:** composer labels per project (`label for="ci"`: "Message to Atlas chat" on `06c`, "Message to Harbor chat" on `08`); an `aside` with `aria-labelledby`; `role="log"` on the messages (`06c`); `role="alert"` on the failure states (`03b`, `06d`, `m12b`); `role="status"` on the working state (`06b`) and outside the toggle (`04`).
- **Keyboard:** I read the recorded results (58 lines, ALL PASS) and did not re-run them. The chat paths are covered (keytest 19–24, 47–50, 51–56). Not covered: the Details button and a tablet card tap with the chat closed (MINOR 8), and the paths in MAJOR 2.
- **Colour:** every state has words or an icon as well as colour ("1 new reply", "From chat", "Linked", "Blocked", "Released").
- **Motion:** the working ring spins (`wb.css:617–618`), and reduced motion stops it (`wb.css:39`).

---

## 8. Rules and premise

- **Board by default:** pass. The chat starts closed and never opens by itself (`03`, `04`).
- **One automatic orchestrator:** pass. `03` ("the first one becomes the orchestrator"), `03d`, `10`, `10c`, `10d`.
- **Needs me for human items only:** pass. The approval, review and decision in `07` are all things a person must do.
- **Memory verbatim with source:** pass. `09` and `09f` show the writer, the date and the exact words. Quick replies carry their own label.
- **Attention:** acceptable. `m12` is the densest screen: a waiting card and a connect callout sit under one message. Each carries its meaning in its first sentence.
- **Generic and synthetic:** pass. Names are synthetic (Maya, Northwind, Atlas, Harbor), addresses are example.com, "Maya's laptop" is a label, and the model ID is public. The connect dialog shows no key.
- **Premise:** B plus one chat slot behind a labelled control, as the owner asked. Still distinct from a generic dashboard.

---

## 9. What works

- The loop at 1440 is clear in both directions: answer → "From this request · on the board now" → row → Details with the exact request → Back to chat (`06c`, `06f`, `06f2`). It repeats on the phone with a same-row return (`m10` → `m11`, `m11b`, `m11c`).
- Writer labels are consistent across chat, memory and Settings, so a model's words never sound like the orchestrator's (`06c`, `09`, `10`, `10e`).
- The not-connected path teaches in place: the step strip (`03`), "Waiting for an agent" with "(adds it to Todo)" (`03b`), the connect dialog and its failure checklist (`03c`–`03e`), and "Connect an agent on a computer" (`m12`).
- Attention stays low: the chat is closed on first run, "1 new reply" counts only unread orchestrator or quick replies, and Needs me holds only human items.
- The phone ask paths keep the approval in view (`m10b`, `m10c`), and the sent-state wording is exact (`m7`, `m8`).
- The Harbor composer label is fixed (`08`).

---

## 10. Verification log

- **Branch:** `2676cf2f` exists. Its design tree (143 files) matches the folder byte for byte, except the disclosed `review-haiku-r1.md` copy.
- **Screens and shots:** 59 screens and 66 PNGs, and every PNG was opened. The required set is present (sign-up, project, empty board, board, add card, chat, Needs me, memory, settings, and the phone board and Needs me). Sizes match the brief: 1440×900 (1440×1180 for the full-page Settings captures), 1024×768, 768×1024, 390×844 at 2× (780×1688), and the 320 and 360 px reflows.
- **External resources:** none in `screens/` or `index.html` (no URL, `@import` or CDN).
- **Colours:** an independent scan of the design (`.css`, `.html`, `.js`, `.py`) finds 36 colours. The theme folder (all files, `.git` skipped) has 343. Shared: none.
- **Probes** (scratch copies outside the folder, rendered through a headless browser): chat open at 1100×800, 1280×800 and 1366×768. The 1280 clip and the 1100 readability come from these.
- **Pixel comparison with B round 4** (ImageMagick `compare`): differences on nearly every screen. Explained by the palette change (section 1).
- **Not done:** I did not re-run the keyboard test, and I did not test with a screen reader or on a real touch device.

Verdict: CHANGES
