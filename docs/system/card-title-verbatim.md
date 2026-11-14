### A card title keeps the "#NNN" its author typed

A card created as "#599 retest" was stored with that exact title but the Deck board showed it as "retest": the Deck's display copy hides internal references such as PR and card numbers, and it ran over card titles as well as over summaries and bodies. The API and the Add card form never changed the text; only the Deck's view of it did.

Titles now take a display path of their own (`cardTitle` in `deck-board.js`) that still hides file paths, evidence lines and model names but leaves "#NNN" alone. It is used for the board card, the "Added ..." notice, the card menu heading and the drawer heading. Summaries, bodies, Today previews and other Deck copy keep hiding references as before. Storage and the card API are untouched, and a test now pins both that a title round-trips verbatim through create and read and that the Deck shows it as typed.
