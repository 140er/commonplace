# Card #220 · B + chat · review round 4 (Haiku)

- **Direction:** `B-chat/`, branch head `2b6e28ddf9ee796b5bc2237d7e5cf7ccdeeabe1e` (`design/220-workbench-chat`, "round 4, answer r3 reviews"). The folder matches the branch tree (162 files, byte for byte) except the two sanitized mirror copies of earlier reviews (`review-haiku-r1.md`, `review-haiku-r3.md`).
- **Reviewer:** Haiku, non-gate review. Read-only: no design file edited, no subagents. Scratch scripts and copies stayed outside the design folder.
- **Read:** the review brief and its B + chat addendum, `B-CHAT-BRIEF.md`, `DESIGN-BRIEF.md`, `DIRECTIONS.md`, `README.md`, `RESPONSES-r1.md` to `RESPONSES-r3.md`, all six earlier reviews (Haiku and Terra, rounds 1–3), `screens/wb.css` and `screens/wb.js` in full, and every screen's chat markup by extraction. **Opened all 76 PNGs in `shots/`.**
- **Checks run:** tree, leak and PNG-size checks; `_src/hexcheck.py` re-run, plus `rgb()`, `url()` and gradient scans; contrast computed from the `:root` tokens; a state scan of all 65 screens; the keytest record (65 PASS lines, not re-run); a diff against B's screens.

**Count: BLOCKING 0 · MAJOR 3 · MINOR 9.**

The first-timer path at 1440 px is clear, and most round-3 fixes are real: the no-answer recovery now has a route, the chat-row and board-pick Details are drawn, the Harbor composer label is right, and the confirm no longer covers the note. Three things keep this from passing: the "not answering" state contradicts itself across three screens, the desktop chat drawn at 390 px overlaps its own text, and a row for a quick-reply card opens with the orchestrator's 14:03 attribution.

---

## 1. Earlier findings: are they really fixed?

Status: **Fixed** = shown in a PNG or screen and checked · **Script only** = works in `wb.js`, not drawn · **Partly** · **Open**.

### Round 1 (Haiku r1, Terra r1)

| Finding | Status | Evidence |
|---|---|---|
| Haiku M1: Needs me and Memory had no chat control | Fixed | `07`, `07c`, `08`, `09`, `09f` show the project-named toggle; `/` works there |
| Haiku M2 and Terra M1: a chat row only highlighted | Fixed for rows; new defect (MAJOR 3) | `06f`, `06f2`, `r-06f-chat-1024`, `r-06f-chat-768`. The A-121 quick-reply row is wrong |
| Haiku M3: replies labelled "Atlas orchestrator" | Fixed | `06a` and `06c` name "Quick reply · local model" and the orchestrator separately; `09`, `10`, `10e` repeat it |
| Terra M2: mobile Show links all led to A-134 | Fixed | `m11`, `m11b`, `m11c` each open their own card; `m10` anchors the rows |
| Haiku MINOR 1, 7: linked-card marker, group role | Fixed | "Linked in chat · 14:03" on A-118 (`06c`, `06f2`, `m11c`); card lists are `role="group"` |
| Haiku MINOR 2: outline stuck after focus left | Fixed | `wb.js:253–261`; keytest "outline follows focus and pointer" |
| Haiku MINOR 3: recovery actions looked like body text | Fixed | `06d` button is bordered; "Can't copy?" underlined (`wb.css:613`) |
| Haiku MINOR 4, 5, 6: chat open by default; mobile badge; undrawn mobile ask | Fixed | `03` opens closed; `m1`, `m2` show "1 new"; `m10b`, `m10c` drawn in r2 |
| Haiku MINOR 8, 9: tablet columns; `/` on most screens | Fixed | Jump bar in `r-06c-chat-1024`; `/` works on the chat screens, Settings has none |
| Haiku MINOR 10: hex counts | Fixed | Re-run: 36 / 336 / none shared |
| Haiku MINOR 11, 12: "Decide again"; "1 new" with no rule | Renamed / fixed | `m7` now reads "Approve or decline" (still a navigation: MINOR 6); desktop chip reads "1 new reply" |
| Haiku MINOR 13, 14: static ring; 44 px "Show N more" | Fixed | `wb.css:617–619` |
| Terra MINOR 1–3: R key; mobile Back focus; white shared | Fixed | `wb.js:113`; `wb.js:281–282`; no `#fff` in the design |

