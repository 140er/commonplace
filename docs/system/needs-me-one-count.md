### One definition of "needs me" in the web shell

Today said "7 waiting on you" while the Board's first column said "Needs you 18": the
counts came from different places. The Board grouped cards by each card's own `need`
flag, while Today and the badges read the owner queue's `count` field. The shell now has
one definition, in `needs-me.js`: a card needs the owner when the owner queue lists it.
Today, Home, the phone and rail badges and the Deck Board all derive from that list. The
count is the number of distinct listed cards and a stale `count` field is ignored. The
Board keeps a card's own flag only when no queue is loaded (a member who cannot read it),
and when a project holds only part of the queue it says how many cards wait across all
projects.

Today's cards were walls of stored text with Hold and Reply, and Approve only after
Reply. Each card now shows a title, a one or two line preview and the main action (Approve,
Answer, or Open) beside Hold and Reply; the full stored words are shown unedited behind
Show full text. This is display only: titles, bodies, choices and versions are submitted
and stored as they were. Display text that hides a card number or a path no longer leaves
empty brackets or dangling words behind it.
