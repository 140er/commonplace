# Card #220 · B + chat · review round 2 (Haiku)

- **Direction:** `B-chat/`, branch head `a93a86b19e6c612fb29ccc061d852b976579693d` (`design/220-workbench-chat`).
- **Reviewer:** Haiku, non-gate review. Read-only: no design file edited, no subagents. Scratch scripts and probe renders were written outside the folder and are not part of it.
- **Read:** the review brief and its B + chat addendum, the B + chat brief, `README.md`, `RESPONSES-r1.md`, `review-haiku-r1.md`, `review-terra-r1.md`, `index.html`, `screens/wb.css` (all 636 lines), `screens/wb.js` (all), `_src/keytest-results.txt`. All 58 PNGs in `shots/` opened and looked at. Markup of the chat screens checked by targeted search (toggles, roles, hash targets, labels, live regions, wording), not line by line.
- **Re-rendered:** all 58 shots from the HTML with the same flags as `_src/shoot.sh`. 56 are byte-identical. `03c-connect-agent` and `09e-memory-searching` differ; both contain the animated `.spin` ring, so I read the difference as animation frame, not stale content. I also rendered chat states at widths the designer did not draw (768×1024, 1100×800, 390 px). Those are cited as "probe, not in shots/".
- **Branch:** `design/220-workbench-chat` and `origin/design/220-workbench-chat` both point at `a93a86b`. Its tree holds 126 files, every one byte-identical to the folder.

**Count: BLOCKING 0 · MAJOR 5 · MINOR 10.**

No desktop core flow is impossible, so nothing is BLOCKING. The five MAJORs are the tablet and phone layouts the brief asks for, plus one feedback gap on the board.

---

## Round-1 findings: are they really fixed?

| r1 finding | Status | Evidence |
|---|---|---|
| Haiku M1: no chat control on Needs me or Memory | Fixed | `07`, `07b`, `08`, `09`, `09b`–`09e` carry the control; `/` works there (keytest 25, 38–39). |
| Haiku M2 + Terra MAJOR 1: chat row only highlights; details stay on A-121 | Fixed for chat rows at 1440 and 1024 | `06f`, `06f2`; keytest 29–32, 34–36. The board-click path is still silent with the chat open: see MAJOR 2. |
| Terra MAJOR 2: all three mobile Show links lead to A-134 | Fixed | `m11`, `m11b`, `m11c` each open their own card; keytest 41–43. |
| Haiku M3: replies labelled "Atlas orchestrator" | Fixed | `06a`, `06c` show "Atlas orchestrator" with the agent tag; `06d`, `09`, `10`, `10e` name the quick-reply writer. |
| Haiku MINOR 1: linked card has no board marker | Fixed | "Linked in chat · 14:03" on A-118 in `06c`, `06f2`, `m11c`. |
| Haiku MINOR 2: outline sticks after focus leaves | Fixed | `wb.js` 202–205 clear on focusout and mouseout; keytest 37. |
| Haiku MINOR 3: recovery actions look like body text | Fixed | `06d` "Check the agent in Settings" is bordered (`.btn`, wb.css 46); `03c` "Can't copy?" is underlined (`.btn.ghost.u`, wb.css 616). |
| Haiku MINOR 4: first screen repeats itself; chat opens by default | Fixed | `03` opens with the chat closed and one explanation in the step strip; keytest 40. |
| Haiku MINOR 5: mobile unread badge lost on Needs me | Fixed | Chat tab badge on `m2` and `m3`; visible text is "new" only, see MINOR 2. |
| Haiku MINOR 6: mobile ask paths undrawn | Not fixed in substance | Still undrawn, and the response's claim is wrong: see MAJOR 3. |
| Haiku MINOR 7: `aria-label` on a generic div | Fixed | `role="group"` with `aria-labelledby` on the card lists (`06c`). |
| Haiku MINOR 8: tablet with chat open hides two columns | Fixed at 1024 only | Jump bar in `r-06c-chat-1024`. At 700–1023 px the board is still unusable: MAJOR 1. |
| Haiku MINOR 9: `/` does nothing on most screens | Fixed | `/` works on `03`, `04`, `07`, `08`, `09`; Settings has none and the README says so. |
| Haiku MINOR 10: README hex counts do not reproduce | Partly | The B-chat count reproduces (36). The theme count does not: MINOR 9. |
| Haiku MINOR 11: "Decide again →" | Fixed | `m7` reads "Approve or decline…". |
| Haiku MINOR 12: "1 new" has no rule | Partly | The desktop chip reads "1 new reply". The phone badge shows "new" alone: MINOR 2. |
| Haiku MINOR 13: working ring is static | Fixed | `@keyframes wb-spin` (wb.css 620–621), stopped by the reduced-motion rule (wb.css 39). |
| Haiku MINOR 14: "Show N more" under 44 px on mobile | Fixed | `.mobile .more { min-height: 44px }` (wb.css 622); the `m11` screens carry `class="mobile"`. |
| Terra MINOR 1: R only focuses Ask a question | Fixed | R clicks `#ask-question` (wb.js 112); keytest 38. |
| Terra MINOR 2: mobile Back has no focus return | Fixed | Back links to `m1-board.html#m-chat-tab`; the page focuses the hash target (wb.js 212–214); keytest 41–44. |
| Terra MINOR 3: white is shared with the theme | Fixed | Surface is `#FEFEFC`; no shared colour (see the ev0lve check). |