### Round 2 (Haiku r2, Terra r2)

| Finding | Status | Evidence |
|---|---|---|
| Haiku M1: board unusable at 700–1023 px | Fixed | `r-06c-chat-768`: modal sheet, board dimmed and inert |
| Haiku M2: board card chosen with chat open gave no result | Fixed | `06g`; `wb.js:203–209` |
| Haiku M3: phone ask path lost the approval | Fixed | `m10b`, `m10c` keep the approval strip and a way back |
| Haiku M4: phone chat showed only the happy path | Fixed | `m12`, `m12b`, `m12c`, `m13`. Memory tab removed (MINOR 7) |
| Haiku M5: two phone behaviours; blank 390 render | Rule fixed; render now overlaps | README line 94; `r-06c-chat-390` (MAJOR 2) |
| Haiku MINOR 1: board cramped at 1100–1279 px | Fixed for columns; rail hint still overflows | `r-06c-chat-1100` (MINOR 1) |
| Haiku MINOR 2: replies not announced; badge said only "new" | Fixed | `role="status"` on `04` and on the phone tab bar |
| Haiku MINOR 3: Needs me and Memory name one project | Declared trade-off | README "Open risks" |
| Haiku MINOR 4, 5, 6, 8, 9: shortcut list; "Ask the orchestrator"; Settings length; memory error copy; palette count | Fixed | `12` (Esc and `]` rows); `m8`; `10` disclosure; `09d`; README lines 139–146 |
| Haiku MINOR 7: empty chat copy | Partly | `06e` has three sentences, not two (MINOR 9) |
| Haiku MINOR 10: promised outcomes undrawn | Partly | "Make it a card now" outcome and the Basic receipt are not drawn (MINOR 5) |
| Terra M1: Harbor composer labelled "Atlas" | Fixed | `08` and `m10h`: label, heading and log agree (keytest) |
| Terra MINOR 1: mobile contextual ask only described | Fixed | `m10b`, `m10c` |
| Terra MINOR 2: chat from Needs me returned to the board | Fixed | `m10-chat-from-needs` has "Back to Needs me" |

### Round 3 (Haiku r3, Terra r3)

| Finding | Status | Evidence |
|---|---|---|
| Haiku MAJOR 1: "No answer yet" recovery led nowhere | Route fixed; state contradicts itself | `06d` and `m12b` → `10f` and `m13b`, both back to the chat. Contradictions: MAJOR 1 |
| Haiku MAJOR 2: chat results with no screen | Fixed | `06g`, `r-06f-chat-1024`, `r-06f-chat-768`, `m10-chat-from-needs`. Settings-from-Needs return is script only (MINOR 4) |
| Haiku MINOR 1: 1280 px tag clipped | Fixed | `r-06c-chat-1280`: tags wrap, none clipped |
| Haiku MINOR 2: "06e explains quick replies" | Fixed | `06e` names quick replies. The 11 / 11b / 12 correction is accepted |
| Haiku MINOR 3: toggle name includes the count | Accepted | README says so; the name still starts with "Chat" |
| Haiku MINOR 4: 1100 frame and composer hint | Partly | `r-06c-chat-1100` drawn; composer hint wraps cleanly; rail hint still overflows (MINOR 1) |
| Haiku MINOR 5: phone memory route | Partly | README now says none; the phone search icon still opens the palette (MINOR 7) |
| Haiku MINOR 6, 7: quick-reply explanation; "adds it to Todo" | Fixed | `06a`, `06c`; `06d`, `m12b` |
| Haiku MINOR 8: Details button did nothing | Fixed for the button | `wb.js:218–239`; Hide on chat-opened Details is still dead (MINOR 2) |
| Haiku MINOR 9: README modal wording | Fixed | README line 123 |
| Haiku MINOR 10: shortcut key wrapped | Fixed | `wb.css:692`; `12` shows "G then B / N / M" on one line |
| Haiku MINOR 11: confirm covered the note | Fixed | `07c` shows the note above the confirm; keytest |
| Haiku MINOR 12: `m7` primary action ambiguous | Partly | Arrow removed; still a navigation (MINOR 6) |
| Haiku MINOR 13: 320 px project strip | Fixed | `r1-04-board-320` shows all four projects |
| Haiku MINOR 14: tap outside tablet chat did nothing | Fixed | `wb.js:212–216`; keytest at 768 px |
| Terra MAJOR 1: phone Chat tab did not name its destination | Fixed | `m2`, `m2b`, `m10h` name the project; the choice depends on hidden state (MINOR 8) |
| Terra MINOR 1: Settings lost its origin | Partly | From the error, `m13b` → `m12b` is drawn; from Needs me it is script only (MINOR 4) |
| Terra MINOR 2: phone unread update silent | Fixed | `role="status"` on the phone tab bar (visually hidden) |

