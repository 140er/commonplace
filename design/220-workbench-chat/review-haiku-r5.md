# Card #220 · B + chat · review round 5 (Haiku)

- **Direction:** `B-chat/`, branch `design/220-workbench-chat` at `b9a1b73fee3d9487a7536ddc49df1c6012ba333e` ("round 5, answer r4 reviews").
- **Reviewer:** Haiku, non-gate review. Read-only: no design file edited, no subagents. Scratch scripts were kept outside the design folder.
- **Read:** the review brief and its B + chat addendum, `B-CHAT-BRIEF.md`, `DESIGN-BRIEF.md`, `DIRECTIONS.md`, `README.md`, `RESPONSES-r1.md` to `RESPONSES-r4.md`, all eight earlier reviews (Haiku and Terra, r1–r4), `index.html` link coverage, `_src/keytest-results.txt`, `_src/layoutcheck-results.txt`, and `screens/wb.css` and `wb.js` where the claims depend on them. Every screen's chat markup was checked by script. **Opened all 78 PNGs in `shots/`.**
- **Branch:** 172 files. All are identical to the folder except `review-haiku-r1.md` and `review-haiku-r3.md`, whose committed copies are the sanitized mirrors the designer disclosed.

**Count: BLOCKING 0 · MAJOR 1 · MINOR 10.**

The round-4 MAJORs are really fixed. The one MAJOR predates this round: the phone board's column row fails reflow at 320 CSS px. The MINORs are mostly consistency between screens (unread counts, chat history, a header width band), not missing features.

## 1. Earlier findings: are they really fixed?

Status: **Fixed** = visible in a PNG or CSS and checked · **Fixed (script)** = works in `wb.js` or the keytest only · **Partly** · **Written down** · **Open**.

