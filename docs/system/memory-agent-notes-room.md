### Agent notes on the Memory page

Agent connections save notes to the inbox wing, which no project owns, so the
Memory page (which searched each project's own wing) never showed them. The page
now also asks `GET /api/memory/rooms`; the server lists the inbox as an "Agent
notes" room only when the caller may search memory and read that wing under the
same policy as any other wing, and never when the wing is private. The page adds
it as a notes-only room beside the project rooms and searches it with the existing
wing-scoped search. Words are shown exactly as saved. Where agents write, the
privacy rules and every other read are unchanged. Minimal labelling choice: the
existing room chip and project filter, with the name "Agent notes"; further visual
treatment belongs to the design work.
