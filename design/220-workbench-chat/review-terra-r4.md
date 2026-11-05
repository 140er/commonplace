# B + chat — adversarial review, round 4

**Count: BLOCKING 0 · MAJOR 1 · MINOR 1.** The review concerns the static design and its demonstrated interactions, not the finished product.

## MAJOR

1. **Escape from phone recovery returns to the board instead of the failed message — `screens/m12b-chat-error.html`, `screens/m13b-settings-not-answering.html`, `screens/wb.js`.** The error screen opens restart instructions whose visible Back link correctly returns to the failed chat and its restart button. But the shared Escape handler hides the Settings dialog, removes `inert` and focuses `data-return-focus="m-settings"` in the underlying board markup. A keyboard user who closes the dialog with Escape lands on the board, loses the error and retry context, and cannot follow the labelled recovery return. The round-three fix and test cover the Back link, not Escape. **Fix:** make Escape follow the same origin-aware return as Back, including focus on `#restart-agent`; check this path separately from the board- and Needs-me-origin Settings paths.

## MINOR

1. **Harbor's Settings return loses the last-opened project — `screens/m2b-needs-me-harbor.html`, `screens/m13-settings.html`.** The Harbor Needs me screen opens Settings with `?from=needs`, but the return handler always links to `m2-needs-me.html`, the Atlas state. On return, the Chat destination and last-opened item change to Atlas. Preserve the originating Needs me state and focus on its Settings control, as the Harbor chat return already does.

## Earlier findings and regression check

- **Terra r1–r3:** matching desktop card details, distinct mobile card destinations and return focus, R, Harbor's composer label, contextual phone approval chats, named Atlas/Harbor phone chat destinations and the mobile live-status markup are present. The literal palette overlap is resolved. The new Settings Back links address r3's originating-place finding for the pictured Atlas and error paths, but the keyboard and Harbor cases above remain.
- **Haiku r1–r2:** project-labelled desktop chat, clear message authorship, linked-card markers, focus/hover outline clearing, closed initial chat, mobile unread count, group labels, shortcuts, reduced motion, phone states and Settings, responsive sheet, contextual approval chat, and the selected-card Details strip are represented. The README records the cross-project unread limitation and the lack of a separate Basic receipt frame. This remains a design trade-off, not a new major finding.
- **Haiku r3:** the restart instructions and Back link are drawn on desktop and phone, and the previously missing board-pick, tablet Details and Needs me return frames exist. The 1100/1280 frames, wrapped tags, quick-reply explanation, destination text, tablet Details handler, confirm-note layout, 320 px project strip and scrim dismissal are present. The corrected README no longer promises phone memory search or card details. The new phone recovery's Escape path has the MAJOR above.
- **B's accepted baseline:** sign-up, connection, board, adding work, approval and hold, memory with source, and settings remain drawn. I found no other substantive regression in those pictured paths. The one automatic orchestrator is explained; Needs me contains human decisions; the board opens with chat closed; source and verbatim text are shown in memory.

## Flow and state walk

Counts exclude typing and automatic transitions. Start and connect: `01` → `01b` → `02` → `03` → `03c` → `03c2` → `03d`, about 7 actions plus external setup. Add card: `04` → `05` → `05b`, 2. Chat: `04` → `06a` → `06b` → `06c` → `06f`, about 3; the board labels new and linked work and Details quotes the originating request. Needs me: `07` → `07c` → `08`, about 3–4; phone contextual questions retain the approval. Memory: `09` → result → `09f`, about 3. Settings: `10` → choice, about 2. The disconnected, empty, thinking and error states are drawn, including recovery; keyboard recovery has the defect above.

At 1024 px chat pushes the board with column navigation; at 768 px it is a modal sheet; at 390 px the phone screen is full-screen with a labelled return. The board remains the default. The design retains the Workbench premise and adds a discoverable project chat without a permanent extra column.

## Accessibility and verification

Targeted CSS checks: `--text-3: #62646C` on `--agent-bg: #EFEAFA` is approximately **5.01:1**; the focus accent `#2E43C0` on surface `#FEFEFC` is approximately **7.76:1**. `:focus-visible` uses a 3 px outline; `.chat-toggle` has a 32 px minimum height, phone Back has 44 px, and the phone send control has 44 px minimum width and height. The reduced-motion rule stops animation. Labels, roles, focus and targets were inspected for the relevant chat and recovery paths; this is not a complete WCAG audit or a screen-reader or touch-device test.

I read the review, design and B + chat briefs, direction premise, README, index, all three response files and all six earlier reviews; inspected the relevant HTML, CSS and interaction code; and opened all 76 PNGs in labelled contact sheets. The specified branch resolves to `2b6e28ddf9ee796b5bc2237d7e5cf7ccdeeabe1e`. Its 162 design-tree files match the supplied folder except the deliberately sanitized copies of `review-haiku-r1.md` and `review-haiku-r3.md`. The scoped colour comparison reports 36 design colours, 336 theme values and no shared hex values; the screenshots show no apparent theme textures. The recorded keyboard run reports 65 passes; I inspected its coverage but did not rerun it. The Escape finding follows the shipped handler and markup; it is not covered by the recorded phone recovery test.

Verdict: CHANGES
