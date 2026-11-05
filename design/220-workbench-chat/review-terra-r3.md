# B + chat — adversarial review, round 3

**Count: BLOCKING 0 · MAJOR 1 · MINOR 2.** This is a review of the supplied design and its demonstrated interactions, not a production accessibility certification. I read the review and designer briefs, direction B's premise, the README, index, both rounds of responses and all four prior reviews. I opened all 66 PNGs in labelled contact sheets and inspected the relevant HTML, CSS, interaction script and recorded keyboard results. The requested branch resolves to `2676cf2ffc499f5d8f79d308bfa38088fa0f4d4f`. The committed design files match the folder except the deliberately sanitized copy of `review-haiku-r1.md`; the new review is not part of that commit. I did not rerun the keyboard test or render new screenshots.

## BLOCKING

None.

## MAJOR

1. **The phone's cross-project Chat entry does not identify its destination — `screens/m2-needs-me.html`, `screens/m10-chat.html`.** Needs me explicitly shows **All projects** and items from Atlas and Harbor, but its bottom control says only **Chat**, with an unqualified “1 new”. It opens Atlas chat (`m10-chat.html?from=needs`); nothing on the originating screen tells the user which project that is or whose reply is counted. A user following a Harbor item can reasonably expect Harbor's conversation and may start a question in Atlas instead. This is the same project-identification problem the r1 fix addressed on desktop, reintroduced in the phone entry. **Fix:** identify the destination and unread count before opening, for example “Atlas chat · 1 new reply”, or require a clearly labelled project selection; specify how it follows the selected item or last project, and draw/test the Harbor case as well as Atlas.

## MINOR

1. **Settings loses the originating place — `screens/m2-needs-me.html`, `screens/m12b-chat-error.html`, `screens/m13-settings.html`.** Both Needs me's Settings control and the error's “Check the agent in Settings” lead to the same sheet, whose only visible exit is “Back to board” (`m1-board.html#m-settings`). Returning from diagnosis discards the error and retry context; returning from Needs me discards that list. Preserve and label the originating destination and restore focus to the initiating control, as the chat sheet already does for Needs me.
2. **The mobile unread update remains silent — `screens/m1-board.html`, `screens/m2-needs-me.html`.** The badge now visibly says “1 new”, with screen-reader text “reply”, but it has no live region. The new desktop status has `role="status"`; the mobile badge does not, so a reply arriving while the user remains on the page is not announced. Add a non-duplicating status announcement naming the project and reply count. This is the unaddressed mobile portion of Haiku r2 MINOR 2.

## Earlier findings and regression check

- **Terra r1:** the chat row opens matching Details, and the three phone Show paths identify their own cards and restore the corresponding row; the R shortcut activates Ask; mobile Back restores focus; the literal colour overlap is resolved. These remain represented in `06c`, `06f`, `06f2`, `m10` and `m11`–`m11c` and the recorded key checks.
- **Terra r2:** the Harbor composer label now agrees with its heading, log and control (`08`). The two approval-related phone questions now have separate contextual sheets and return links (`m10b`, `m10c`); the Needs me chat return goes back to Needs me. The mobile control still fails to name its project before opening: MAJOR 1.
- **Haiku r1:** the desktop project-scoped controls, card details, named message writers, linked-card marker, outline clearing, styled recovery actions, initially closed chat, mobile unread badge, named card groups, column navigation, scoped shortcut, colour-count method, approval wording, reply count rule, animated ring and 44 px mobile more control remain present. The previously undrawn mobile questions are now drawn. The carried 320 px project strip was accepted in B r4 and remains a known limitation.
- **Haiku r2:** the 768 px modal sheet and 390 px full-screen state are drawn; selecting a board card during chat shows its Details and a return strip; the phone approval paths retain context; no-agent, error, empty and Settings screens are drawn; the phone Memory tab is removed; and the README states one phone breakpoint. The 1100–1279 px rule narrows the rail and chat and allows tags to wrap. The desktop live status and visible phone count are added, but mobile announcement remains MINOR 2. The shortcuts reference, release wording, shorter Settings and empty-chat copy, memory error and reproducible palette scope are addressed. The cross-project unread limitation is disclosed, and the no-agent card action names Todo; no separate Basic-mode receipt or created-card frame is drawn. I accept those as documented trade-offs rather than new MAJORs.
- **B's previously accepted flows:** signup, connection, board, card creation, approval with hold and stop, exact-source memory, and automatic orchestrator settings remain drawn. I found no further regression in those supplied frames. The new phone cross-project entry and Settings return need the changes above.

## Flow walk

Actions exclude typing and automatic transitions; these are the pictured design paths, not a claim every static control is implemented.

| Flow | Path and approximate actions | Assessment |
|---|---|---|
| Start | `01` → `01b` → `02` → `03` → `03c` → `03c2` → `03d`, about 7–8 including email and external agent setup | Teaches the first agent and shows waiting, delivery and failure. |
| Board | `04`, no action | Board is the default; blocked and human-review work are visible. |
| Add card | `04` → `05` → `05b`, 2 | Card placement and confirmation are shown. |
| Chat and work | `04` → `06a` → `06b` → `06c` → `06f`, about 3 | Request, response, created and linked cards, matching Details and return are drawn. Phone cross-project entry has MAJOR 1. |
| Needs me | `07` → `07c` → `08`, about 3–4; optional `07d` question and return, 2 more | Confirmation, hold and Stop are explained; phone question retains its approval context. |
| Memory | `09` → result → `09f`, about 3 including opening Memory | Exact quote, author, source and time are shown; the quote accompanies the question. |
| Settings | `10` → mode or agent action, about 1–4 | Automatic selection and disconnect effects are explained; phone return has MINOR 1. |

## Accessibility, states and constraints

The prior computed CSS pairs remain: `--text-3` **#62646C** on `--agent-bg` **#EFEAFA** is about **5.01:1**; the focus accent **#2E43C0** on the surface **#FEFEFC** is about **7.76:1**. The desktop `.chat-toggle` has `min-height: 32px`; `.m-chat-log .made a` has `min-height: 48px`, `.m-chat-in .btn` has `min-width` and `min-height: 44px`, and `.m-acts .btn` has `min-height: 44px` (`wb.css`). The focus outline is 3 px, the working ring is disabled by the reduced-motion rule, and the supplied keyboard log reports 54 checks passing, including sheet traps and returns. These are targeted checks; I did not independently run the interaction suite or audit every control.

The chat is labelled and closed on the default board, with explicit open/close controls; the tablet modal and mobile sheet have visible return paths. Empty, waiting, thinking and error states are drawn. Needs me contains human decisions; memory examples retain source and exact text; the one orchestrator is automatic when an agent is connected. The desktop project-specific control, card provenance and named writers support the B + chat premise. The synthetic examples remain generic.

I independently ran the supplied scoped colour comparison: 36 design colours across 64 files, 336 theme values across 93 files, and **no shared hex values**. The reviewed screenshots show no apparent borrowed theme texture or imagery. This checks the recorded literal palette rule, not every possible visual resemblance.

Verdict: CHANGES