### B round 4 items, which must not get worse

- Counts (`04`: 4 / 3 / 2 / 14; `05b`: Todo 5; `06c`: Todo 6): unchanged.
- Toast with Undo (`05b`) and the held send (`08`, `m6`): unchanged.
- Single-key switch shows its state (`12`: "On"): unchanged.
- Settings agent name "(Maya's laptop)" (`10`): unchanged.
- Developer wording removed (`09d`, `10b`): unchanged.
- 320 px project strip (`r1-04-board-320`): fixed.
- Palette footer excludes Approve and Decline (`11`): unchanged.
- Dialog Esc and focus return: keytest passes.
- The "Orchestrator connected" pill is gone from the board header: a declared trade-off, accepted.
- The phone bar lost **Memory**. B's passed bar had Board, Chat, Needs me and Memory; B+chat has three tabs (MINOR 7).

B's onboarding screens `01`, `01b` and `01c` are byte-identical to B. As far as I checked by diff, the other shared screens differ only in the toggle, labels, ids and the declared fixes.

---

## 2. The brief's questions

1. **Chat easy to find, open and close (keyboard too), board still the default.** Yes at 1440 px. The control is labelled and sits after the project name (`04`). It opens with Enter, `/`, or from Needs me and Memory, and closes with Close chat, Esc in the box, `]`, or the control itself. Focus goes into the box on open and back to the control on close (`wb.js:141–160`). The board is the default, and the chat starts closed (`03`, `04`).
2. **Chat ↔ card link visible.** Yes at 1440 px: "From chat · 14:03" tags, a dashed outline on the card the focused row points to, and "From this request · on the board now" (`06c`), with the exact request in Details (`06f`, `06f2`). The same link is drawn at 1024 and 768 px and on the phone (`m10` → `m11`, `m11b`, `m11c`). The exception is the quick-reply row (MAJOR 3).
3. **Tablet and mobile.** 1024 px pushes the board, with a jump bar (`r-06c-chat-1024`). 1100 px reads well, but the rail hint overflows (MINOR 1). 768 px is a dimmed, inert sheet with a way back (`r-06c-chat-768`). The phone uses the `m` screens, which are clear (`m10`, `m10b`, `m10c`, `m12`). The desktop markup at 390 px overlaps its own text (MAJOR 2).
4. **Not-connected, error and empty states.** All are drawn: `03`, `03b`, `03c`–`03e` and `m12` (no agent); `06d`, `m12b`, `10f`, `m13b` (no answer); `06e`, `m12c` (empty). The recovery route works. The copy contradicts itself (MAJOR 1).
5. **No ev0lve-theme visuals.** Yes. 36 colours in 70 design files, 336 in the 93-file theme folder, none shared. No `url()`, gradient, image or `@import`. The one font is local Geist Mono (OFL), not the theme's Inter or JetBrains Mono.
6. **Nothing that passed got worse.** Almost. The phone lost the Memory tab (MINOR 7). The Hide and `]` claims are not implemented (MINOR 2, mostly from B). Everything else I checked is unchanged or a declared fix.
7. **Jargon.** Agent, orchestrator and MCP are explained (`01`, `03`, `03c`). "Quick reply" is explained on `06a` and `10`. "PAT", "wing" and "claim" do not appear.

## 3. BLOCKING

None. Every core flow can be finished from the PNGs. The no-answer path ends in drawn actions (Ask again, Make it a card now), and the contradictions do not stop the user from sending again.

## 4. MAJOR

### MAJOR 1 — The "not answering" state contradicts itself on three screens

