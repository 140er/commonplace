### The Add card form can hand a card to a bot

- A usability test found that Who offered only Me and Any agent, so a task could not be given to a specific bot.
- Decision: the server lists the agents a person may hand a card to, with a `starts_run` answer taken from the same rules as an owner's assignment (owner, configured Coworker, the Coworker's worker). The form shows that answer in the option and in the hint, so the person is told before adding whether a run starts.
- Not changed: Who still defaults to Me, so adding a card starts nothing unless the person picks an agent the form says starts now. The assignment rules themselves are untouched: an owner's hand-over still claims the card, and only the Coworker's worker is started.