| Round | Finding | Status now | Evidence |
|---|---|---|---|
| r1 | H1-M1 no chat control on Needs me or Memory | Fixed | `07`, `07c`, `08`, `09`, `09b`–`09e` carry the control; the unread count is missing on some (m3) |
| r1 | H1-M2, T1-M1 chat row only highlighted | Fixed | `06f`, `06f2`, `06f3`; `r-06f-chat-1024`, `r-06f-chat-768` |
| r1 | H1-M3 "Atlas orchestrator" on model replies | Fixed | `06a`, `06c`: "Quick reply · local model" against the agent tag; `09` fourth result; `10`, `10e` text |
| r1 | T1-M2 mobile Show links all to A-134 | Fixed | `m11`, `m11b`, `m11c` each show their own card and column |
| r1 | H1-m1 linked card has no marker | Fixed | "Linked in chat · 14:03" on A-118 in `06c`, `06f2`, `m11c` |
| r1 | H1-m2 outline sticks after focus leaves | Fixed (script) | `wb.js` focusout and mouseout; keytest |
| r1 | H1-m3 recovery actions look like body text | Fixed | `06d` bordered button; `03c` underlined "Can't copy?" |
| r1 | H1-m4 first screen repeats; chat open by default | Fixed | `03` and `04` open with the chat closed |
| r1 | H1-m5 mobile unread badge lost on Needs me | Fixed | "1 new" on `m1` and `m2` |
| r1 | H1-m6 mobile ask paths undrawn | Fixed (r2) | `m10b`, `m10c` |
| r1 | H1-m7 `aria-label` on a generic div | Fixed | `role="group"` on the card lists in `06c` |
| r1 | H1-m8 tablet with chat hides two columns | Fixed | column jump bar in `r-06c-chat-1024` |
| r1 | H1-m9 `/` does nothing on most screens | Fixed | keytest (board, Needs me, Memory) |
| r1 | H1-m10 README hex counts | Fixed | my re-run agrees (section 8) |
| r1 | H1-m11 "Decide again" | Fixed | `m7`: "Open the approval" |
| r1 | H1-m12 "1 new" has no rule | Fixed | visible "1 new reply"; rule in README line 16 |
| r1 | H1-m13 working ring is static | Fixed | `@keyframes wb-spin` (`wb.css:617`); reduced-motion rule (`wb.css:39`) |
| r1 | H1-m14 "Show N more" under 44 px | Fixed | `.mobile .more` 44 px (`wb.css:619`) |
| r1 | T1-m1 R only focuses the button | Fixed | keytest |
| r1 | T1-m2 mobile Back has no focus return | Fixed | `m10` back link to `#m-chat-tab`; keytest |
| r1 | T1-m3 white shared with the theme | Fixed | surface `#FEFEFC`; hex check (section 8) |
| r2 | T2-M1 Harbor composer labelled "Atlas" | Fixed | `08`: "Message to Harbor chat" |
| r2 | H2-M1 768 px board unusable with chat open | Fixed | `r-06c-chat-768`: modal sheet over a dimmed board |
| r2 | H2-M2 board click with chat open is silent | Fixed | `06g`: "You picked A-121 on the board…" |
| r2 | H2-M3 phone ask loses the approval | Fixed | `m10b`, `m10c` keep the strip and "Back to the approval" |
| r2 | H2-M4 phone chat shows the happy path only | Fixed for chat states | `m12`, `m12b`, `m12c`, `m13`; phone Memory tab removed (m8) |
| r2 | H2-M5 two phone behaviours; blank 390 render | Fixed | one rule below 700 px; `r-06c-chat-390` renders the sheet from the top |
| r2 | H2-m1 board cramped at 1100–1279 px | Fixed | `r-06c-chat-1100` readable; `r-06c-chat-1280` tags wrap (header: m4) |
| r2 | H2-m2 replies not announced; phone badge says "new" | Fixed | `role="status"` outside the toggle; phone tab status line; "1 new" |
| r2 | H2-m3 Needs me and Memory name one project | Declared, unchanged | README open risks |
| r2 | H2-m4–m9 shortcuts, "Ask" promise, Settings length, empty copy, memory error, colour count | Fixed | `12` Esc and `]` rows; `m8`; `10` disclosure; `06e`, `m12c`; `09d`; README |
| r2 | H2-m10 promised outcomes undrawn | Partly | "adds it to Todo" drawn; the Basic receipt is described only |
| r3 | H3-M1 "No answer yet" recovery leads nowhere | Fixed | `06d`, `m12b` → `10f`, `m13b`; Check again; back to the chat |
| r3 | H3-M2 chat results with no screen | Fixed | `06g`, `r-06f-chat-1024`, `r-06f-chat-768`, `m10-chat-from-needs` |
| r3 | H3-m1 1280 px tag clipped | Fixed | `r-06c-chat-1280`: tags wrap (header width: m4) |
| r3 | H3-m2 response claims not on screens | Fixed | `06e` names quick replies |
| r3 | H3-m3 toggle name includes the count | Accepted | README |
| r3 | H3-m4 no 1100 px frame | Fixed | `r-06c-chat-1100` |
| r3 | H3-m5 phone memory route | Documented, reason wrong | m8 |
| r3 | H3-m6–m14 jargon, "adds it to Todo", Details button, README modal wording, `kbd` wrap, confirm covering the note, `m7` wording, 320 px strip, scrim tap | Fixed | `06a`, `06d`, `wb.js`; `12`; `07c`; `m7`; `r1-04-board-320`; keytest |
| r3 | T3-M1 phone Chat tab does not name its project | Fixed | `m2` "Atlas chat 1 new"; `m2b` "Harbor chat 1 new"; `m10h` |
| r3 | T3-m1 Settings loses its origin | Fixed | `m13c` "Back to Needs me"; `m13b` "Back to the chat" |
| r3 | T3-m2 mobile unread update silent | Fixed | `role="status"` line in the tab bar |
| r4 | H4-M1 "not answering" contradicts itself | Fixed | `06d`, `m12b`: "Connected, not answering since 14:02"; `10f`: "Last active 14:03"; `m13b` agrees |
| r4 | H4-M2 `r-06c-chat-390` overlaps its own text | Fixed | last message above the composer; hints hidden below 700 px |
| r4 | H4-M3 A-121 row says "the 14:03 answer" | Fixed | `06f3`: "the 12:41 quick reply (local model)" |
| r4 | TR4-M1 Esc on `m13b` returns to the board | Fixed | keytest, per origin |
| r4 | H4-m1 header wraps below 1280 px | Partly | fixed at 1024 and 1100; at 1280–1399 "Add card" still wraps (m4) |
| r4 | H4-m2 Hide and `]` dead on chat-opened Details | Fixed | keytest on `06f` |
| r4 | H4-m3 phone status text not state-specific | Fixed | no count on the chat sheets |
| r4 | H4-m4, TR4-m1 Settings return from Needs me and Harbor | Fixed | `m13c-settings-from-needs`; keytest for `?from=harbor` |
| r4 | H4-m5 recovery actions have no drawn result | Written down | README flow 4 |
| r4 | H4-m6 `m7` action reads as an action | Fixed | "Open the approval" |
| r4 | H4-m7 phone Memory tab removed | Written down, reason wrong | m8 |
| r4 | H4-m8 phone chat tab depends on hidden state | Fixed | explanatory line on `m2` and `m2b` |
| r4 | H4-m9 `06e` has three sentences | Fixed | two sentences |

