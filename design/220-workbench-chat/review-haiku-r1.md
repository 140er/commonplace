# Card #220 · B + chat · review round 1 (Haiku)

- **Direction:** `B-chat/`, branch head `2bdd2fae2cce2323fe99425156c2039856dd5ab2` (`design/220-workbench-chat`)
- **Reviewer:** Haiku, non-gate review. Read-only: no design file edited, no subagents. Scratch checks only in `/tmp/bchat-r1-*`.
- **Read:** the review and B+chat briefs, DESIGN-BRIEF, DIRECTIONS, `B-chat/README.md`, `index.html`, `screens/wb.css` and `wb.js` in full, the chat, Needs me, memory, settings, mobile and tablet markup, `_src/keytest-results.txt`, and B's round-4 reviews and `REPORT-ALL-PASS.md`. **Opened all 54 PNGs in `shots/`.** I did not open `review-terra-r1.md` in the folder, so this review stays independent.

**Count: BLOCKING 0 · MAJOR 3 · MINOR 14.**

## Verification

- **Branch matches folder.** Commit `2bdd2fae` holds 115 files, each byte-identical to the folder. The folder adds `review-terra-r1.md`, which is not in the commit.
- **No external resources.** No http(s) URL, `@import`, CDN or external script in `screens/`, `index.html` or `_src/build.py`. The one font is local (`wb.css:3`).
- **Contrast, computed from the `:root` tokens** (`wb.css:5–25`). For `--text`, `--text-2` and `--text-3` on the 11 surfaces the chat uses, the lowest ratio is `--text-3` #62646C on `--agent-bg` at 5.01:1. Every chat figure in the README reproduces: "1 new" chip 6.79 (README 6.8); toggle open, white on `--text`, 17.74 (17.7); "no orchestrator yet", `--text-2` on surface, 8.55 (8.6); New-since line 7.84 (7.8); context strip 16.97 (17.0); mobile "Showing…" bar 15.36 (15.4); toggle border `--line-strong` on `--pane` 3.91 (3.9).
- **Non-text contrast.** `--line-strong` #7E7E76 is 3.48:1 on `--well` and 4.09:1 on white (control borders, ≥ 3:1). Focus ring: 3 px `--accent` outline (`wb.css:36`), 7.50:1 on pane and 6.67:1 on well. Decorative only (the text carries the meaning): chip edge `#AEB8EC` at 1.93:1 on surface, and hit edge `#E8CE7E` at 1.46:1 on `#FFF8E1`.
- **Targets (CSS).** `.chat-toggle` 32 px (`wb.css:550`); Close chat `.btn.sm` 28 px (`wb.css:51`); chat card rows 36 px (`wb.css:211`), 48 px on mobile (`wb.css:600`); Back to board 44 px (`wb.css:376`); mobile send 44×44 (`wb.css:606`); mobile tabs 56 px (`wb.css:360`). Every chat target I checked is ≥ 24 px. The mobile exception is MINOR 14.
- **Motion.** `prefers-reduced-motion` removes all animation and transition (`wb.css:39`). The file has no `@keyframes`, so the working ring `.spin` (`wb.css:426`) is static.
- **Keyboard.** `keytest-results.txt` is ALL PASS (15 dialog checks, 10 chat checks). I read the logic in `wb.js:113–168` and found one gap (MINOR 2).
- **No ev0lve visuals.** A text scan of `~/work/ev0lve-themes` finds 52 distinct hex values; B-chat has 38. The only shared value is `#ffffff`, so the claim holds, but the README's counts (37 and 41) do not reproduce (MINOR 10).
- **B to B+chat.** The `wb.css` delta is additions only; no existing rule changed. The single-key switch now restores its saved state on load (B r4 minor 4). Fixed from B r4: counts (`05`, `05b`, `06c`, `11`); toasts (`05b`, `08`: the button sits beside `role="status"`); the Chat pill (now the toggle); "(Maya's laptop)" (`10`, `10b`, `10c`, `10e`); "localhost" and "index" (`10b`, `09d`); the desktop Stop note in the README. Still open: B r4 minor 5 (`m7`, see MINOR 11) and minor 9 (320 px project strip).