- **Screens:** `screens/06d-chat-error.html` (chat header, line 132, "Orchestrator connected"; body "…was last active at 14:03; the app may be closed or the computer asleep"). `screens/m12b-chat-error.html` (header, line 41, same claim). `screens/10f-settings-not-answering.html` (amber card, line 31: "Not answering since 14:02 · connected · last active 14:03"; Agents table, line 54: "Atlas orchestrator (Maya's laptop) … Last active **2 min ago**").
- **User problem:** This is the state a first-timer reaches when a message gets no answer. The chat header above "No answer yet." reads as healthy. The copy says the agent was active at 14:03 and may also be asleep. The Settings page that explains the failure gives two last-active times for the same agent, one of them "2 min ago". A first-timer cannot tell whether the agent is running, so the restart steps look doubtful. This fails the brief's clarity rule on the screen written to explain the failure. (`m13b` says "last active 14:03" throughout, so it is the one to match.)
- **Fix:** One set of facts on all three screens. (1) In `06d` and `m12b`, change the chat subtitle to the state, for example "Connected, not answering since 14:02". (2) In `10f`, change the table row to "Last active 14:03", as in the amber card and `m13b`. (3) Rewrite the body copy to the facts, for example: "It read your message at 14:02 and has not answered. Its agent was last active at 14:03. Check that the agent app is open." Drop "the app may be closed or the computer asleep" unless a screen shows it.

### MAJOR 2 — The desktop chat drawn at 390 px overlaps its own text

- **Screen:** `shots/r-06c-chat-390.png`, rendered from `screens/06c-chat-answer.html` at 390×844 @2x. The composer box covers the last message ("When all three are done I'll ask you to approve…"), and the hint row ("Enter to send · Shift+Enter new line", "Esc closes the chat") is printed over the message text.
- **User problem:** README line 94 says the desktop markup at phone widths "is never stacked under the board" and cites this shot as proof. The shot shows the chat's own last message unreadable under the composer. The brief asks for overlap to be fixed before a screen ships.
- **Cause (CSS):** `wb.css:480`, inside the ≤699 px block, sets `.ctx-body { overflow: visible; }`. That turns off the scroll container of the chat log (`06c` line 134: `role="log"` on `.ctx-body`; base rule `wb.css:162`, `overflow: auto`). The sheet is `height: 100vh; overflow: hidden` (`wb.css:559`, `:645`), so the unscrolled log runs under the composer (`06c` line 147).
- **Fix:** Keep `overflow: auto` on the chat log at phone width, by scoping the rule so it does not apply to `#chat`. Hide the keyboard hint below 700 px. Re-render `r-06c-chat-390` and check that the last message ends above the composer. Or remove the desktop-markup claim and this shot, and rely on the `m` screens, which are correct.

### MAJOR 3 — A row for a quick-reply card opens with the orchestrator's 14:03 attribution

- **Screen:** `screens/06a-chat-open.html` line 141. "Cards in this answer" sits inside the 12:41 **Quick reply · local model** and lists A-121. The same row appears in `06b`, `06d`, `06f`, `06f2` and `06g`. Selecting it runs `showDetails(id, row)` (`wb.js:188–193`; click handler `wb.js:203–205`), and the strip reads "Opened from Atlas chat, the 14:03 answer." That text is hard-coded in `wb.js:193` for every chat row.
- **User problem:** A user who selects A-121 from the quick reply is told the card came from "the 14:03 answer", which is the orchestrator's message. The quick reply was written by a model, and the design promises it is never shown under the orchestrator's name (README flow 6; "Quick replies vs the orchestrator"). The keytest exercises the A-134 and A-118 rows, never the A-121 quick-reply row. This is MAJOR because it misstates provenance on a path that `06a` itself promises ("select one to open its details"). It is live only, so no PNG shows it.
- **Fix:** Build the strip from the row's own message (writer and time, for example "Opened from Quick reply · local model, 12:41"), taking the text from a data attribute on the row rather than the string in `wb.js:193`. Add a keytest for the `06a` A-121 row, and draw that state as a `06f`-style screen so it is visible in a PNG.

## 5. MINOR