The 320 px project strip carried from B r4 is untouched. B passed with it, so it is not a regression.

`RESPONSES-r1.md` says the desktop `07d` and the mobile `m10` sheet "already show both halves" of the ask strip. `m10-chat.html` has no "You are asking" text and no "Back to the approval" link (0 matches each). The claim is wrong.

---

## MAJOR

### MAJOR 1 — At tablet width with the chat open, the board is unusable

- **Screen:** `screens/06c-chat-answer.html`. Every chat-open state at 700–1023 px is affected, because wb.js 126 adds `chat-open` to the shell in all of them (`06a`–`06f2`, `03b`–`03e`, `07d`, `09f`). Probe at 768×1024, not in `shots/`.
- **User problem:** the chat takes 340 px and the rail 220 px, which leaves about 208 px for the board at 768 px wide. In the probe, one partial column shows, and the header's "Blocked 1" filter is cut off because `.center` clips (`overflow: hidden`, wb.css 59). The card tag "From chat · 14:03" loses its time. The column jump bar does reach the other columns, but the header control is unreachable. The only chat-open tablet frame drawn is 1024×768 (`r-06c-chat-1024`), where the board is about 464 px wide. The brief names ≈1024 px, but the design's own tablet range is 700–1099 px, so 700–1023 px is in scope.
- **Evidence:** wb.css 581–585 set `.shell.chat-open { grid-template-columns: 220px minmax(0,1fr) 340px }` and the board to `repeat(4, 190px)` with `overflow-x: auto`. Centre width is viewport − 560 px: about 140 px at 700, 208 px at 768, 464 px at 1024.
- **Fix:** below 1024 px, open the chat as a modal sheet with the same scrim, focus trap and Esc-return that B already uses for the tablet Details sheet (`.shell.panel-open`, wb.css 461–462). Keep the push only at 1024 px and up. Draw `06c` at 768×1024 as a screen.

### MAJOR 2 — Selecting a board card with the chat open gives no visible result

- **Screens:** `screens/06a-chat-open.html` and `screens/06c-chat-answer.html` (chat open, board visible). Code: `screens/wb.js` 43–47 (Enter), 186–187 (click), `showDetails` 161–177.
- **User problem:** with the chat open, a click or Enter on a board card (for example A-121) only moves the outline. `showDetails()` writes the card into the context panel, which is hidden while the chat is open, and nothing says so. The user cannot tell which card is now selected or where its Details went. The keytest covers board clicks only with the chat closed (`_src/keytest-results.txt` line 33: `"chat":false`).
- **Fix:** when a board card is chosen while the chat is open, show that card's Details in the same slot with the "Back to chat" strip, reusing the chat-row path (`showDetails(id, row)` with a board-origin strip). Add the case to `keytest.cjs`. Minimum fallback: a line in the chat header, "A-121 selected · Details: close chat".

### MAJOR 3 — The phone ask path loses the approval

- **Screens:** `screens/m3-needs-me-item.html` ("Ask a question"), `screens/m8-send-too-late.html` ("Ask the orchestrator"), `screens/m10-chat.html` (where both land).
- **User problem:** the sheet has no "You are asking about…" strip and no "Back to the approval" link. The phone user lands in an empty chat about sending beta 0.9 to 40 testers, and "Back to board" drops the approval entirely. The desktop flow (`07d`) works. The phone flow exists only as a sentence in the README, which says "Not drawn".
- **Fix:** draw `m3` → `m10` and `m8` → `m10` with the same strip as `07d`, plus "Back to the approval" linking back to the item (`m3-needs-me-item.html`). Then the README can say "drawn" and flow 5.5 loses the "Not drawn" note.

### MAJOR 4 — The phone chat shows only the connected happy path