## Brief questions

| Question | Answer |
|---|---|
| Chat easy to find, open and close, keyboard too, without hiding the board? | Yes on the board: header toggle, `/`, Esc, `]`, Close chat; focus returns to the toggle. Not on Needs me or Memory (M1). `/` is a no-op on most other screens (MINOR 9). |
| Board stays the default view? | Yes. A new project opens the chat, which the brief says is not required (MINOR 4). |
| Chat ↔ card link visible? | For new cards, yes: "From chat · 14:03" tag plus dashed outline on focus. For linked existing cards, no (MINOR 1, 2). From chat to card details, no (M2). |
| Tablet and mobile | Tablet: chat pushes the board; Done is off-screen (MINOR 8). Mobile: full-screen sheet with a clear Back to board. Undrawn mobile ask paths (MINOR 6); badge lost on Needs me (MINOR 5). |
| Not-connected, error, empty states | All drawn (`03`, `03b`, `03c`–`03e`, `06d`, `06e`) and plain. Recovery links look like body text (MINOR 3). |
| No ev0lve visuals | Yes: no shared colour except plain white. README counts are off (MINOR 10). |
| Nothing that passed in B's earlier rounds got worse | Yes. Fixes listed above. Two carried minors (MINOR 11 and the 320 px strip). |

## BLOCKING

None. Every core flow finishes: the chat sends, shows and closes; the board stays the default; the keyboard path exists.

## MAJOR

**M1. Needs me and Memory have no project chat control and no unread signal (desktop).**
- **Screens:** `07-needs-me.html` (also `07b`, `07c`, `08`) and `09-memory.html` (also `09b`–`09e`). The only way into the chat is one item's own button (`#ask-question`, `#open-in-chat`). The rail has no chat entry, and the Chat toggle exists only in the board header (`04-board.html:27`).
- **User problem:** someone working in Needs me or Memory cannot open the project chat from the rail or header, and never sees "1 new reply" there. The owner asked to see the chat "bij B". `/` works only where a chat pane is present (`07`, `09`, and the item states), and the README does not say so.
- **Fix:** put the same `Chat ⟨1 new⟩` control in the Needs me and Memory headers, scoped to the selected item's project (or the workspace when none is selected). Alternatively, add a rail row "Atlas chat · 1 new" under Projects. Draw it on `07` and `09`, and add it to the README flow map.

**M2. Selecting a chat card gives a highlight, not its details, so the chat-to-card path dead-ends.**
- **Screens:** `06a-chat-open.html` (line 138, "select to show on the board") and `06c-chat-answer.html`; also `r-06c-chat-1024.png`.
- **Behaviour:** a chat row click only sets `.is-selected` on the board card (`wb.js:159–168`). With chat open, the right slot is the chat, so Details are hidden. The only Details drawn is A-121, so no screen shows what a selected chat card looks like.
- **User problem:** a first-timer clicks A-134 in the answer and sees a highlight on the board. The blocker, the approval or the memory they came to check is one "Close chat" away, and no screen shows it. The README discloses this trade-off, but it leaves the main chat flow without its detail step.
- **Fix:** draw and wire the state. On desktop and tablet, a chat row opens that card's Details in the right slot with a "Back to chat" strip, following the `07d` "Back to the approval" pattern. Add `06f-chat-card-details.html` and describe the transition in the README.

