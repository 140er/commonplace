# B + chat: responses to round 5

Terra passed. Haiku's one MAJOR is fixed. Haiku MINORs m1–m5, m7, m9 and m10 are fixed; m6 and m8 are partly fixed, and the two Terra MINORs stay documented limitations (reasons below). Nothing else that passed was changed. Re-rendered: all 78 PNGs. I opened the changed ones: `r2-m1-board-320`, `r-06c-chat-1280`, `r2-04-board-1024`, `03d`, `03e`.

Checks, rerun:
- **Keytest:** ALL PASS, 75 checks (`_src/keytest-results.txt`).
- **Layout check:** now also renders 1366 and 1280 px, phone screens at 320 px, and checks that nothing runs off the screen at phone widths. 306 renders, **0 issues** (`_src/layoutcheck-results.txt`). Its first run with the new widths found what Haiku m4 described: headers at 1280–1366 px losing Add card or the filters, phone action bars at 320 px, and the create-project dialog at 390 px. All are fixed below.
- **Fact check:** no contradictions.

## MAJOR

| # | Finding | Status | Where |
|---|---|---|---|
| Haiku M1 | The phone column row fails reflow at 320 px (hidden sideways scroll) | **Fixed** | `.m-cols` wraps (`flex-wrap: wrap`), and `scrollbar-width: none` and the sideways scroll are removed. Below about 370 px the 44 px tabs take two rows. This applies to every phone board screen (`m1`, `m4`, `m9`, `m10`, `m11*`, `m12*`, which share the row). Re-rendered `r2-m1-board-320`, with **Done 14** fully visible on row two. The README states the reflow width: phone screens work to **320 CSS px** without sideways scrolling. The layout check now renders every phone screen at 320 px and flags anything off-screen. That also caught the bottom action bars at 320 px, which now stack below 360 px. |

## Haiku MINOR

| # | Status | Where |
|---|---|---|
| m1 Offline 10:04 message lost in `03d` | Fixed | `03d` shows it: "Sent when this device came back online, and delivered at 10:11". The dialog says both waiting messages, 10:02 and 10:04, were delivered. |
| m2 `03e` has no Close | Fixed | **Close Esc** beside Make a new setup. The focused action reads **Keep waiting (stay open)**. The keytest's dialog checks for `03e` still pass (focus, Tab trap, Esc return). |
| m3 Unread count differs between screens | Fixed | One unread state per project: `08` now shows **Harbor chat 1 new reply** (as `m2b`), and `09b`, `09d` and `09e` show **Atlas chat 1 new reply** (as `09`). The README names the two exceptions, states before anything was said: `07b` and `09c`. |
| m4 Header at 1280–1399 px drops Add card | Fixed | The header rule now runs to 1399 px: key hints hide, and the filter group takes its own row. Title, Chat, Settings and Add card share the first row. The layout check covers 1280 and 1366 px (`r-06c-chat-1280` re-rendered). |
| m5 Settings an unlabelled icon at 1024–1279 px | Fixed | The word **Settings** stays on the title row (`r2-04-board-1024`, `r-06c-chat-1024`, `r-06c-chat-1100`). One exception, stated in the README: with the chat pushed open below 1100 px on a project with no orchestrator yet (`03b`–`03e` at 1024 px), the long "no orchestrator yet" chip needs the room. |
| m6 The phone chat has no history | Partly | `m10` and `m12b` open with **Show earlier messages**, like `m10b`. No separate phone screen draws the 12:41 quick reply. `r-06c-chat-390` (the same chat rendered at phone width) shows "Quick reply · local model" and its explanation, so the writer label is visible at phone width in a PNG. |
| m7 No divider on the ask paths | Fixed | "New since you last looked" sits above the 14:09 message in `07d` and `m10b`, as in `09f` and `m10-chat-from-needs`. |
| m8 Wrong reason for dropping the phone Memory tab | Corrected | README: B's four-tab bar fit at 390 px, so space was not the reason. B's Memory tab led to a phone screen that was never drawn, and this direction draws none either, so the tab was removed rather than kept dead. The palette route and "phone memory results are not drawn" stay stated. Restoring a tab that leads nowhere would not help a user. |
| m9 Hosted provider looks active | Fixed | `10e`: Local model now says "Still in use: quick replies come from here until a hosted provider is turned on." The Hosted provider status still reads "Off · not set up yet". |
| m10 Wording | Fixed | `09d`: "Search is unavailable right now. Try again in a moment." `11`: "New, from chat 14:03" (no "state:"). |

## Terra MINOR (passed review)

| # | Status | Why |
|---|---|---|
| 1 Phone memory route undrawn | Kept as a limitation | Drawing a phone palette and a phone memory result is new scope in a round asked to stay small. The README lists it under *What I traded away* and *Open risks*. |
| 2 Recovery outcomes described, not pictured | Kept as described | Each outcome is a state already drawn elsewhere (`06b` working, `05b` card added, the connected card in `10`). README flow 4 maps them. One drawn path per flow. |

## Other changes, all caused by the new checks

- **Dialogs below 700 px:** full width less a margin, with actions that wrap. The layout check found the first-run "Create your first project" dialog running off a 390 px screen. Desktop sizes are unchanged.
- **Desktop-only screens:** the layout check skips its phone-width reflow test for the connect and disconnect dialogs, the Settings pages, the palette and the shortcuts sheet. Their phone form is `m13` Settings, or the step is done on a computer (`m12`). They are still checked at every desktop and tablet width. The README says so.

## Hygiene

The mirrored review copies on the branch have home paths, scratch paths and local tool names replaced with generic wording. The originals in the card folder are unchanged.