- **Screens:** `screens/m10-chat.html` (the only phone chat screen); `screens/m1-board.html` (top bar).
- **User problem:** a phone user with no orchestrator sees no "Waiting for an agent", no note that connecting happens on a computer, and no recovery when the agent stays silent (Ask again, Make it a card now, Check the agent). Those states exist on desktop only (`03b`, `06d`, `06e`). On the phone, Settings is a sliders icon with no visible label (`aria-label="Project settings"`), so the agent list and the chat-answer setting have no visible route. The phone tab bar offers Memory, but no phone Memory screen is drawn. B's phone set (`m1`–`m9`) had no Memory, Settings or connect screen either, so this gap was inherited. The chat is new, however, and the brief asks for chat states (item 5) and a phone chat (item 6).
- **Fix:** draw three phone chat states from the same markup: no-agent (waiting, a "Connect an agent on a computer" step, Make it a card now), error (Ask again, Make it a card now, Check the agent in Settings), and empty. Draw one phone Settings sheet reached from a labelled "Settings" control. Either draw a phone Memory screen or remove the Memory tab from the phone bar.

### MAJOR 5 — The README gives two phone chat behaviours and no rule for which applies

- **Screens:** `screens/04-board.html` and `screens/06c-chat-answer.html` at 390 px (desktop markup). README "Tablet and mobile": "Below 700 px, the desktop markup … stacks the chat pane under the board", against "Mobile, 390×844" for the `m` screens.
- **User problem:** at 390 px, by the README's own sentence, the desktop markup applies. In the probe at 390×2400, the chat header starts about 1,650 CSS px below the top, under four stacked columns, so the phone's Chat control opens something that is not visible from the top of the screen. At 390×844 the chat-open frames `06a`, `06c`, `06e` and `09f` came back blank in my headless render (2.7 KB PNGs with the shipped flags). `06c` does paint at 390×2400. I did not establish the cause; the autofocused composer is one candidate. No frame of this state is in the folder, so its behaviour is unverified.
- **Fix:** define one phone breakpoint. Below 700 px, serve the `m` screens (full-screen chat sheet), state the rule in the README, and do not stack the chat under the board on phones. Draw the chat-open state at 390×844 from the markup that ships.

---

## MINOR

1. **The board is cramped at 1100–1279 px with the chat open** (`screens/06c-chat-answer.html`, probe at 1100×800). Columns are about 87–107 px. "From chat · 14:03" shows as "From chat", "Needs me" as "Needs m", and IDs break ("A-" / "118"). No media rule covers 1100–1279 px; wb.css has only the 700–1099 px rules. Fix: at 1100–1279 px narrow the chat to about 300 px, or collapse the rail to icons while the chat is open.
2. **New replies are not announced, and the phone badge says only "new"** (`04-board.html`: `<span class="ct-chip new">1 new reply`; `m1-board.html`: `<span class="badge newb">new`). Neither has a role or live region, so a reply that arrives while the chat is closed is silent to screen-reader users. Fix: `role="status"` on the count, and show "1 new" visibly on the phone badge.
3. **Needs me and Memory name one project** (`07-needs-me.html` reads "Atlas chat · 1 new reply" with Harbor items on the page; `08-needs-me-done.html` switches to "Harbor chat" on selection). A reply in the other project is invisible from the list. Fix: show a combined count ("2 new replies in 2 projects") or a per-row marker.
4. **The shortcuts reference is incomplete** (`12-shortcuts.html`). It does not list Esc-closes-the-chat, which the README and the composer hint both promise. The `]` close key is in wb.js 110 but is not among the 41 keytest checks. Fix: add both rows and both checks.
5. **"Ask the orchestrator" promises a change that cannot be made** (`m8-send-too-late.html`: "To change something, ask the orchestrator."). The email has been sent and cannot be recalled; `07` says so. Fix: "This can't be recalled here. To follow up, ask the orchestrator."
6. **Settings "Chat answers" is too heavy** (`10-settings.html`). About 100 words of explanation and three option panels sit above the agent list. Fix: one sentence in the block, with the rest behind "What quick replies are".
7. **The empty chat copy is five sentences** (`06e-chat-empty.html`, about 85 words on a blank screen). Fix: two sentences plus the three existing chips, which are real buttons (`<button class="chip">`).
8. **The Memory error copy is internal** (`09d-memory-error.html`: "Search is updating after a new version."). A first-timer can only retry. Fix: "Search is unavailable right now. Your memories are unchanged."
9. **The README's theme colour count does not reproduce** ("336 in the theme folder"). A plain `#hex` scan of the same 93 files gives 376 distinct values; excluding HTML entities and word-prefixed matches gives 203. "Shared: none" holds under both. Fix: state the exact pattern and the count it gives.
10. **Two promised outcomes are not drawn.** `10-settings.html` (Basic mode) promises "a short receipt confirms your message reached the orchestrator"; `03b-chat-no-agent.html` offers "Make it a card now" without showing the result. No frame shows the receipt or where the card lands. Fix: draw one of each, or cut the promise.

