### Moving a card says where it really went

- A usability re-test found that Move to In review showed the card under Needs you (In review stayed at 0), that the notice after a move or an add named a column other than the one the card sat in, that "Active - Start it" read as if a run starts, and that the menu stayed open for several seconds after a choice.
- Cause (a mapping bug, not a wording choice): the owner queue read by the Board counted every card in Review with no recipient as a decision waiting on the owner, including a review the person moved themself, while the card's own `need` already said it was not waiting on anyone. The queue now skips that card, so the two agree; what the states mean is unchanged.
- Decision: after a move or an add the notice is built from the card the server answered with, not from a queue read before. A move that landed elsewhere than asked says so. The menu closes as soon as the server answers and the board refresh follows without holding it open; focus returns to the card's actions button afterwards.
- The Todo to Active entry says "Marks it as started" because a person's move starts no run; typed work with an assigned holder says "Starts its assigned work", which is what the server does for it.
- Not changed: Add card's Who field, the 409 handling, or what each column means.

### One name for each status (card #667)

- Problem: the Move menu listed three lanes (Active, In review, Shipped) while the status select listed five statuses (Backlog to Done) and the card face said "Ready"; Admin listed projects under "Bots".
- Decision: statuses are always named Backlog, Todo, Doing, Review, Done, as the status select, the tools and the server's refusal text already say. Lanes keep their names (Waiting on you, Active, In review, Shipped) and are only a view. The Move menu lists the five statuses with the lane each lands in; the moves the server refuses for a person are greyed with the first step. The card face and drawer header add the real status after the summary word. Stored values, routes and tool names are unchanged. `docs/glossary.md` is the one page that maps the app's words to the tool names.