1. **The ⌘K search hint overflows at 1100 px, and the header wraps at 1024–1280 px** (`shots/r-06c-chat-1100.png`, `r-06c-chat-1280.png`, `r2-04-board-1024.png`; `wb.css:676–679`). In a zoomed crop of the rail, "⌘/Ctrl" fits, but the "K" spills past the input's right border. Round 3 MINOR 4 asked for this; the response fixed only the composer hint. Below 1280 px the header also wraps and "Add card" drops to a second row. Fix: keep the kbd on one line with a shorter label below 1280 px, and keep "Add card" on the title row (for example, icon-only Settings below 1280 px).
2. **"Hide" and `]` do not do what the design says** (`screens/06f-chat-card-details.html`, `06f2`, `06g`; `shots/r-06f-chat-1024.png`, `r-06f-chat-768.png`; `wb.js:218–239`, `wb.js:111`; `screens/12-shortcuts.html`). Hide (with `aria-keyshortcuts="]"`) acts only when the tablet sheet is `panel-open`. On a chat-opened Details at 1440 px or 700–1099 px it does nothing. `]` only closes the chat, but `12-shortcuts` and README line 88 say "close the chat, or hide the side panel". The dead button predates this round. Fix: Hide on a chat-opened Details returns to the chat, as Back to chat does. Implement "hide the side panel" for `]`, or drop that half of the claim.
3. **The phone tab-bar status text is not state-specific** (`screens/m12-chat-no-agent.html`, `m12c-chat-empty.html`, `m10-chat.html`). Each carries `<span class="sr" role="status">1 new reply in Atlas chat</span>`. The no-agent state says "No orchestrator yet", and the empty state has no messages, so the count is wrong there. It is visually hidden but present. Fix: derive the status from the same data as the chat header, with no count when there is no orchestrator or the chat is empty.
4. **"Back to Needs me" from Settings is script only** (`screens/m13-settings.html`; `wb.js:275–277`). The Settings links on `m2`, `m2b`, `m10-chat-from-needs` and `m10h` point to `m13-settings.html?from=needs`, but the only drawn Settings sheet says "Back to board". Round 3 Terra MINOR 1 therefore holds only in the keytest. Fix: draw the from-Needs variant with "Back to Needs me" and focus on Settings, and name it in README flow 5.
5. **Recovery actions have no drawn result.** "Check again" (`10f`, `m13b`), "Ask again" and "Make it a card now" (`06d`, `m12b`): a first-timer presses them, and no frame shows what follows. Fix: draw one result for "Check again" (still not answering, or delivered) and one for "Ask again", or state in the README that these outcomes are not drawn.
6. **The stopped-state primary action is a navigation** (`screens/m7-send-stopped.html`, bottom bar). "Approve or decline" links to `m3`, beside "← Needs me". The arrow is gone (round 3 MINOR 12), but the label still reads as an action. Fix: label it "Open the approval", or put both decisions in the bar, as `m3` does.
7. **Phone memory: a B tab removed, and README line 153 is contradicted.** B's passed phone bar had Board, Chat, Needs me and Memory (`B-workbench/screens/m1-board.html`); B+chat has three tabs. The brief asks to keep everything else as B had it (item 7). README line 153 says "there is no phone memory search", but the phone search icon (`screens/m1-board.html`, aria-label "Search or do (command palette)") opens the palette, and the desktop palette offers "Search memory for…" (`screens/11-command-palette.html`). Fix: keep a phone route to memory (a fourth tab, or memory search in the phone palette, drawn once), or correct line 153 and list the removal under "What I traded away".
8. **Phone "Atlas chat" on All projects depends on hidden state** (`screens/m2-needs-me.html`, `m2b-needs-me-harbor.html`). The tab names one of two projects shown on the list, and the choice comes from "the item you last opened, else the last project". A user who wants Harbor's chat must first open the Harbor decision, and nothing on `m2` says so. Fix: a visible project choice on the Needs me header, or one line on `m2`: "Chat for the project of the item you last opened".
9. **The empty-chat copy has three sentences, not the two the response claims** (`screens/06e-chat-empty.html`; RESPONSES-r2, Haiku MINOR 7). The box reads "Write what you need." / "The orchestrator answers here and turns the work into cards marked 'From chat' on the board." / "Short 'Quick reply' answers come from the model chosen in Settings." Fix: correct the response, or cut the third sentence, since `06a` already explains quick replies.

## 6. Flow walk (actions excluding typing)