---

## Ease of use: flow walk

| Flow | Drawn path | Actions (excluding typing) | Result |
|---|---|---|---|
| Start | 01 → 01b → 02 → 03 → 03c → 03c2 → 03d | About 7–8, including the email link and pasting into the agent app | Finishable. The step strip in `03` explains the orchestrator. |
| Board and add card | 04 → 05 → 05b | 2 | Finishable. |
| Talk to the orchestrator (desktop) | 04 → Chat → 06a → 06b → 06c → row → 06f → Back to chat | 3 clicks | Finishable, and chat → card details works. |
| Talk (phone, connected) | m1 → Chat tab → m10 → Show → m11 → Back to chat | 3 taps | Finishable. No phone not-connected or error state: MAJOR 4. |
| Approve (desktop) | 04 → Needs me (07) → Approve and send… → Yes, approve → 07c → 08 | 3–4 | Finishable. The 60-second hold and Stop are explained. |
| Ask about an approval (desktop) | R or Ask a question (07d) → type → Back to the approval | 2 | Finishable. The strip names the item. |
| Ask about an approval (phone) | m3 → Ask a question → m10 → type → Back to board | 2 | Context lost; the way back leaves the approval: MAJOR 3. |
| Memory (desktop) | 09 → search → result → Open in chat (09f) → type | 3 | Finishable. The quote travels with the question. |
| Settings | Settings → 10 → radio (saves itself) / 10e → provider, key, Turn on | 1–4 | Finishable. The disconnect confirms state the rule (10c, 10d). |
| Connect a second agent | 04 → Settings → Connect an agent → 03c → 03c2 → 03d | 3, plus copy and paste outside the app | Finishable. The restart is the one step outside the app. |

## Clarity, empty, error and loading states

- Orchestrator and agent are defined in `01`, `02`, `03`, and in `06e` and `10`. MCP is defined once, on `03c`, as an open standard. None of the terms the brief lists (PAT, wing, claim) appears as visible text.
- Chat states exist on desktop: waiting and offline (`03b`), connecting and failed (`03c`–`03e`), working (`06b`), no answer (`06d`), empty (`06e`). Memory has no-results, empty, error and searching states (`09b`–`09e`). Needs me has an empty state (`07b`).
- Every message names its writer, and quick replies are labelled. That keeps "memory verbatim with source" true.

## Accessibility (WCAG 2.2 AA)

Contrast, computed from the `:root` tokens in wb.css 5–25 with the WCAG relative-luminance formula. Text needs 4.5:1; UI parts need 3:1.

| Pair (where used) | Ratio |
|---|---|
| text-3 #62646C on well #EDEDE7 (column hints, card IDs) | 5.02 |
| text-3 #62646C on agent-bg #EFEAFA (lowest chat text pair) | 5.01 |
| text-3 #62646C on pane #FAFAF8 (times, metadata) | 5.65 |
| text-2 #4A4C55 on surface #FEFEFC (chip, subtitle) | 8.47 |
| text #17181C on surface (body) | 17.57 |
| surface #FEFEFC on text (open toggle, wb.css 552) | 17.57 |
| accent #2E43C0 on accent-weak #ECEEFB ("1 new reply" chip, wb.css 555) | 6.79 |
| accent on surface (New-since line, on the chat surface) | 7.76 |
| surface on accent (primary buttons) | 7.76 |
| block #A3231A on block-bg #FCE9E7 ("No answer yet", wb.css 425) | 6.38 |
| wait #7A4300 on wait-bg #FFF1D6 | 7.15 |
| ok #1B6136 on ok-bg #E4F3E9 | 6.51 |
| line-strong #7E7E76 on surface (control borders) | 4.05 |
| line-strong on pane (chat toggle border, header) | 3.91 |
| line-strong on well (dashed add and empty borders) | 3.48 |
| accent focus ring (wb.css 36) on pane / well / surface | 7.50 / 6.67 / 7.76 |
| accent-weak chip edge #AEB8EC on surface (decorative; the text carries the state, wb.css 555) | 1.91 |

The README's figures reproduce (6.8, 17.6, 8.5, 7.8, 3.9). Every text pair passes AA, and every non-text pair that identifies a control passes 3:1.

