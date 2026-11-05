# B + chat: responses to round 1

Both reviews returned CHANGES with no BLOCKING. Every MAJOR is fixed. Of the MINORs, Haiku 1–5 and 7–14 and Terra 1–3 are fixed; Haiku 6 is only written down in the README, not drawn (reason below). All 58 PNGs were re-rendered, and I opened the changed ones: `03`, `04`, `06a`, `06c`, `06f`, `07`, `07d`, `09`, `10`, `m2`, `m11c`, `r-06c-chat-1024`. The live keyboard test `_src/keytest.cjs` is **ALL PASS, 41 checks** (`_src/keytest-results.txt`).

The orchestrator also asked for two things, both done:
- **Palette:** plain white is now near-white `#FEFEFC` everywhere, so the literal "no shared hex" check passes. The comparison and its file scope are recorded in the README: 36 colours here, 336 in the theme folder, **shared: none**.
- **Generic wording:** the README and `_src` no longer name local tools or home paths. `shoot.sh` runs as `bash _src/shoot.sh`, with an optional `BROWSER_PROFILE`.

## MAJOR

| # | Finding | Status | Where |
|---|---|---|---|
| Haiku M1 | Needs me and Memory have no project chat control or unread signal | **Fixed** | Needs me and Memory headers now carry the same control, named for its project: **Atlas chat 1 new reply** (`07`, `09`; also `07b`, `07c`, `09b`–`09e`; **Harbor chat** on `08`, where a Harbor item is selected). It opens the project chat with its newest messages. The "You are asking about…" strip and the memory quote appear only for their own openers (Ask a question, Open in chat); the keytest checks both. `/` works on these screens too. README: *Where the chat control lives*, IA, flows 5 and 6. |
| Haiku M2 + Terra MAJOR 1 | A chat card row only highlights; Details keep showing A-121 | **Fixed** | Activating a chat row (click or Enter) opens **that card's Details** in the right-hand slot and selects it on the board. A strip reads "Opened from Atlas chat, the 14:03 answer" with **Back to chat**, which reopens the chat with focus on the same row. Details are built from the same data as the card (one `<template>` per card), so ID and title always agree, and selecting a board card (click or Enter) updates Details too. Closing the chat keeps the chosen card. New screens: `06f-chat-card-details` (A-134) and `06f2-chat-card-details-a118` (A-118 in Doing). The keytest checks A-134 → Back to chat → A-118 → close the chat → a board card, comparing ID and title each time, plus both static screens. At 1024 px the card opens in the same pushed slot, not a modal (keytest). |
| Terra MAJOR 2 | Mobile Show links all lead to A-134 | **Fixed** | `m10` rows link to their own screens: `m11-board-from-chat` (A-134, Todo), `m11b-board-from-chat-a135` (A-135, Todo) and `m11c-board-from-chat-a118` (A-118 on the Doing tab), each with the card selected and outlined. **Back to chat** goes to `m10-chat.html#row-A-…`, and the page focuses that same row. The keytest checks all three paths: column, selection, focus and return. |
| Haiku M3 | Replies are labelled "Atlas orchestrator" while a model writes quick replies | **Fixed** | Every message names its writer. The 12:41 status answer is now **Quick reply · local model** (neutral chat icon, no agent tag), and its wording only reports what is on the board. The 14:03 answer that made cards stays **Atlas orchestrator**. `09` gains a 4th result with the same source line: "Atlas chat · Quick reply · local model · today, 12:41". `10` says quick replies are saved in chat and memory under their own name, never the orchestrator's. `10e` adds that hosted replies are saved labelled "Quick reply · hosted provider". `06e` explains it in one sentence. |

## Haiku MINOR

| # | Finding | Status | Where |
|---|---|---|---|
| 1 | Linked card has no board marker | Fixed | A-118 shows **Linked in chat · 14:03** (link icon, text) in `06c`, `06f`, `06f2`, `11`, `11b`, `12`, `m11c`. |
| 2 | Outline sticks after focus leaves | Fixed | `wb.js` clears it on `focusout` and `mouseout`. Keytest case: focus A-135 → outlined; focus the box → none; hover A-118 → outlined; pointer away → none. |
| 3 | Recovery actions look like body text | Fixed | `06d`: **Check the agent in Settings** is a bordered button with an icon. `03c`: "Can't copy? Show the setup to select it" is underlined, in body colour. |
| 4 | First screen repeats itself; chat opens by default | Fixed | `03` starts with the chat **closed**. The step strip is the one explanation and has the one Connect button. The chat callout is cut to one line, and `03b` drops its second Connect button. The connect dialogs `03c`–`03e` show the waiting message behind them, and `03d` shows it delivered. |
| 5 | Mobile unread badge lost on Needs me | Fixed | The **new** badge appears on every screen with the tab bar (`m1`, `m2`) until the chat is opened (`m11` has none). |
| 6 | Mobile ask paths undrawn | Not drawn, written down | README, flow 5.5: "Ask a question" on `m3` and "Ask the orchestrator" on `m8` open the `m10` sheet with the same "You are asking about…" strip as `07d`. Not drawn, to keep one drawn path per flow; the desktop `07d` and the mobile `m10` sheet already show both halves. |
| 7 | `aria-label` on a generic `div` | Fixed | The card lists in messages are `role="group"`, `aria-labelledby` their heading (`06a`–`06f`, `m10`). |
| 8 | Tablet chat hides two columns | Fixed | At 700–1099 px with the chat (or a chat card) open, a **column jump bar** (Todo · Doing · Review · Done 14) sits above the board (`r-06c-chat-1024`). |
| 9 | `/` does nothing on most screens | Fixed | `/` now works on the board, Needs me and Memory screens (they all have the control). The README states the scope and that Settings has none. |
| 10 | README hex counts don't reproduce | Fixed | `_src/hexcheck.py` prints its file scope. The README records the scope and result (36 / 336 / shared none). Id selectors like `#add-card` are no longer miscounted as colours. |
| 11 | `m7` "Decide again →" | Fixed | The button now reads **Approve or decline…**. |
| 12 | "1 new" has no rule | Fixed | Visible text **1 new reply** (desktop). README rule: "N new replies" counts answers from the orchestrator or a quick reply; teammate messages don't raise it. |
| 13 | The working ring is static | Fixed | `.spin` turns (`@keyframes wb-spin`), and the existing `prefers-reduced-motion` rule stops it. The README motion note is updated. |
| 14 | Mobile "Show N more" under 44 px | Fixed | `.mobile .more` has `min-height: 44px`. |

## Terra MINOR

| # | Finding | Status | Where |
|---|---|---|---|
| 1 | R only focuses Ask a question | Fixed | R now activates it, which opens the chat with the approval strip (keytest: both openers `aria-expanded="true"`, strip shown). |
| 2 | Mobile Back has no focus return | Fixed | Back to board links to `m1-board.html#m-chat-tab`, and `wb.js` focuses the element named in the address on load. The keytest checks Back (→ `#m-chat-tab`) and Esc separately, and Back to chat (→ the same row). |
| 3 | White is shared with the theme | Fixed | See "Palette" above. |

## Carried from B r4, still open

- **320 px project strip** (B r4 minor 9): not touched this round. It is unrelated to the chat, and B passed with it.
