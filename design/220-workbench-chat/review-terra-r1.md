# B + chat — adversarial review, round 1

I read the review brief and its addendum, the design and B + chat briefs, direction B's premise and round-4 reviews, the B + chat README and contact sheet, and inspected the screen HTML, shared CSS and interaction script. I opened all 54 PNGs, including the alternate widths, in labelled contact sheets. This is a review of the supplied static design and its demonstrated interactions, not a claim about the product. I did not rerun the supplied keyboard tests or independently compare the theme's hex sets.

## BLOCKING

None.

## MAJOR

1. **The selected chat card and the details panel can disagree — `06c-chat-answer.html` (`wb.js`).** The answer offers A-134, A-135 and A-118 as links to show on the board. The click handler changes the selected board card but does not update `#ctx`, whose content remains A-121. Closing chat therefore reveals the old card's details even after a different card was selected. A user following a request into its newly created work can read or act on the wrong card. Fix: update the context panel to the selected card when the chat row is activated, and retain that selection when closing chat; demonstrate the A-134 and A-118 paths and verify their title, ID and details agree.

2. **Two mobile card links lead to the wrong card — `m10-chat.html` and `m11-board-from-chat.html`.** All three Show links point to the same `m11-board-from-chat.html`, which says “Showing A-134” and selects A-134 in Todo. Choosing A-135 or the linked A-118 in Doing therefore takes the user to a different card. This breaks the new chat-to-board link precisely when the user tries to inspect the work. Fix: carry the chosen card ID and open its actual column and selection; draw or demonstrate at least the A-135 and A-118 outcomes, including the way back to the same chat position.

## MINOR

1. **Shortcut and implementation diverge — `07-needs-me.html`, `wb.js`.** The R shortcut is labelled “Ask a question,” but the key handler only focuses the button; it does not open chat. Either activate the button on R or label the shortcut as moving focus to it. The documented keyboard route should match the result.
2. **The mobile Back button does not demonstrate the promised focus return — `m10-chat.html`, `m1-board.html`.** Back navigates to a separate document, whereas Escape closes the in-page dialog and focuses its trigger. The README claims both return focus to the Chat tab, but the link has no corresponding focus-restoration mechanism. Restore focus on navigation or implement both paths as the same sheet close; test each separately.
3. **Palette constraint needs an explicit decision — `README.md`.** The recorded comparison says white `#FFFFFF` is shared with the theme, while the B + chat brief literally says no shared hex colour. The design is visually neutral; document an approved exception for white or satisfy the literal check.

## Flow and rules check

Approximate actions exclude typing and automatic transitions: signup and project creation take three in-app submissions plus the email link; connect takes two in-app actions plus pasting setup and restarting the agent app; add card takes two; chat takes opening and sending (two); approval takes selecting Needs me, selecting the item, approving and confirming (four); memory takes opening Memory, searching and selecting a source (about three); settings takes one to open, then the chosen change. The static screens cover the principal empty, waiting, failure and recovery states. The board remains the main view, the chat has labelled desktop and mobile entry and exit controls, the Needs me example is a human approval, and the memory result shows its source and exact words. Chat replaces details on desktop, pushes the board at tablet width and becomes a full-screen mobile sheet. Those choices support the premise, subject to the card-selection defects above.

Verdict: CHANGES