Targets, from the CSS. All are at least 24 px; phone targets are at least 44 px where the brief asks.

- Chat toggle 32 px (wb.css 550). Default `.btn` 32 px (wb.css 46). Close chat and Back to chat, `.btn.sm`, 28 px (wb.css 51).
- Chat rows 36 px (wb.css 211), and 48 px on the phone (wb.css 600).
- Back to board 44 px (wb.css 376). Phone send 44×44 (wb.css 606). Phone tabs 56 px (wb.css 360).
- Tablet column jump 32 px (wb.css 627). Quote remove 28 px (wb.css 578). "Show N more" 44 px on the phone (wb.css 622).
- Radio labels are `display: block` (wb.css 531), so the label row is the activation target.

Other checks:

- Focus is visible: a 3 px accent outline (wb.css 36).
- The composer has a visually hidden label ("Message to Atlas chat"). Icon buttons carry names (search, project settings, remove the quote). Radio inputs are labelled with `for`.
- `aria-expanded` and `aria-controls` appear on every opener. `aside`, `role="log"`, `role="status"` and `role="alert"` are used as intended.
- Reduced motion stops the ring (wb.css 39).
- No colour-only meaning found: each state has text or a shape alongside the colour.

Gaps: MINOR 2 (announcements), MAJOR 2 (keyboard and pointer feedback for board selection), MAJOR 5 (phone reflow unverified).

## Rules and premise

- **Board is the default view:** pass. The chat is closed on open (`03`, `04`). It opens only in pending-state screens.
- **One automatic orchestrator:** pass. The `10` rule text, `10c` ("Iris becomes the orchestrator automatically") and `10d` ("No other agent can take over") make it automatic, and nothing lets a user pick one.
- **Needs me holds human items only:** pass. The approval, review and decision in `07` are all things a person must do.
- **Memory verbatim with source:** pass for `09` and `09f`. Quick replies are labelled.
- **Attention:** acceptable, with the density points in MINOR 6 and 7. The toggle, the Needs me and Memory chips, the phone badge and "New since you last looked" each earn their place.
- **Generic and synthetic:** pass. The names are synthetic. `llama-3.1-8b-instruct` in `10`–`10d` is a public model ID, not a host, token or internal tool.
- **Premise:** B plus one chat rail behind one control, as the owner asked. It is distinct from a generic dashboard.

## No ev0lve visuals

- B-chat: 36 distinct colours across 58 `.css`, `.html`, `.js` and `.py` files. Local ev0lve theme folder: 376 distinct values across 93 files (plain scan). "Shared: none" holds under both counts.
- No file is byte-identical between the two trees.
- Fonts differ. B ships Geist Mono (with its OFL licence). The theme ships Inter and JetBrains Mono. Palette, texture and imagery are B's own.

## Nothing that passed in B's earlier rounds got worse

- The board header swaps the "Orchestrator connected" pill for the Chat control. That is declared as a trade-off, and the chat header and Settings carry the full status.
- The chat-open tablet layout is new, and it is MAJOR 1.
- I found no other change to B's passed screens (`01`, `02`, `05`, `05b`, `07`, `08`, `09`, `10`) that makes them worse.

## What works

- The chat-to-card link is visible in four places: "From chat · 14:03" and "Linked in chat · 14:03" on the cards, the dashed outline from a focused row, and "Made from chat at 14:03" in Details, quoting the request verbatim (`06f`, `06f2`).
- Writer labels are exact ("Atlas orchestrator" agent tag against "Quick reply · local model") and repeat in memory results (`09`).
- Consequential confirms (`07c`, `10c`, `10d`, `m5`) put focus on Cancel.
- The hosted-provider copy says what leaves the machine and what does not (`10e`).
- Single-key shortcuts can be switched off and the setting is kept (`12`; wb.js 108, 217), which covers WCAG 2.1.4.

## Verification log

- **Branch:** both refs at `a93a86b`. 126 files, every one byte-identical to the folder.
- **Keyboard:** `_src/keytest-results.txt` has 41 PASS lines and "ALL PASS". I read the results file; I did not re-run `keytest.cjs`.
- **Shots:** 56 of 58 re-render byte-identical with the shipped flags. The other two differ on the spinner frame.
- **Probes, not in the folder:** 768×1024 (MAJOR 1), 1100×800 (MINOR 1), 390×844 (blank, MAJOR 5), 390×2400 (painted, MAJOR 5), and a 390 px closed board (painted).
- **Contrast:** computed from the `wb.css` tokens with the WCAG formula.

Verdict: CHANGES
