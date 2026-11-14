### Deck Board and card drawer

The optional Deck Board reuses the existing board/card routes and mutation
controller. Four open columns separate personal decisions, active work, review
and shipped work. Only personal decisions carry amber edges. All column totals use quiet grey.
The drawer adds recorded Bot attribution, coworker, version and progress; missing
progress is never filled with inferred successful checks. Detailed fields and
existing move, handoff, assignment removal, start, comment and memory controls
remain reachable. Display cleanup omits evidence locations and ticket/model
clutter while preserving stored content, exact decision payloads and version checks.
The header combines live state totals with reachable ownership filters. Progress uses a plain tree with recorded outcomes, and Reply reveals the existing comment form. On phones a state pill selects one complete column inside its own row and the modal drawer is a
full-height sheet. Classic rendering and server authority stay unchanged.

Generated drawer markup is inserted through replacement callbacks, preserving
literal dollar tokens in card titles, summaries, body text, coworker names and
exact decision values. Edit card wraps only fields and action controls;
Description, Activity, waiting, blockers, runs, context and origin stay in the
resting view, including origin quotes that load after the initial render.

Board phone actions share the page title line. State pills use a separate
full-width scrolling line with a 16px trailing fade; the phone bar keeps its mark
without duplicating the page title. Progress parents rely on their state text
without a second status dot. Desktop filters leave 12px extra before Add card.
