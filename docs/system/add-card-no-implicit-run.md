### Adding a card never starts a run by default

- A usability test found that the Add card form defaulted Who to "Any agent". On a board with a Coworker config, every card that arrives unassigned in Todo fires the board's Todo trigger, so a first-time user's first card silently queued an agent run (spend and side effects, no warning).
- Decision: the form defaults Who to the person adding the card. A card assigned to a person is refused by the start predicate, so nothing starts. "Any agent" stays available as an explicit choice, labelled "starts a run now", with a hint line that says the same. A missing or empty Who is sent as that person, never as an unassigned card.
- Not changed: the server's Todo trigger rule. A card that arrives unassigned in Todo on a configured board starts a run for API clients, routines and agents, because that is what a board's Coworker config asks for. Only the browser form's default changed, so the choice to start a run is always visible and deliberate.
