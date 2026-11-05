# B + chat — adversarial review, round 2

**Count: BLOCKING 0 · MAJOR 1 · MINOR 2.** This reviews the supplied design, not a production implementation. I read the briefs, premise, README, index, both r1 reviews and responses, and B's r4 reviews; opened all 58 PNGs in labelled contact sheets; inspected the relevant HTML, CSS and script. The 126 files in the specified commit's design folder match the corresponding local files byte for byte. The recorded keyboard test reports ALL PASS; I inspected its results and the interaction code but did not rerun it.

## BLOCKING

None.

## MAJOR

1. **Harbor's chat composer is labelled as Atlas — `08-needs-me-done.html`.** The selected item, toggle, chat heading and log say Harbor, but the label for `#ci` says **“Message to Atlas chat”**. A screen-reader user opening the new project-scoped chat is told that typing will address a different project. This undermines the very project distinction the r1 change introduced and can cause a user to hesitate or send project information under a false understanding of its destination. **Fix:** label the composer “Message to Harbor chat” from the same project data as the heading and log; check the accessible name whenever selection changes projects.

## MINOR

1. **The mobile contextual ask is still only described — `m3-needs-me-item.html`, `m8-send-too-late.html`, `m10-chat.html`.** The response adopts Haiku's documentation alternative, but the pictured mobile chat has only “Back to board”; it has neither the approval context strip nor a return to the approval. The two source controls also do not demonstrate that transition. Draw the contextual sheet and return path so a reviewer can follow it from the PNGs, including after a question about an already released approval.
2. **A mobile chat opened from Needs me returns to the board — `m2-needs-me.html`, `m10-chat.html`.** The Chat tab opens the same sheet whose Back link and Escape fallback both return to Board, losing the user's Needs me place. Preserve the initiating destination and restore focus to its Chat tab; label the back action accordingly.

## Round-one check

- Terra's two MAJORs: the desktop chat row now selects the matching card and constructs its matching Details; A-134 and A-118 are drawn with a Back to chat route. The three mobile rows have distinct destinations and same-row return links. R now activates Ask a question; the mobile Back link has a focus target; the literal palette check reports no shared hex values.
- Haiku's three MAJORs: Needs me and Memory now have project-labelled toggles; card rows lead to Details; quick replies and orchestrator messages have separate writer labels, including in Memory and Settings. The Harbor accessible-label mismatch above is a new defect in the first fix.
- Haiku's MINORs: the linked-card marker, clearing outline, recovery styling, closed initial chat, mobile unread badge, named groups, tablet jump bar, shortcut scope, palette counts, mobile approval wording, explicit reply-count rule, animated ring and mobile more-control sizing are addressed in the named screens, CSS or script. The mobile contextual ask remains undrawn, as the response explicitly says; see MINOR 1. The earlier 320 px project-strip issue remains open.
- B's earlier acceptance remains substantially intact in the supplied screens: onboarding and connection states, confirmation and hold, automatic orchestrator, verbatim memory with source, board and settings remain represented. The new Harbor label is the accessibility regression identified above.

## Flows, accessibility and constraints

Approximate actions exclude typing and automatic transitions: signup and project creation take three submissions plus the email link; agent connection takes about two in-app actions plus setup in the agent app; add card takes two; open and send chat takes two; approval takes about four including confirmation; find a memory takes about three; settings takes one to open plus the chosen change. The board remains the default, Needs me contains human decisions, and the chat/card relationship is visible in both directions. The no-agent, waiting, error and empty chat states have recovery text. Mobile has a full-screen chat, and tablet has a column jump bar.

The CSS specifies a 32 px chat toggle, 28 px small buttons, 48 px mobile chat rows, 44 px mobile send and more controls, and a 3 px focus outline using `#2E43C0`. The reduced-motion rule disables animation with `!important`; the working ring otherwise uses a one-second rotation. These are targeted checks, not a complete accessibility audit. The independently run palette script reports 36 design colours, 336 theme colours, and no shared hex values in its stated file scope. The supplied examples are synthetic; no theme textures are apparent in the screenshots.

Verdict: CHANGES
