### Card context in a start

- A runner lease carries `card_context`: the started card as the machine's worker connection may read it. The runner appends it to the launch message, so an agent starts from the card itself and not only from its number.
- Fields, title and body are always present and verbatim. Asks, decisions and handoff notes, comments, blockers and related cards, linked evidence and linked memory text follow in that order while the block stays inside `COMMONPLACE_START_CONTEXT_TOKENS` (default 6000; 0 sends none).
- Nothing is shortened or summarised: an item is kept or cut whole, and a cut section ends with a marker that counts the cut items and names the call that returns them. User text is JSON-quoted.
- A card that cannot be read at lease time never fails the lease. `commonplace/runner/start_context.py` holds the assembly in one function with an ordered section list for later additions. A started card's own goal is never cut, like its title; the goals above it are a section after decisions and are cut from the top level down.