| Flow | Drawn path | Actions | Result |
|---|---|---|---|
| Start | `01` → `01b` → `02` → `03` → `03c` → `03c2` → `03d` | about 7, incl. the email link | Finishable; `03` teaches the orchestrator |
| Board | `04` | 0 | Counts match the cards (4 / 3 / 2 / 14) |
| Add card | `04` → `05` → `05b` | 2 | Finishable |
| Ask the orchestrator | `04` → `06a` → `06b` → `06c` | 2 (Chat, Send) | Finishable |
| See the cards an answer made | `06c` → `06f` → Back to chat | 2 | Finishable at 1440; drawn at 1024 and 768 |
| Pick a board card while chatting | `06c` → `06g` | 1 | Finishable |
| Quick-reply row | `06a` → A-121 row | 1 | Wrong attribution (MAJOR 3) |
| No answer | `06d` → `10f` → Back to the chat | 3 | Route works; state contradicts (MAJOR 1); "Ask again" undrawn (MINOR 5) |
| Approve | `04` → `07` → `07c` → `08` | 3, plus the 60-second hold | Finishable; labels exact |
| Ask about an approval | `07` → `07d` → Back to the approval | 2 (+ typing) | Finishable |
| Find a memory | `09` → `09f` | 2 (+ typing) | Finishable |
| Settings | `04` → `10` → radio | 2 | Finishable |
| Connect an agent | `03` → `03c` → `03c2` → `03d` | 3 in the app, plus setup outside | Finishable |
| Phone chat | `m1` → Chat → `m10` → Show → `m11` → Back to chat | 4 taps | Finishable |
| Phone approval | `m2` → `m3` → `m5` → `m6` → `m7` / `m8` | 4 | Finishable |
| Phone ask | `m3` → Ask → `m10b` → Back to the approval | 2 (+ typing) | Finishable |
| Phone no answer | `m12b` → `m13b` → Back to the chat | 3 | Route works; state contradicts (MAJOR 1) |
| Phone Settings from Needs me | `m2` → Settings → `m13` | 2 | Return label not drawn (MINOR 4) |
| Phone memory | none | — | No route (MINOR 7) |

## 7. Accessibility (WCAG 2.2 AA)

**Contrast.** Computed from the `:root` tokens (`wb.css:5–25`) with the WCAG relative-luminance formula. Every README figure reproduces.

| Pair (where used) | Ratio |
|---|---|
| `--text` on `--surface` (body, chat text) | 17.57 |
| `--surface` on `--text` (open toggle, `wb.css:552`) | 17.57 |
| `--text-2` on `--surface` (subtitle, "no orchestrator yet" chip) | 8.47 |
| `--text-3` on `--surface` / `--pane` / `--well` / `--agent-bg` (times, IDs, hints) | 5.84 / 5.65 / 5.02 / 5.01 |
| accent on accent-weak ("1 new reply" chip, `wb.css:555`) | 6.79 |
| accent on surface ("New since you last looked") | 7.76 |
| surface on accent (primary buttons) | 7.76 |
| `--block` on `--block-bg` ("No answer yet", `wb.css:425`) | 6.38 |
| `--wait` on `--wait-bg` ("Not answering since", `10f`) | 7.15 |
| `--ok` on `--ok-bg`; `--agent` on `--agent-bg` | 6.51; 6.63 |
| `--text` on accent-weak (mobile "Showing…" bar) | 15.36 |
| `--text` on `--pane` (context strip) | 16.97 |
| `--line-strong` on surface / pane / well (control borders) | 4.05 / 3.91 / 3.48 |
| focus ring `--accent` on pane / well (`wb.css:36`) | 7.50 / 6.67 |
| `#AEB8EC` chip edge on surface (decorative) | 1.91 |

Text pairs that carry meaning are all at least 4.5:1. Control boundaries are at least 3:1, except the dashed "add" and "empty" borders at 3.48:1, where the label carries the meaning. The chip edge is decorative.

**Targets (CSS).** Toggle 32 px (`wb.css:550`); `.btn.sm` 28 px (`:51`); chat rows 36 px (`:211`), 48 px on phone (`:597`, `:673`); desktop chips 26 px (`:152`), phone chips 44 px (`:658`); column jump 32 px (`:624`); quote remove 28 px (`:578`); phone tabs 56 px (`:360`); phone column tabs 44 px (`:352`); phone Settings 44 px (`:653`); phone Back 44 px (`:376`); send 44×44 (`:603`); Show N more 44 px (`:619`). I found no interactive target below 24 px, and none below 44 px where the brief asks.