**M3. Chat replies are labelled "Atlas orchestrator", but Settings says a model writes them.**
- **Screens:** `06a-chat-open.html` (line 136: "Atlas orchestrator" with agent tag) and `06c-chat-answer.html`; `10-settings.html` ("Which model writes the quick reply you see in chat. The orchestrator still does the work." Local model is selected); `10e-settings-hosted.html`.
- **User problem:** a first-timer cannot tell whether "One thing. A-121 is blocked…" came from their orchestrator or from a local or hosted model. Under Hosted provider, text written by an outside service appears under the orchestrator's name. This breaks "memory shown verbatim with its source". Chat messages are memory (`09`, `09f`), and the design never says whether a model's quick reply is stored, or under whose name.
- **Fix:** label each reply with its writer: "Quick reply · local model" or "· hosted provider", and "Atlas orchestrator" only for the orchestrator's own messages. Show the same source line in the memory result. State in `10` and `10e` whether quick replies are saved. Draw the label on `06c`.

## MINOR

1. **Linked card has no board marker** (`06c-chat-answer.html`, A-118). Only new cards get "From chat · 14:03". A linked card shows its outline only while focused or hovered (keytest line 28). Fix: a persistent "Linked from chat · 14:03" tag on A-118 in `06c` and `11`.
2. **Outline sticks after focus leaves** (`wb.js:157`). `.card.is-linked` is set on focusin and mouseover, and cleared only when another chat row is entered. There are no focusout or mouseout handlers (grep count 0). After Tab leaves the chat, the card stays dashed. Fix: clear `is-linked` on focusout and mouseout of `[data-card]`, and add that case to the keytest.
3. **Recovery actions look like body text.** `06d-chat-error.html`: `<a class="btn sm ghost">Check the agent in Settings</a>`. `03c-connect-agent.html`: `<button class="btn ghost">Can't copy? …</button>`. The ghost style is transparent with `--text-2` (above 7:1 on these surfaces) and no underline. After "No answer yet", the only path to fix the agent reads as a sentence. Fix: underline the link, or use the bordered secondary style.
4. **First screen repeats its explanation** (`03-board-empty.html`, `03b-chat-no-agent.html`). The orchestrator is explained three times: the step strip, the chat callout, and "no orchestrator yet" in the toggle and chat subtitle. "Connect an agent" appears twice. The chat opens by default, though the brief says it "does not have to be open all the time". Fix: keep one explanation and one connect button, and start with the chat closed (the toggle already says "no orchestrator yet").
5. **Mobile unread badge is lost on Needs me.** `m1-board.html` shows "new" on the Chat tab; `m2-needs-me.html` and `m3-needs-me-item.html` show none. Fix: keep the badge on every screen with the bottom tabs until the chat is opened.
6. **Mobile ask paths are undrawn.** `m3-needs-me-item.html` ("Ask a question") and `m8-send-too-late.html` ("Ask the orchestrator", focused) have no destination. Desktop has `07d` and `09f`; mobile has no equivalent. Fix: draw m3 → m10 with the approval strip ("You are asking about…", "Back to the approval"), or write "not drawn" in the README's tablet and mobile section.
7. **`aria-label` on a generic `div`.** `06a-chat-open.html:138` and `m10-chat.html`: `<div class="made" aria-label="Cards in this answer">`. Naming a generic element is not reliable (ARIA 1.2). Fix: `role="group"` with `aria-labelledby` pointing at the `.made-h` text.
8. **Tablet with chat open hides two columns** (`r-06c-chat-1024.png`). Review is cut and Done is off-screen with no visible control. The README tells the user to close the chat to see Done. Fix: a column switcher or a "Done 14 →" cue above the board at 700–1099 px.
9. **`/` does nothing on most screens.** `wb.js:127` returns when there is no `#chat`. Chat panes are absent on `10`, `10b`–`10e`, `09b`–`09e`, `07b`, `07c`, `08` and onboarding, yet the README says "`/` from anywhere outside a field" and `12` lists it. Fix: narrow the claim to the screens that have the chat, or fix it with M1.
10. **README hex counts do not reproduce.** The README says 37 colours in B-chat and 41 in ev0lve-themes. My text scan finds 38 and 52 across all text files (script `/tmp/bchat-r1-hexcmp.py`). The shared value is `#ffffff` only, as the README says. Fix: correct the counts or state the file scope.
11. **Carried from B r4: "Decide again →"** (`m7-send-stopped.html`; PNG byte-identical to B's). The bottom action does not say what it does, and Approve and Decline sit below the fold. Fix: label it "Approve or decline…", or show both buttons in the bottom bar as `m3` does.
12. **The "1 new" chip has no rule** (`04-board.html:27`). "1 new" does not say new what. The README admits there is no rule for two or more messages or for teammate messages. Fix: visible text "1 new reply", "3 new replies", and one README line on teammate messages.
13. **The "working" ring is static** (`06b-chat-thinking.html`, `03b`; `.spin`, `wb.css:426`, no `@keyframes`). It reads as stalled work, although the text carries the status. Fix: add a rotation keyframe; the reduced-motion rule already removes it.
14. **Mobile "Show N more" is under 44 px.** `m11-board-from-chat.html` ("Show 3 more"). `.more` is a 36 px flex item: 6 px and 10 px padding around a 20 px line (`wb.css:141`). The brief's mobile bar is 44 px. Fix: `min-height: 44px` on mobile.

## Flow walk (actions, excluding typing)

| Flow | Path | Actions | Result |
|---|---|---|---|
| Start | `01` → `01b` → `02` → `03` → `03c` → `03c2` → `03d` | about 7, including opening the email link | Finishable. `03` teaches the orchestrator. |
| Board | `04` | 0 | Column counts match the cards drawn. |
| Add card | `04` → `05` → `05b` | 2 plus typing | Todo 5 is 4 drawn plus "Show 1 more". |
| Talk to the orchestrator | `04` → Chat → `06a` → `06b` → `06c` | 2 plus typing | Finishable. New cards are linked by tag and time. M2 after the answer. |
| Mobile chat | `m1` → Chat tab → `m10` → Show → `m11` → Back to chat | 4 | Finishable. |
| Needs me | `04` → Needs me → `07` → `07c` → `08`; ask: `07d` → Back to the approval | 3; ask: 2 | Finishable. Approval content is hidden while asking (see M2's pattern). |
| Mobile approval | `m2` → `m3` → `m5` → `m6` → `m7` / `m8` | 4 | Finishable. Mobile ask undrawn (MINOR 6). |
| Memory | Rail → `09` → result → `09f` → Back to the source | 3 plus typing | Finishable. No chat from here (M1). |
| Settings | Settings → `10` → `10b` / `10c` / `10d` / `10e` | 1–3 | Finishable. Disconnect copy is exact. |

## Rules and premise

- **Board is the default view:** pass. A new project opens the chat (MINOR 4).
- **One automatic orchestrator:** pass (`03d`, `10`, `10c`, `10d`).
- **Needs me holds only human items:** pass (approval, review, decision).
- **Memory verbatim with source:** pass for memory items (`09`, `09f`); open for model-written replies (M3).
- **Attention:** the first screen repeats itself (MINOR 4), and `10` carries a long "Chat answers" block. The other new elements earn their place.
- **Generic and synthetic:** pass. No real names, hosts or keys. The connect dialog masks the key; "Maya's laptop" is a generic label.
- **Premise and owner decision:** distinct from a generic SaaS dashboard. B plus one chat element behind a toggle, as the owner allowed.
- **AA:** no failure found on a core screen in the values I checked. The problems above are about interaction and wording.

## What works

- The toggle is one labelled button with `aria-expanded`, a filled open state, and "1 new" shown only when something is new (`04`, `06a`).
- Error and waiting states (`03b`, `06b`, `06d`) use text plus `role="alert"` or `role="status"`, and each offers a next action.
- `09f`: "Opened at Leon's message from memory. Your question takes the quote along, word for word." is exact, and the quote can be removed.
- `m7` and `m8`: the send-hold copy says exactly what was and was not sent.

Verdict: CHANGES
