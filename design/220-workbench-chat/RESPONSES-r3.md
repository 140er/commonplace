# B + chat: responses to round 3

Both reviews returned CHANGES with no BLOCKING. All three MAJORs are fixed and drawn. Every Haiku and Terra MINOR is fixed, or the false claim behind it is corrected, except Haiku 3, which I settled by correcting the README. Details are below. All 76 PNGs were re-rendered. I opened the changed and new ones: `06g`, `10f`, `r-06f-chat-768`, `r-06c-chat-1280`, `07c`, `m2b`, `m10h`, `m13b`, `r1-04-board-320`. The live keyboard test `_src/keytest.cjs` is **ALL PASS, 65 checks** (`_src/keytest-results.txt`).

## MAJOR

| # | Finding | Status | Where |
|---|---|---|---|
| Terra M1 | The phone Chat tab on the all-projects Needs me doesn't say where it goes | **Fixed** | On Needs me the tab names its destination and count: **Atlas chat 1 new** (accessible name "Atlas chat 1 new reply", plus a `role="status"` line "1 new reply in Atlas chat"). It follows the project of the item last opened in the list, else the last project you had open. Drawn: `m2-needs-me` (Atlas) → `m10-chat-from-needs`, and **the Harbor case**: `m2b-needs-me-harbor` (the Harbor decision marked "Last opened", tab **Harbor chat**) → `m10h-chat-harbor`, with heading, log and composer all "Harbor chat" and **Back to Needs me**. On the board the tab stays **Chat**, because the project is in the top bar. Keytest checks both: tab text, sheet heading, composer label, back label and focus return. |
| Haiku M1 | "No answer yet" recovery leads nowhere | **Fixed** | `06d` and `m12b` now lead with **Show how to restart the agent**, then **Ask again**, then **Make it a card now (adds it to Todo)**. The first goes to a new Settings state, **connected but not answering since 14:02**: `10f-settings-not-answering` (desktop) and `m13b-settings-not-answering` (phone). Each has an amber orchestrator card ("Not answering since 14:02 · connected · last active 14:03 · your 14:02 message is kept"), the three restart steps (adapted from `03e` to an agent that was connected), **Check again**, and **Back to the chat**, which returns to the same button. README flow 4. Keytest checks the button order, the amber state, the 3 steps and the return, on desktop and phone. |
| Haiku M2 | Chat results that had no screen | **Fixed** | Drawn as static markup, with no script needed: `06g-chat-board-pick` (1440, A-121 picked on the board with the chat open, with the strip "You picked A-121 on the board. The chat is one click away."). `r-06f-chat-1024` (chat row → Details in the pushed slot) and `r-06f-chat-768` (as a modal sheet): `06f` now carries the pushed state in its markup, so it renders correctly at each width. `m10-chat-from-needs` (static **Back to Needs me**). All are in `index.html` and README flows 4 and 5 and the tablet and phone sections. Keytest checks `06g` (ID and title match), `06f` at 1024 (beside the board) and at 768 (modal). |

## Haiku MINOR

| # | Status | Where |
|---|---|---|
| 1 Tag clipped at 1280 px | Fixed | Card tags may wrap up to 1399 px with the chat open. Drawn: `r-06c-chat-1280`. |
| 2 Response claims that the screens don't show | Fixed / corrected | `06e` now names quick replies ("Short “Quick reply” answers come from the model chosen in Settings."). **Correction:** in `11`, `11b` and `12` the palette or the shortcuts dialog covers A-118's "Linked in chat" tag. RESPONSES-r1 was wrong to cite those frames; the tag is visible in `06c`, `06f`, `06f2`, `06g` and `m11c`. |
| 3 Toggle name includes the count | README corrected | The count stays inside the control: the name "Chat 1 new reply" is useful while something is new. The README now says so instead of claiming a short name. A space before the count makes the name read "Chat 1 new reply", not "Chat1 new reply". The keytest caught that on the phone tab. |
| 4 No 1100 px frame; hint breaks | Fixed | Drawn: `r-06c-chat-1100`. The composer hint's two parts never break inside a phrase (`white-space: nowrap` per part; the row wraps between them). |
| 5 Phone memory route not drawn | README corrected | The sentence now says there is no phone memory search or phone card-detail view in this direction; memory appears on the phone only as quotes in chat. |
| 6 "Quick reply" not explained where it appears | Fixed | Under the quick reply: "Written by the model chosen in Settings, not by the orchestrator." (`06a`, `06c`, and every chat that shows it). |
| 7 "Make it a card now" loses its destination | Fixed | "(adds it to Todo)" on `06d` and `m12b` too. |
| 8 Details button does nothing in the prototype | Fixed | Wired in `wb.js`. At 700–1099 px it opens B's modal Details sheet with focus on Hide; Esc or Hide close it back to Details. Keytest at 1024 px. The README states what a tablet card tap does. |
| 9 README says "not modal on desktop or tablet" | Fixed | It now reads "not modal at 1024 px and up; a modal dialog sheet below 1024 px", consistent everywhere. |
| 10 A shortcut key wraps | Fixed | `kbd { white-space: nowrap }`. |
| 11 Confirm covers the note field | Fixed | When the confirm opens (and in `07c`), the details scroll so the note field sits fully above it. Keytest checks the positions. |
| 12 `m7` primary action is ambiguous | Fixed | The button reads **Approve or decline**, without the arrow, and opens `m3`, where both decisions are. |
| 13 320 px project strip (carried) | Fixed | The project list wraps below 700 px, so all four projects and their counts show (`r1-04-board-320`). |
| 14 Tapping outside the tablet chat does nothing | Fixed | A tap on the dimmed area closes the chat; on a card sheet opened from the chat it goes back to the chat. Keytest at 768 px. README tablet section. |

## Terra MINOR

| # | Status | Where |
|---|---|---|
| 1 Settings loses the originating place | Fixed | From the error: **Back to the chat** → `m12b`, with focus on the restart button (static, `m13b`). From Needs me: the sheet reads **Back to Needs me** and returns to `m2` with focus on Settings (keytest). From the board: Back to board, as before. |
| 2 Mobile unread update is silent | Fixed | The phone tab bar carries a `role="status"` line, "1 new reply in Atlas chat" (or Harbor), outside the link (keytest). |

## Hygiene

The mirrored review copies on the branch have home paths, scratch paths and local tool names replaced with generic wording. The originals in the card folder are unchanged.
