# B + chat: responses to round 2

Both reviews returned CHANGES with no BLOCKING. All six MAJORs are fixed and drawn. Of the MINORs, all are fixed except Haiku 3 and Haiku 10, which are partly addressed (reasons below). All 66 PNGs were re-rendered, and I opened the changed ones: `r-06c-chat-768`, `r-06c-chat-390`, `m1`, `m10b`, `m10c`, `m12`, `m12b`, `m13`. I also opened a probe at 1100×800 (not kept). The live keyboard test `_src/keytest.cjs` is **ALL PASS, 54 checks** (`_src/keytest-results.txt`).

## MAJOR

| # | Finding | Status | Where |
|---|---|---|---|
| Terra M1 | Harbor's composer is labelled "Message to Atlas chat" | **Fixed** | The composer label now comes from the same project value as the chat heading and the log name (`chatpane(proj=…)`), so `08` reads "Message to Harbor chat". Keytest: on `07` and `08` it opens the chat and checks that the label, heading, log name and control all name the same project (Atlas, then Harbor). |
| Haiku M1 | At 700–1023 px the pushed chat leaves the board unusable | **Fixed** | Below 1024 px the chat is a **modal sheet**: scrim, `role="dialog"`, `aria-modal`, rail and board `inert`, Tab kept inside, and Esc or Close chat return focus to the control. That is the same model as B's tablet Details sheet. The push stays at 1024–1099 px. A chat row opened below 1024 px shows its Details as the same kind of sheet, and Esc goes back to that row. Drawn: `r-06c-chat-768` (06c at 768×1024). Keytest at 768 px: dialog, scrim, inert board, a 25-press Tab trap, Esc return, and the row → details → Esc path. |
| Haiku M2 | A board card chosen with the chat open gives no visible result | **Fixed** | Click or Enter on a board card while the chat is open shows that card's Details in the same slot. The strip says "You picked A-121 on the board. The chat is one click away." and has **Back to chat**, which reopens the chat with the cursor in the box. Keytest: a click on A-121 and Enter on A-124, each checking the ID and title in Details against the selected card, plus Back to chat. |
| Haiku M3 + Terra MINOR 1 | The phone ask path loses the approval | **Fixed** | `m3` **Ask a question** opens `m10b-chat-ask-approval`, with "You are asking about *Send beta 0.9 to 40 testers?* Approve or decline it in Needs me" and **Back to the approval** (top left), which returns to `m3` with focus on Ask. `m8` **Ask the orchestrator** opens `m10c-chat-ask-released`, with "…released at 14:22. It can't be recalled here." and the same return to `m8`. That covers the question after a released send too. Keytest checks both round trips. |
| Haiku M4 | The phone chat shows only the happy path; no phone Settings or Memory | **Fixed** | Phone chat states: `m12-chat-no-agent` (Waiting for an agent, **Make it a card now (adds it to Todo)**, "Connect an agent on a computer"), `m12b-chat-error` (Ask again, Make it a card now, **Check the agent in Settings**) and `m12c-chat-empty`. The top bar has a labelled **Settings** button (44 px), which opens one sheet, `m13-settings`: orchestrator, chat-answer mode, agents, and "connect or disconnect on a computer". **Memory tab dropped** from the phone bar (Board / Chat / Needs me), the simpler option, and the README says so. Keytest: Settings → `m13` → back with focus on Settings. |
| Haiku M5 | Two phone behaviours, no rule; the 390 px render was blank | **Fixed** | One rule, stated in the README: below 700 px the `m` screens apply, and the chat is a full-screen sheet. The desktop markup at phone widths now shows the same full-screen modal sheet from the top, never a chat stacked under the board (the old stacking rule is removed). `r-06c-chat-390` is rendered from the shipped `06c` markup at 390×844 and shows the chat. Keytest at 390 px: the sheet sits at (0,0), 390 wide, at least 840 tall, modal, with the board inert. |

## Haiku MINOR

| # | Status | Where |
|---|---|---|
| 1 Board cramped at 1100–1279 px | Fixed | With the chat open, the rail is 188 px and the chat 304 px, card tags may wrap, and IDs never break (`.cid` nowrap). Checked in a 1100×800 probe: four readable columns, with full "From chat · 14:03" tags. |
| 2 New replies not announced; phone badge says only "new" | Fixed | A `role="status"` line next to the control says "1 new reply in Atlas chat" (outside the button, so the button's name stays short). The phone badge reads **1 new**. |
| 3 Needs me and Memory name one project | Partly | Kept: the control is scoped to the selected item's project. A combined "2 new replies in 2 projects" control would have to choose which chat to open, and would put one more thing in front of the user. The README states the rule and lists it as a risk. |
| 4 Shortcuts reference incomplete | Fixed | `12`'s Esc row now reads "Close a dialog, the palette, or the chat (focus in the chat)". Keytest adds `]` closing the chat. |
| 5 "Ask the orchestrator" promises a change | Fixed | `m8`: "This can't be recalled here. To follow up, ask the orchestrator." |
| 6 Settings "Chat answers" too heavy | Fixed | One lead sentence; the rest sits behind a "What quick replies are" disclosure (`<details>`). |
| 7 Empty chat copy is five sentences | Fixed | `06e` and `m12c`: two sentences plus the three starter chips. |
| 8 Memory error copy is internal | Fixed | `09d`: "Search is unavailable right now. This usually takes about 2 minutes." The second sentence, "Your memories are safe and unchanged.", was already there. |
| 9 Theme colour count doesn't reproduce | Fixed | The README states the exact pattern (`#[0-9a-fA-F]{3,8}(?![\w-])`), the scope and the counts (36 / 336 / none shared). It also notes that a plainer pattern gives larger counts (376) and still no shared value. |
| 10 Two promised outcomes undrawn | Partly | "Make it a card now" now says where the card goes: **(adds it to Todo)** in `03b` and `m12`. The Basic receipt pattern is the one drawn in `03d` ("Delivered to the orchestrator at 10:11. It answers here."). No separate Basic-mode frame, to keep one drawn path per flow. |

## Terra MINOR

| # | Status | Where |
|---|---|---|
| 1 Mobile contextual ask only described | Fixed | See Haiku M3: `m10b`, `m10c`. |
| 2 Chat opened from Needs me returns to the board | Fixed | The Chat tab on Needs me opens the chat with **Back to Needs me**, which returns to `m2` with focus on the Chat tab (keytest). |

## Hygiene

The review files mirrored onto the branch have home paths and local tool names replaced with generic wording, in the mirrored copies only. The originals in the card folder are unchanged. The README and `_src` were already generic.