**Other checks.** Focus: 3 px accent outline (`wb.css:36`). The box takes focus on open and the opener on close (`wb.js:141–160`). Names: a visually hidden composer label per project (`06c` line 147); `role="log"`; `role="status"` on the working state (`06b`); `role="alert"` on no answer (`06d`). Motion: the ring spins (`wb.css:617–618`), and reduced motion stops it (`:39`). Colour: every state has words or an icon. Keyboard: the record has 65 PASS lines. Not covered: the A-121 quick-reply row (MAJOR 3) and the chat-origin Hide (MINOR 2).

## 8. Rules and premise

- **Board by default:** pass. The chat starts closed (`03`, `04`).
- **One automatic orchestrator:** pass (`03d`, `10`, `10c`, `10d`, `10f`).
- **Needs me for human items only:** pass (`07`: approval, review and decision).
- **Memory verbatim with source:** pass for memory results (`09` and `09f` show writer, source, time and exact words). MAJOR 3 is a provenance error on a chat row, not a memory result.
- **Attention:** acceptable. "1 new reply" appears only when something is new (`04`, `05b`), and "New since you last looked" marks the new message (`06a`).
- **Generic and synthetic:** pass. Names are synthetic (Maya, Northwind, Atlas, Harbor, Iris, Otto, Juno, Leon). Addresses use example.com. The model ID is public. The connect dialog masks the key (`03c`).
- **Premise:** B plus one chat slot behind one labelled control, as the owner asked on 5 Nov. The chat-to-card link is visible and drawn (`06c`, `06f`, `06f2`, `m11`–`m11c`).

## 9. What works

- The loop at 1440 px is clear in both directions: answer → "From this request · on the board now" → row → Details with the exact request → Back to chat (`06c`, `06f`, `06f2`). It holds at 1024 px (pushed), 768 px (sheet) and on the phone (`m10` → `m11`, `m11b`, `m11c`).
- Writer labels are consistent across chat, memory and Settings.
- The not-connected path teaches in place: the three-step strip (`03`), waiting and offline (`03b`), the connect dialog and its failure checklist (`03c`–`03e`), and the phone callout (`m12`).
- The approval path is strong at both sizes: exact words, the 60-second hold, Stop, and a confirm with the note visible (`07c`; `m5`, `m6`, `m7`).
- Phone ask paths keep the approval in view (`m10b`, `m10c`). The send-hold copy says what was and was not sent (`m7`, `m8`).
- The Harbor chat names its project everywhere it appears (`08`, `m10h`).

## 10. Verification log

- **Branch:** `2b6e28dd` exists (commit "round 4, answer r3 reviews"). A `git archive` of `design/220-workbench-chat` has 162 files; only `review-haiku-r1.md` and `review-haiku-r3.md` differ from the folder, and those are the sanitized copies. The branch tree has no home-path, host or internal-tool-name matches under the pattern check. The README names the local ev0lve theme folder, as the brief requires, and records the check there.
- **Screens and shots:** 65 HTML screens and 76 PNGs; `index.html` links every file. Sizes match the brief: 1440×900 (1440×1180 for `10`, `10b`, `10e`, `10f`); 780×1688 for 390×844 @2x; 1024×768; 768×1024; 1100×800; 1280×800; 320 and 360 px reflows.
- **Colours:** `_src/hexcheck.py` re-run: 36 colours in 70 design files, 336 in 93 theme files, none shared. Extra scans: no `rgb()` or `hsl()` overlap; no `url()`, gradient, image or `@import` in the design; no `#fff` or `#ffffff` in the design (the theme has 54 lowercase matches); the one font is local (Geist Mono, OFL).
- **States:** a scan of all 65 screens for `aria-expanded`, scrims, modal flags and live regions agrees with the states the PNGs show.
- **Keytest:** `_src/keytest-results.txt` has 65 PASS lines and ALL PASS. Not re-run.
- **PNGs against markup:** I read the text of each changed screen and compared it with its PNG. I did not re-render. The PNG files date from 11:39 and some HTML from 11:41; the states I read matched the current markup.
- **Not done:** no screen-reader pass; no real touch device; no re-render.

Verdict: CHANGES