## 2. BLOCKING

None. Every core flow finishes from the PNGs (section 5).

## 3. MAJOR

### M1 — The phone board fails reflow at 320 CSS px (`screens/m1-board.html`; render `shots/r2-m1-board-320.png`)

- **User problem:** At 320 CSS px the column row (Todo · Doing · Review · Done) is wider than the screen, and "Done 14" is cut at the right edge. The row scrolls sideways with its scrollbar hidden (`.m-cols { overflow-x: auto; scrollbar-width: none }`, `screens/wb.css:351`), so a user on a narrow phone may never see the Done column. WCAG 2.2 criterion 1.4.10 (Reflow) requires vertically scrolling content to work at 320 CSS px without horizontal scrolling. The same row is on the other phone board screens (`m4`, `m9`, `m10`, `m11`, `m11b`, `m11c`, `m12`, `m12b`, `m12c`). The designer's own render shows the failure, and `B-workbench/README.md` line 121 claims "Reflow (1.4.10) … implemented".
- **Scope and history:** The same 320 px render exists in `B-workbench/shots/`, so this is carried over from B and is not a regression. Earlier rounds checked 320 px only for the desktop project strip. At 390 px all four tabs fit (`shots/m1-board.png`), so the failure is confined to narrow widths.
- **Fix:** Let `.m-cols` wrap below about 370 px (its 44 px tabs can take two rows), or use a segmented select. Remove `scrollbar-width: none`. Re-render `r2-m1-board-320` with Done fully visible, and state the reflow width in the README.

## 4. MINOR (10)

m1. **`screens/03d-connect-done.html`, against `03b-chat-no-agent.html`.** The message the user wrote while offline ("Also add a card for the store listing.", 10:04, shown as "Not sent. This device is offline. Your words are kept here until it sends.", `03b` line 71) is gone once the agent connects. `03d` shows only the 10:02 message as delivered, and the 10:04 text appears nowhere in the file. The promise "kept until it sends" breaks on the first-run path. *Fix:* show the 10:04 message as delivered or sending in `03d` and in the chat, and keep Try again and Edit until it is sent.

m2. **`screens/03e-connect-failed.html` (line 87).** The failure dialog has no Cancel or Close and no Esc hint. `03c` and `03c2` have "Cancel Esc" (lines 92 and 86). The only exits are "Make a new setup" and the focused "Keep waiting", which reads as "stay here". *Fix:* add "Close" (back to the board, with step 2 still open), show Esc, and say what "Keep waiting" does.

m3. **Unread count on the chat control.** Desktop `08-needs-me-done.html` (line 26) shows "Harbor chat" with no count, while `m2b-needs-me-harbor.html` (line 26) shows "Harbor chat 1 new" for the same Harbor decision. `09b`–`09e` show "Atlas chat" with no count, while `09-memory.html` shows "1 new reply" for the same unread reply. README line 16 says the count shows when an answer arrived while the chat was closed. *Fix:* derive the chip from the same unread state on every screen with the control, or list the exceptions in the README.

m4. **Header at 1280–1399 px with the chat open (`shots/r-06c-chat-1280.png`, `screens/06c-chat-answer.html`).** "Add card" drops to a second header row. The rule that moves the filter group (`wb.css:716`) applies only below 1280 px, and the card-tag wrap (`wb.css:695`) does not cover the header. RESPONSES-r4 row 1 says "Add card" stays on row one, and `layoutcheck` covers 1024 and 1440 only. *Fix:* extend the header rule to 1399 px (or hide the hints there), and add 1280 and 1366 to `layoutcheck`.

m5. **Settings is an unlabelled icon at 1024–1279 px (`shots/r-06c-chat-1024.png`, `r-06c-chat-1100.png`, `r2-04-board-1024.png`).** The word is visually hidden (`.bar .wl`, `wb.css:707`) and only a sliders icon remains. Settings is where a second agent is connected and the model is chosen. The README discloses the trade-off. *Fix:* keep the word "Settings" on that row. The filter group already has its own row there, so the room exists.

m6. **The phone chat has no history (`screens/m10-chat.html`; also `m12b-chat-error.html`).** The sheet starts at "Today / Maya 14:02". The 12:30 message and the 12:41 quick reply (drawn on `06c` and in `r-06c-chat-390.png`) are absent, and there is no "Show earlier messages" control, although `m10b-chat-ask-approval.html` (line 35) has one. No phone screen shows a quick reply, so the writer label is never seen on a phone, while the README says every message names its writer. *Fix:* add the earlier-messages control and draw one quick reply in the phone sheet.

m7. **No divider on the ask paths (`screens/07d-needs-me-ask.html`, `m10b-chat-ask-approval.html`).** `07` counts the 14:09 reply as new, but when the chat opens by Ask a question there is no "New since you last looked" line above it. `09f-memory-ask.html` and `m10-chat-from-needs.html` do show the line. *Fix:* put the divider above the first unread message, whichever control opened the chat.

m8. **The reason for dropping the phone Memory tab is wrong (`README.md` lines 142 and 158; `screens/m1-board.html`).** The README says the chat "took a tab's place". B's bottom bar already had Board, Chat, Needs me and Memory (`B-workbench/screens/m1-board.html`), so the chat did not displace Memory, and four tabs fit at 390 px. The owner's rule was "keep everything else as B had it". r4 accepted the removal as a documented trade-off, but that acceptance rests on a true reason. *Fix:* restore Memory as the fourth tab, as B drew it, or correct the reason. Either way keep "phone memory results are not drawn" and name the palette route.

m9. **"Hosted provider" is pre-selected (`screens/10e-settings-hosted.html` line 41: `checked autofocus`).** The status reads "Off · not set up yet" and "Local model" is unselected, so it is unclear whether quick replies are still written by a model. *Fix:* keep the saved mode selected until "Turn on" succeeds, or label the choice "Hosted provider (off)".

m10. **Wording.** `screens/09d-memory-error.html` line 33 says "This usually takes about 2 minutes." It does not say what takes two minutes, so a first-timer may wait. *Fix:* "Try again in a moment." `screens/11-command-palette.html` rows show "state: New", which is internal wording. *Fix:* "New, from chat 14:03".

## 5. Ease of use: flow walk

Counts exclude typing and automatic transitions.

| Flow | Drawn path | Actions | Result |
|---|---|---|---|
| Start | 01 → 01b → 02 → 03 → 03c → 03c2 → 03d | about 7, plus the email link and two steps in the agent app | Finishable. `03` and `03d` explain the orchestrator (m1 covers the lost message). |
| Board | 04 | 0 | Column counts match: 4 / 3 / 2 / 14. |
| Add card | 04 → 05 → 05b | 2 | Finishable. `05b` says the card waits in Todo. |
| Talk to the orchestrator | 04 → Chat → 06a → 06b → 06c → row → 06f → Back to chat | 3–4 | Finishable. The chat ↔ card link is visible in both directions. |
| Handle a needs-me item | 04 → 07 → Approve and send… → 07c → 08 | 3–4, plus the 60-second hold | Finishable. Stop and the released state are drawn. |
| Ask about an approval | 07 → 07d → Back to the approval | 2 | Finishable. No divider (m7). |
| Find a memory | 09 → result → 09f → Back to the source | 2, plus typing | Finishable on desktop. No drawn phone route (m8). |
| Settings | 04 → 10 → radio | 2 | Finishable. The disconnect copy (`10c`, `10d`) matches the automatic rule. |
| Connect another agent | 04 → Settings → 10 → Connect an agent | 2–3 | Finishable at 1280 px and wider. Below that Settings is an unlabelled icon (m5). |
| Phone: chat | m1 → Chat → m10 → Show → m11 → Back to chat | 4 taps | Finishable. |
| Phone: approval | m2 → m3 → m5 → m6 → m7 or m8 | 4 | Finishable. |
| Phone: no answer | m12b → m13b → Back to the chat | 3 | Finishable. |
| Phone: memory | none drawn | — | No route (m8). |

## 6. Clarity, empty, error and loading states

- Drawn and plain: not connected (`03`, `03b`, `03c`–`03e`, `m12`), waiting and failed sends, working (`06b`), no answer (`06d`, `m12b`, `10f`, `m13b`), empty chat (`06e`, `m12c`), Needs me empty (`07b`), memory no-results, empty, error and searching (`09b`–`09e`), unreachable model (`10b`), hosted consent (`10e`), disconnect (`10c`, `10d`).
- Terms are explained where they appear: "orchestrator" in `02`, `03`, `03d` and `10`; "agent" in `01`; MCP in `03c`; "Quick reply" in `06a` and `10`. "PAT", "wing" and "claim" do not appear.
- Clarity risks: m1 (a lost message), m2 (exit from the failure dialog), and the inconsistent counts and history (m3, m4, m6).

## 7. Accessibility (WCAG 2.2 AA)

Computed by me from the `:root` tokens (`wb.css` lines 5–25), using WCAG relative luminance:

| Pair (where used) | Ratio |
|---|---|
| text `#17181C` on surface `#FEFEFC` (body, chat text) | 17.57 |
| text-2 `#4A4C55` on surface (subtitles, "no orchestrator yet" chip) | 8.47 |
| text-3 `#62646C` on agent-bg `#EFEAFA` (lowest chat text pair) | 5.01 |
| text-3 on well `#EDEDE7` (column hints, IDs) | 5.02 |
| text-3 on pane `#FAFAF8` (times, hints) | 5.65 |
| accent `#2E43C0` on accent-weak `#ECEEFB` ("1 new reply" chip) | 6.79 |
| block `#A3231A` on block-bg `#FCE9E7` ("No answer yet") | 6.38 |
| wait `#7A4300` on wait-bg `#FFF1D6` ("Not answering since") | 7.15 |
| agent `#5A3EA8` on agent-bg (agent tag) | 6.63 |
| control border line-strong `#7E7E76` on surface | 4.05 |
| line-strong on pane (chat toggle border, `wb.css:550`) | 3.91 |
| line-strong on well (dashed add and empty borders; the label carries the meaning) | 3.48 |
| focus ring accent on surface / on well (`wb.css:36`, 3 px) | 7.76 / 6.67 |

Text that carries meaning is at least 4.5:1, and boundaries that identify a control are at least 3:1. The placeholder uses text-3 (`wb.css:379`), which is at least 5.0 on every background I computed.

**Targets (CSS):** chat toggle 32 px (`wb.css:550`); Close chat and Back to chat, `.btn.sm`, 28 px (`wb.css:51`); chat rows 36 px (`wb.css:211`), 48 px on the phone (`wb.css:597`, `:673`); card ⋯ `.card-top .iconbtn` 44 px (`wb.css:135`); phone column tabs 44 px (`wb.css:352`, though the row overflows at 320 px, M1); `.mobile .more` 44 px (`wb.css:619`); desktop default `.iconbtn` 32 px (`wb.css:53`). Nothing interactive is below 24 px, and phone targets are 44 px.

**Keyboard and semantics:**
- `_src/keytest-results.txt` has 75 PASS lines and ALL PASS, with no FAIL. I did not re-run it. Its coverage includes dialog focus and trap, the chat open and close paths, row → Details → Back to chat at 1440, 1024 and 768 px, the 390 px sheet, Esc for each Settings origin, and R. Its dialog checks cover `03e`, but nothing in the design shows that dialog's exits as a visible control (m2).
- `aria-expanded` on the chat toggle and openers matches pane visibility on all 33 screens with chat markup (script check).
- Every text field has a label (for example the composer labels "Message to Atlas chat", per project), and every button has a name, across all 67 screens (script check).
- Single-key shortcuts can be switched off (`12`), which covers 2.1.4.
- Motion: `prefers-reduced-motion` with `!important` (`wb.css:39`) stops `wb-spin` (`wb.css:617`). The text carries the working state.
- Colour is never the only cue: chips, tags, "New since you last looked", dashed outlines and the Details strips carry words.
- Not done: no screen-reader pass, no real touch device, no 200% zoom render, and no live focus-order test beyond the recorded keytest.

## 8. Rules, premise and "no ev0lve visuals"

- **Board is the default view:** `03` and `04` start with the chat closed, and the chat never opens by itself.
- **One automatic orchestrator:** `03d` ("the first agent … became the project's orchestrator"), `10`, `10c` ("Iris becomes the orchestrator automatically"), `10d` ("No other agent can take over").
- **Needs me holds human items:** approvals, reviews and decisions. The approved-not-sent row in `08` is a live hold the user can stop.
- **Memory verbatim with source:** `09` and `09f` show writer, source, date and exact words. Quick replies carry their own source line.
- **Attention:** the unread chip appears only by exception. The density at 1440 px is B's and is acceptable.
- **Generic and synthetic:** names are Maya, Iris, Otto, Juno, Leon, Atlas, Harbor and Northwind; addresses are example.com; the model id is public. The connect step shows the setup with the key hidden ("key: •••••••• (in the copied text only)").
- **No ev0lve visuals:** I re-ran the colour comparison of the design (`.css`, `.html`, `.js`, `.py`) against the local ev0lve theme folder (every file, `.git` skipped). Design: 36 colours in 73 files. Theme: 343 values in 93 files. Shared: none. The README's 336 comes from a slightly different normalisation, and the conclusion is the same. I found no textures or images in the 78 PNGs, and the font is the bundled Geist Mono (OFL), not the theme's Inter or JetBrains Mono.

## 9. Nothing that passed got worse

- `01-signup`, `01b-check-email` and `01c-link-expired` are byte-identical to B.
- The other 37 shared screens differ. The declared palette change (`#FFFFFF` → `#FEFEFC`) and the declared features (toggle, phone bar, Settings disclosure, jump bar) account for the differences I checked. I did not diff each file line by line, and the r4 review found no undeclared change.
- B's `06-chat` page (a chat page with the same request → cards content) is replaced by `06a`–`06c` in the chat pane. That is the change the owner asked for, so it is not a regression, but the README's "What I traded away" does not name it.
- B's r4 items hold: counts 4 / 3 / 2 / 14, Todo 5 on `05b`, Todo 6 on `06c`; toast with Undo (`05b`); held send (`08`, `m6`); single-key switch "On" (`12`); "(Maya's laptop)" (`10`); developer wording removed (`09d`, `10b`); the palette footer excludes Approve and Decline (`11`); the desktop 320 px project strip (`r1-04-board-320`).
- Declared losses: the "Orchestrator connected" pill is gone from the board header, so the board shows only "no orchestrator yet"; the phone Memory tab is gone (m8). Carried, not new: the phone column row at 320 px (M1).

## 10. What works

- The chat ↔ card loop is clear in both directions at 1440, 1024 and 768 px and on the phone: answer → "From this request · on the board now" → row → Details with the exact request → Back to chat.
- Writer labels hold across chat, memory and Settings.
- The no-answer path ends in drawn actions, with consistent facts across `06d`, `m12b`, `10f` and `m13b`.
- The approval path keeps the exact words, the 60-second hold, Stop and the released state. The phone ask sheets keep the approval in view.

Verdict: CHANGES
